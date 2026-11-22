"""Launch an isolated Chromium instance for a NOVA profile.

The launcher owns three responsibilities:

1. **Isolation** -- every profile gets its own ``user_data_dir`` so cookies,
   storage and caches never leak between profiles (see
   :mod:`novaprofile.launcher.isolation`).
2. **Command line** -- the profile config is translated into Chromium
   switches (proxy, language, window size, user agent, WebRTC/DNS behaviour).
3. **Process lifecycle** -- the browser is started, logged, tracked and can be
   terminated gracefully or killed, including the orphaned renderer/GPU
   children.

Network-stack constraint
------------------------
The TLS/JA3 and HTTP/2 fingerprints of the profile are produced by the
**native Chromium network stack**.  NOVA deliberately does **not** fake them:
no custom TLS stack, no TLS-fragmenting proxy and no HTTP/2 header reordering
proxy is inserted into the path.  Any such shim would change the ClientHello
and the SETTINGS frame in ways that no real Android device produces, which
makes the profile *more* detectable than an untouched browser.  All network
emulation therefore happens below the TLS layer (IP, DNS, WebRTC) and inside
the browser core, never in a MITM proxy.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.cores import CoreManager, CoreSelector
from ..core.versions import CoreFallbackPolicy, CoreSelection
from ..js_runtime.injector import (
    JS_RUNTIME_FILENAME,
    InjectionPoint,
    remove_runtime,
    write_runtime,
)
from .isolation import ProfileIsolation
from .leak_guard import (
    LeakGuardConfig,
    build_leak_guard_flags,
    merge_feature_lists,
)

#: Name of the encrypted config file handed to the browser core.
CORE_CONFIG_FILENAME: str = "nova_core.json"

#: The switch that points the browser at the JavaScript runtime.
#:
#: The native core reads the profile from ``--nova-config``.  The
#: JavaScript layer is the fallback for a stock Chromium: the script is
#: written into the user data directory and the path is passed here, so
#: the payload never appears in the process arguments -- those are
#: readable by every process on the machine, the user data directory is
#: not.
JS_RUNTIME_FLAG: str = "--nova-js-runtime"

#: Default Chromium locations on Windows / POSIX.
DEFAULT_CHROME_PATHS: tuple[str, ...] = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

#: Chromium features that are disabled for every profile to keep the surface
#: small and deterministic.
BASE_DISABLE_FEATURES: tuple[str, ...] = (
    "Translate",
    "OptimizationHints",
    "MediaRouter",
    "DialMediaRouteProvider",
    "InterestFeedContentSuggestions",
    "CalculateNativeWinOcclusion",
    "BackForwardCache",
)


def _getattr_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    return current


def _webrtc_ip_policy(
    mode: str,
    *,
    block_non_proxied_udp: bool,
    ice_transport_policy: str,
) -> str:
    """Resolve the single ``--force-webrtc-ip-handling-policy`` value.

    The WebRTC mode is the primary signal; ``block_non_proxied_udp`` and the
    ICE transport policy refine it.  The *strictest* applicable policy wins so
    that the emitted switch never leaks the real local address.
    """

    if mode == "disabled":
        return "disable_non_proxied_udp"
    if mode == "real":
        return "default"
    if mode in {"altered", "default_public_interface_only"}:
        return "default_public_interface_only"
    # A profile without a proxy has to behave like an ordinary handset:
    # the candidates must be reachable, otherwise the absence of a usable
    # RTCPeerConnection is a louder bot signal than any address leak.
    if mode == "direct":
        return "default"
    # "proxy_only" and anything unknown fall back to the proxy-only policy.
    if ice_transport_policy == "relay":
        return "relay"
    if block_non_proxied_udp:
        return "proxy_only"
    return "default"


@dataclass
class LaunchedBrowser:
    """A running browser instance."""

    pid: int
    process: subprocess.Popen[bytes] | None
    user_data_dir: Path
    config_path: Path
    started_at: datetime
    profile_id: str = ""
    session_id: str = ""
    log_path: Path | None = None
    command: list[str] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        """Whether the browser process is still alive."""

        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def uptime_seconds(self) -> float:
        """Seconds elapsed since the browser was started."""

        return (datetime.now(UTC) - self.started_at).total_seconds()


class BrowserLauncher:
    """Build the Chromium command line and manage the browser process."""

    def __init__(
        self,
        chrome_path: str | None = None,
        *,
        isolation: ProfileIsolation | None = None,
        headless: bool = False,
        extra_args: Sequence[str] = (),
        leak_guard: LeakGuardConfig | None = None,
        core_manager: CoreManager | None = None,
        core_policy: CoreFallbackPolicy = CoreFallbackPolicy.NEAREST,
        js_runtime: bool = True,
    ) -> None:
        self._chrome_path = chrome_path
        self._isolation = isolation or ProfileIsolation()
        self._headless = headless
        self._extra_args = list(extra_args)
        self._leak_guard = leak_guard or LeakGuardConfig()
        self._core_manager = core_manager
        self._core_policy = core_policy
        # The JavaScript layer is on by default: it is the fallback that
        # makes a stock Chromium carry the fingerprint at all.  It stands
        # down by itself when the native core is present.
        self._js_runtime_enabled = js_runtime
        self._launched: dict[str, LaunchedBrowser] = {}

    # -- core selection ----------------------------------------------------

    @property
    def core_manager(self) -> CoreManager | None:
        """The manager that owns the installed cores, if any."""

        return self._core_manager

    def select_core(self, config: Any) -> CoreSelection | None:
        """Resolve the browser core that will serve ``config``.

        The core is chosen by the profile's declared major version.  When a
        fallback picks a different core, the profile's declared version is
        adjusted so that the User-Agent, the client hints and the running
        core still agree -- a profile that claims Chromium 124 while
        Chromium 120 runs is a contradiction a site can read.

        Returns ``None`` when no core manager is configured, in which case
        the launcher's own executable is used.
        """

        if self._core_manager is None:
            return None

        version = getattr(config, "browser_version", None)
        if version is None:
            return None

        selector = CoreSelector(self._core_manager, policy=self._core_policy)
        return selector.select(version.major)

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def find_chrome(explicit: str | None = None) -> str:
        """Locate a Chrome/Chromium executable.

        Raises :class:`FileNotFoundError` when nothing suitable is installed.
        """

        import shutil

        if explicit:
            candidate = Path(explicit)
            if candidate.exists():
                return str(candidate)
            found = shutil.which(explicit)
            if found:
                return found
            raise FileNotFoundError(f"chrome executable not found: {explicit}")
        for path in DEFAULT_CHROME_PATHS:
            if Path(path).exists():
                return path
        for name in ("chrome", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                return found
        raise FileNotFoundError("no Chrome/Chromium executable found; set chrome_path explicitly")

    @property
    def chrome_path(self) -> str:
        """The resolved Chrome executable path."""

        return self.find_chrome(self._chrome_path)

    # -- command line ------------------------------------------------------

    def build_args(self, config: Any, user_data_dir: Path) -> list[str]:
        """Build the full Chromium command line for ``config``.

        The returned list starts with the executable path.
        """

        # A multi-core installation serves the profile with the core that
        # matches its declared major version.  The executable of that core
        # replaces the default one.
        #
        # The core is selected *before* the default executable is
        # resolved.  Resolving it the other way round makes the launcher
        # fail on a machine that has no system-wide Chrome installed --
        # even though the core it was about to use carries its own
        # executable, which is the whole point of a multi-core
        # installation.
        executable: str | None = None
        selection = self.select_core(config)
        if selection is not None:
            core_executable = self._executable_of(selection)
            if core_executable:
                executable = core_executable
        if executable is None:
            executable = self.chrome_path

        args: list[str] = [executable]

        if self._headless:
            args.append("--headless=new")

        # -- isolation ----------------------------------------------------
        args.append(f"--user-data-dir={user_data_dir}")
        args.append("--no-first-run")
        args.append("--no-default-browser-check")
        args.append("--disable-background-networking")
        args.append("--disable-backgrounding-occluded-windows")
        args.append("--disable-renderer-backgrounding")
        args.append("--disable-background-timer-throttling")
        args.append("--disable-popup-blocking")
        args.append("--disable-sync")
        args.append("--disable-translate")
        args.append("--no-service-autorun")
        args.append("--password-store=basic")
        args.append("--use-mock-keychain")

        # -- identity ------------------------------------------------------
        user_agent = _getattr_path(config, "navigator.user_agent")
        if user_agent:
            args.append(f"--user-agent={user_agent}")

        language = _getattr_path(config, "navigator.language")
        if language:
            args.append(f"--lang={language}")
        accept_language = _getattr_path(config, "http_headers.accept_language")
        if accept_language:
            args.append(f"--accept-lang={accept_language}")

        client_hints = _getattr_path(config, "http_headers.sec_ch_ua")
        if client_hints:
            args.append(f"--client-hints={client_hints}")

        # -- window geometry ------------------------------------------------
        width = _getattr_path(config, "screen.width")
        height = _getattr_path(config, "screen.height")
        if width and height:
            args.append(f"--window-size={width},{height}")
        if _getattr_path(config, "viewport.outer_width") and _getattr_path(
            config, "viewport.outer_height"
        ):
            args.append("--window-position=0,0")
        device_scale_factor = _getattr_path(config, "screen.device_pixel_ratio")
        if device_scale_factor:
            args.append(f"--force-device-scale-factor={device_scale_factor}")

        # -- proxy ----------------------------------------------------------
        proxy_enabled = bool(_getattr_path(config, "proxy.enabled"))
        proxy_protocol = str(_getattr_path(config, "proxy.protocol") or "direct")
        proxy_host = str(_getattr_path(config, "proxy.host") or "")
        proxy_port = int(_getattr_path(config, "proxy.port") or 0)
        if proxy_enabled and proxy_host and proxy_port and proxy_protocol != "direct":
            scheme = "http" if proxy_protocol in {"http", "https"} else proxy_protocol
            args.append(f"--proxy-server={scheme}://{proxy_host}:{proxy_port}")
            if _getattr_path(config, "proxy.auth_method") == "userpass":
                username = str(_getattr_path(config, "proxy.username") or "")
                password = str(_getattr_path(config, "proxy.password") or "")
                if username:
                    args.append("--proxy-bypass-list=<-loopback>")
                    args.append(f"--proxy-server-credentials={username}:{password}")

        # -- DNS ------------------------------------------------------------
        dns_mode = str(_getattr_path(config, "dns.mode") or "system")
        dns_servers = _getattr_path(config, "dns.servers") or []
        prevent_dns_leak = bool(_getattr_path(config, "dns.prevent_dns_leak"))
        host_resolver_rules = self._host_resolver_rules(dns_mode, dns_servers, prevent_dns_leak)
        if host_resolver_rules:
            args.append(f"--host-resolver-rules={host_resolver_rules}")

        # -- WebRTC ---------------------------------------------------------
        # Exactly ONE --force-webrtc-ip-handling-policy may be emitted: Chrome
        # honours the last occurrence, so emitting several would silently
        # override the configured mode.
        webrtc_mode = str(_getattr_path(config, "webrtc.mode") or "default_public_interface_only")
        disable_features = list(BASE_DISABLE_FEATURES)
        ip_policy = _webrtc_ip_policy(
            webrtc_mode,
            block_non_proxied_udp=bool(_getattr_path(config, "webrtc.block_non_proxied_udp")),
            ice_transport_policy=str(_getattr_path(config, "webrtc.ice_transport_policy") or "all"),
        )
        args.append(f"--force-webrtc-ip-handling-policy={ip_policy}")
        if (
            not bool(_getattr_path(config, "webrtc.mdns_enabled"))
            and "WebRtcHideLocalIpsWithMdns" not in disable_features
        ):
            disable_features.append("WebRtcHideLocalIpsWithMdns")

        # -- leak containment ------------------------------------------------
        # The host's GPU, its IP and its platform must not reach the page.
        guard = self._leak_guard
        args.extend(build_leak_guard_flags(guard))
        disable_features, enable_features = merge_feature_lists(disable_features, [], guard)
        if enable_features:
            args.append(f"--enable-features={','.join(enable_features)}")

        args.append(f"--disable-features={','.join(disable_features)}")

        # -- geo / locale ----------------------------------------------------
        timezone = _getattr_path(config, "timezone.iana_name")
        if timezone:
            # Chromium reads the timezone from the OS; NOVA sets it through
            # the browser core, but the flag keeps the two in agreement.
            args.append(f"--nova-timezone={timezone}")
        locale = _getattr_path(config, "locale.primary")
        if locale:
            args.append(f"--nova-locale={locale}")

        # -- browser core config --------------------------------------------
        config_path = user_data_dir / CORE_CONFIG_FILENAME
        args.append(f"--nova-config={config_path}")

        # -- the JavaScript fallback layer ------------------------------------
        # When the native core is present it does the work and the
        # JavaScript layer stands down (it checks for the native marker).
        # When it is absent -- a stock Chromium, or a build of the patch
        # series that has not been compiled yet -- the script is what
        # carries the fingerprint into the page.
        if self._js_runtime_enabled:
            runtime_path = user_data_dir / JS_RUNTIME_FILENAME
            if runtime_path.exists():
                args.append(f"{JS_RUNTIME_FLAG}={runtime_path}")

        # -- user supplied extras --------------------------------------------
        args.extend(self._extra_args)

        return args

    @staticmethod
    def _executable_of(selection: CoreSelection) -> str:
        """The path of the executable of a selected core.

        The manifest names the executable relative to the core's directory.
        An empty or missing executable means the core cannot be launched,
        and the caller falls back to the launcher's own binary.
        """

        manifest = selection.core
        if not manifest.executable:
            return ""
        candidate = Path(manifest.install_path) / manifest.executable
        if candidate.is_file():
            return str(candidate)
        # The manifest may already carry an absolute path.
        absolute = Path(manifest.executable)
        return str(absolute) if absolute.is_file() else ""

    @staticmethod
    def _host_resolver_rules(
        dns_mode: str, dns_servers: Sequence[str], prevent_dns_leak: bool
    ) -> str:
        """Build the ``--host-resolver-rules`` value for the DNS mode."""

        if not dns_servers:
            return ""
        rules: list[str] = []
        if dns_mode in {"proxy_remote", "custom_udp", "doh", "dot"}:
            # Resolve everything through the proxy so that the DNS server of
            # the exit network is used -- never the local one.
            rules.append("MAP * ~NOTFOUND")
        else:
            return ""
        if prevent_dns_leak:
            rules.append("EXCLUDE localhost")
        return ", ".join(rules)

    # -- core config -------------------------------------------------------

    def write_core_config(self, config: Any, user_data_dir: Path) -> Path:
        """Write the profile config next to the browser state.

        The file is the hand-over point to the C++ browser core: the core
        reads ``nova_core.json`` from the ``user_data_dir`` and applies the
        fingerprint overrides before the first page is painted.  The control
        plane writes it encrypted (see :mod:`novaprofile.security.crypto`);
        when the encryption helper is unavailable the payload is written as
        plain JSON with an explicit ``"encrypted": false`` marker so the core
        can refuse to start rather than silently running an unprotected
        profile.
        """

        user_data_dir.mkdir(parents=True, exist_ok=True)
        target = user_data_dir / CORE_CONFIG_FILENAME
        payload = self._config_payload(config)
        try:
            from novaprofile.security.crypto import encrypt_secret

            ciphertext = encrypt_secret(json.dumps(payload, separators=(",", ":")))
            document: dict[str, Any] = {
                "schema": "nova.core/1",
                "encrypted": True,
                "payload": ciphertext,
            }
        except Exception:
            document = {
                "schema": "nova.core/1",
                "encrypted": False,
                "payload": payload,
            }
        target.write_text(
            json.dumps(document, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return target

    @staticmethod
    def _config_payload(config: Any) -> dict[str, Any]:
        """Serialise the profile config into a JSON-compatible dictionary.

        The document is the one the browser core reads, so it is put
        through the boundary: a number that does not fit in 32 bits and
        a null where a string is expected would otherwise make the
        core refuse the whole profile.
        """

        from .core_document import build_core_document

        return build_core_document(config)

    # -- the JavaScript fallback layer ---------------------------------------

    def _write_js_runtime(self, config: Any, user_data_dir: Path) -> Path | None:
        """Write the JavaScript runtime for ``config`` into ``user_data_dir``.

        The runtime is the fallback layer: it carries the fingerprint into
        the page when the native core is not there.  It is written next to
        the core config and removed again when the browser exits -- a
        fingerprint left on disk in the clear is a liability, because the
        next profile in the same directory would be able to read it.

        A failure here is not fatal.  A profile without the JavaScript
        layer still runs, it just reports the host's real values, and the
        probe would catch that.  Failing the launch instead would mean a
        missing optional dependency takes the whole product down.
        """

        dump = getattr(config, "model_dump", None)
        if not callable(dump):
            return None
        try:
            from ..schema.profile import ProfileConfig

            profile = ProfileConfig.model_validate(config.model_dump(mode="python"))
            written = write_runtime(
                profile,
                user_data_dir,
                injection_point=InjectionPoint.DOCUMENT_START,
            )
            return written.path
        except Exception:
            # The runtime is an enhancement.  The launch proceeds without
            # it, and the reason is recorded rather than raised.
            return None

    # -- launching ---------------------------------------------------------

    def launch(self, config: Any, *, extra_args: list[str] | None = None) -> LaunchedBrowser:
        """Start the browser for ``config`` and return the handle."""

        profile_id = str(_getattr_path(config, "identity.id") or "")
        if not profile_id:
            raise ValueError("config has no identity.id; cannot launch")

        user_data_dir = self._isolation.create(profile_id)
        # A previous crash may have left the singleton locks behind.
        ProfileIsolation.cleanup_lock_files(user_data_dir)

        config_path = self.write_core_config(config, user_data_dir)

        # The JavaScript fallback layer is written next to the core
        # config.  It is written *before* the command line is built,
        # because the command line refers to it by path.
        if self._js_runtime_enabled:
            self._write_js_runtime(config, user_data_dir)

        args = self.build_args(config, user_data_dir)
        if extra_args:
            args.extend(extra_args)

        session_id = uuid.uuid4().hex
        log_dir = self._isolation.ensure_logs(profile_id)
        log_path = log_dir / f"{session_id}.log"

        started_at = datetime.now(UTC)
        stdout_handle = log_path.open("ab")
        stderr_handle = log_path.open("ab")
        try:
            process = subprocess.Popen(  # noqa: S603 - explicit local binary
                args,
                stdout=stdout_handle,
                stderr=stderr_handle,
                stdin=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except OSError:
            stdout_handle.close()
            stderr_handle.close()
            raise
        finally:
            with contextlib.suppress(Exception):
                stdout_handle.close()
            with contextlib.suppress(Exception):
                stderr_handle.close()

        launched = LaunchedBrowser(
            pid=process.pid,
            process=process,
            user_data_dir=user_data_dir,
            config_path=config_path,
            started_at=started_at,
            profile_id=profile_id,
            session_id=session_id,
            log_path=log_path,
            command=list(args),
        )
        self._launched[profile_id] = launched
        return launched

    # -- termination -------------------------------------------------------

    def terminate(self, profile_id: str, *, graceful: bool = True) -> bool:
        """Stop the browser of ``profile_id``.

        A graceful stop asks the child processes to exit and then the parent
        process as well; an ungraceful stop kills the whole process tree.
        """

        launched = self._launched.get(profile_id)
        if launched is None:
            return False
        process = launched.process
        if process is None:
            return False
        if process.poll() is not None:
            self._launched.pop(profile_id, None)
            return False

        if graceful:
            self._terminate_children(launched)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover - stubborn
                    process.kill()
        else:
            self.kill_orphans(profile_id)
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                process.wait(timeout=5)

        with contextlib.suppress(Exception):
            ProfileIsolation.cleanup_lock_files(launched.user_data_dir)
        # The JavaScript payload is removed with the browser.  It carries
        # the fingerprint in the clear, and the next profile in the same
        # directory must not be able to read it.
        with contextlib.suppress(Exception):
            remove_runtime(launched.user_data_dir)
        self._launched.pop(profile_id, None)
        return True

    @staticmethod
    def _terminate_children(launched: LaunchedBrowser) -> None:
        """Ask the renderer/GPU children to exit politely."""

        try:
            import psutil

            proc = psutil.Process(launched.pid)
            for child in proc.children(recursive=True):
                with contextlib.suppress(Exception):
                    child.terminate()
        except Exception:  # pragma: no cover - psutil optional at runtime
            pass

    def kill_orphans(self, profile_id: str) -> list[int]:
        """Kill the renderer/GPU child processes of ``profile_id``.

        Returns the list of PIDs that were terminated.
        """

        launched = self._launched.get(profile_id)
        if launched is None:
            return []
        killed: list[int] = []
        try:
            import psutil

            proc = psutil.Process(launched.pid)
            for child in proc.children(recursive=True):
                with contextlib.suppress(Exception):
                    child.kill()
                    killed.append(child.pid)
        except Exception:  # pragma: no cover - psutil optional at runtime
            pass
        return killed

    # -- introspection -----------------------------------------------------

    @property
    def launched(self) -> dict[str, LaunchedBrowser]:
        """The browsers this launcher started, keyed by profile id."""

        return dict(self._launched)

    def is_running(self, profile_id: str) -> bool:
        """Whether the browser of ``profile_id`` is still alive."""

        launched = self._launched.get(profile_id)
        return launched is not None and launched.is_running

    @staticmethod
    def format_command(args: Sequence[str]) -> str:
        """Render a command line for logging (POSIX quoting)."""

        return " ".join(shlex.quote(str(arg)) for arg in args)


def _creation_flags() -> int:
    """Return the ``creationflags`` for a detached child process."""

    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        return 0x00000008 | 0x00000200
    return 0


def default_launcher(**kwargs: Any) -> BrowserLauncher:
    """Build a launcher with the default isolation layout."""

    return BrowserLauncher(**kwargs)


def chrome_version(chrome_path: str | None = None) -> str:
    """Return the version string of the installed Chrome (``""`` on failure)."""

    path = BrowserLauncher.find_chrome(chrome_path)
    try:
        completed = subprocess.run(  # noqa: S603 - explicit local binary
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment
        return ""
    return completed.stdout.strip() or completed.stderr.strip()


__all__ = [
    "BASE_DISABLE_FEATURES",
    "CORE_CONFIG_FILENAME",
    "DEFAULT_CHROME_PATHS",
    "BrowserLauncher",
    "LaunchedBrowser",
    "chrome_version",
    "default_launcher",
]
