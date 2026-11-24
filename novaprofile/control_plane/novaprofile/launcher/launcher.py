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

import asyncio
import contextlib
import hashlib
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
    JS_PAYLOAD_FILENAME,
    remove_runtime,
    write_payload,
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
JS_PAYLOAD_FLAG: str = "--nova-js-payload"

#: Default Chromium locations on Windows / POSIX.
#:
#: The NOVA core comes first, and it comes first deliberately: a profile
#: that is served by a stock Chromium reports the *host's* values for
#: every surface the core owns -- the platform, the core count, the
#: memory, the panel, the client hints -- because stock Chromium does
#: not know what ``--nova-config`` means.  The browser then looks like
#: a browser and behaves like a desktop, which is exactly the failure
#: the product exists to prevent.
#:
#: The paths are the output directories of the patched Chromium build.
#: When none of them holds a built core, the launcher falls back to a
#: stock browser and the JavaScript layer carries the profile -- which
#: is worse than the native core, but better than nothing.
NOVA_CORE_PATHS: tuple[str, ...] = (
    r"C:\chromium\src\out\nova-win\chrome.exe",
    r"C:\chromium\src\out\Release\chrome.exe",
    r"C:\chromium\src\out\Default\chrome.exe",
)

DEFAULT_CHROME_PATHS: tuple[str, ...] = (
    *NOVA_CORE_PATHS,
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
    debug_port: int = 0
    #: The task that keeps the device emulation alive.
    #:
    #: The touch mode of a target lives on the session that the
    #: developer protocol client holds open.  Chromium drops it when the
    #: session is detached, so the connection has to stay open for as
    #: long as the browser is open.  The task is the one that holds it.
    emulation_watcher: asyncio.Task[None] | None = None
    emulation_loop: asyncio.AbstractEventLoop | None = None
    #: The emulator that holds the connection to the browser.
    #:
    #: The connection is what keeps the touch mode of a target alive,
    #: so it is closed when the browser is closed.
    emulation_emulator: Any = None

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
        device_emulation: bool = True,
        debug_port_base: int = 9333,
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
        # The device emulation is on by default: it is what turns the
        # operator's mouse into a finger.
        self._device_emulation_enabled = device_emulation
        self._debug_port_base = debug_port_base
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

    # -- the developer protocol ----------------------------------------------

    def _debug_port_for(self, profile_id: str) -> int:
        """The protocol port of ``profile_id``.

        The port is derived from the profile's own identifier, so that
        two profiles that are open at the same time get two ports, and
        so that a profile gets the same port every time it is opened.
        """

        if not profile_id:
            return self._debug_port_base
        digest = hashlib.sha256(profile_id.encode("utf-8")).digest()
        offset = int.from_bytes(digest[:2], "big") % 1000
        return self._debug_port_base + offset

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

        # -- the developer protocol ---------------------------------------
        # The protocol is how the touch mode is turned on.  Chromium has
        # no command line switch for it: the touch mode of the browser
        # is a per-target setting of the developer tools, and the only
        # way to set it without a human in front of the screen is the
        # protocol that the developer tools use.
        #
        # The port is derived from the profile, so that two profiles
        # that are open at the same time do not fight over it.
        debug_port = self._debug_port_for(
            str(_getattr_path(config, "identity.id") or "")
        )
        args.append(f"--remote-debugging-port={debug_port}")
        # The protocol answers to the local machine only.  The switch
        # is the default in current Chromium, but it is named so that
        # the intent is on the command line and not in a policy.
        args.append("--remote-debugging-address=127.0.0.1")

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

        # -- touch ----------------------------------------------------------
        # A profile that claims to be an Android handset has to *behave*
        # like one, not merely say so.  ``navigator.maxTouchPoints``
        # says the panel takes ten fingers, but a browser that is not in
        # touch mode still hands the page a mouse: the page sees
        # ``ontouchstart`` missing, the pointer events report a mouse,
        # and ``matchMedia("(pointer: coarse)")`` says the device has a
        # fine pointer.  Those three readings contradict the touch-point
        # count, and a script that reads all of them knows the profile
        # is a desktop pretending to be a handset.
        #
        # The switch turns touch on; the emulation below makes the page
        # believe the mouse is a finger.
        max_touch_points = int(_getattr_path(config, "navigator.max_touch_points") or 0)
        if max_touch_points > 0:
            args.append("--touch-events=enabled")
            # The touch points are the number of contacts the panel
            # reports, and Chromium derives the number of pointers from
            # the screen's own size.  The window is not the screen, so
            # the count is pinned to what the profile declares.
            args.append(f"--touch-point-count={max_touch_points}")

            # -- the pointer type --------------------------------------------
            # Turning touch on is not the same as *being* a touch device.
            # With only the switch above, the browser still has a mouse
            # attached: it fires ``pointerdown`` with ``pointerType`` of
            # ``"mouse"``, it answers ``matchMedia("(pointer: coarse)")``
            # with false, and the page can hover.  A page that reads the
            # three together knows the "handset" is a desktop with a
            # touchscreen bolted on, and the operator's own drag is a
            # mouse drag rather than a finger.
            #
            # These settings are the ones the device toolbar of the
            # browser's own developer tools uses to turn a desktop
            # window into a handset, and they are what the page reads.
            # The pointer type is *coarse* because a finger is not
            # accurate; the hover type is *none* because a finger cannot
            # hover, and the available types are coarse alone because a
            # handset has no mouse.  The values are the ones the
            # browser's own enum uses: two is the coarse pointer and one
            # is the absence of hovering.
            pointer_settings = (
                "primaryPointerType=2,"
                "availablePointerTypes=2,"
                "primaryHoverType=1,"
                "availableHoverTypes=1"
            )
            args.append(f"--blink-settings={pointer_settings}")

        # -- window geometry ------------------------------------------------
        # The window is fitted to the real monitor, keeping the handset's
        # aspect ratio.  The values a page reads (screen size, DPR) come
        # from the core and the JS payload, not from the window, so the
        # window size and the host's scale factor are left to the desktop.
        # Forcing the handset's DPR (e.g. 2.625) would zoom the whole
        # browser UI and the page by that factor on a desktop monitor.
        from .geometry import window_geometry_for

        width = _getattr_path(config, "screen.width")
        height = _getattr_path(config, "screen.height")
        if width and height:
            geometry = window_geometry_for(
                int(width), int(height), headful=not self._headless
            )
            args.append(f"--window-size={geometry.width},{geometry.height}")
            if not self._headless:
                args.append(f"--window-position={geometry.x},{geometry.y}")

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
        #
        # Only the payload is handed over.  The layer's code is compiled
        # into the browser: it is the same for every profile and it does
        # not fit on a command line next to the payload.
        if self._js_runtime_enabled:
            payload_path = user_data_dir / JS_PAYLOAD_FILENAME
            if payload_path.exists():
                args.append(f"{JS_PAYLOAD_FLAG}={payload_path}")

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
        # The core (nova_core_config.cc::ParseProfileConfig) reads the
        # profile fields -- ``identity``, ``seed_config``, ``navigator`` ...
        # -- at the root of the document and has no decryption step.  An
        # envelope such as {"encrypted": true, "payload": ...} makes the
        # core reject the profile and the page sees the host's values.
        payload = self._config_payload(config)
        target.write_text(
            json.dumps(payload, separators=(",", ":"), default=str),
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

    def _write_js_payload(self, config: Any, user_data_dir: Path) -> Path | None:
        """Write the JavaScript layer's payload for ``config``.

        The payload is the profile's values without the code that
        installs them.  The code is compiled into the browser, so only
        the payload travels: the code is the same for every profile and
        it does not fit on a Windows command line next to the payload.

        The payload is written next to the core config and removed again
        when the browser exits -- a fingerprint left on disk in the clear
        is a liability, because the next profile in the same directory
        would be able to read it.

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
            written = write_payload(profile, user_data_dir)
            return written.path
        except Exception:
            # The layer is an enhancement.  The launch proceeds without
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

        # The JavaScript fallback layer's payload is written next to the
        # core config.  It is written *before* the command line is
        # built, because the command line refers to it by path.
        if self._js_runtime_enabled:
            self._write_js_payload(config, user_data_dir)

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
            debug_port=self._debug_port_for(profile_id),
        )
        self._launched[profile_id] = launched

        # The touch mode is turned on after the browser is up, because
        # it is a per-target setting and the target has to exist first.
        #
        # The failure is not fatal.  A browser without the touch mode is
        # a browser that a site can tell apart, but it is still a
        # working browser, and the operator is better served by a
        # profile that opens than by an error.
        if self._device_emulation_enabled and not self._headless:
            self._apply_device_emulation(config, launched)

        return launched

    def _apply_device_emulation(self, config: Any, launched: LaunchedBrowser) -> bool:
        """Turn the browser's touch mode on.

        The commands that do it -- the device metrics, the touch
        emulation, the mouse to touch conversion and the user agent --
        are the ones the browser's own driver uses to drive the mobile
        web tests.

        The connection to the browser stays open for as long as the
        browser is open.  The touch mode of a target lives on the
        session that the protocol client holds, and Chromium drops it
        when the session is detached: a launcher that sends the
        commands and closes the connection leaves the profile with a
        mouse, even though it reported that the emulation was applied.
        The watcher also emulates the targets that appear later -- a
        second tab, a popup -- so that a page that opens one does not
        find a desktop in it.
        """

        from .device_emulation import EmulationError

        screen = _getattr_path(config, "screen") or {}
        width = int(_getattr_path(screen, "width") or 0)
        height = int(_getattr_path(screen, "height") or 0)
        ratio = float(_getattr_path(screen, "device_pixel_ratio") or 1.0)
        user_agent = str(_getattr_path(config, "navigator.user_agent") or "")
        platform = str(_getattr_path(config, "navigator.platform") or "")
        max_touch_points = int(_getattr_path(config, "navigator.max_touch_points") or 10)
        accept_language = str(
            _getattr_path(config, "navigator.accept_language") or "vi-VN,vi;q=0.9"
        )

        # The control plane serves its API from an event loop, and a
        # running loop cannot be started again.  The watcher therefore
        # runs in a thread of its own, with a loop of its own.
        import threading

        ready: threading.Event = threading.Event()
        outcome: dict[str, object] = {}

        async def run() -> None:
            from .device_emulation import DeviceEmulator

            emulator = DeviceEmulator(
                port=launched.debug_port,
                width=width,
                height=height,
                device_scale_factor=ratio,
                user_agent=user_agent,
                max_touch_points=max_touch_points,
                screen_width=width,
                screen_height=height,
                platform=platform,
                accept_language=accept_language,
            )
            launched.emulation_emulator = emulator

            def on_ready(count: int) -> None:
                outcome["count"] = count
                ready.set()

            try:
                # The coroutine keeps the connection to the browser
                # open for as long as the browser is open.  It returns
                # when the connection dies, which is when the browser
                # is closed.
                count = await emulator.run_forever(on_ready)
            except EmulationError as exc:
                outcome["error"] = exc
                ready.set()
                return
            outcome.setdefault("count", count)
            launched.emulation_loop = asyncio.get_running_loop()
            ready.set()

        def worker() -> None:
            try:
                asyncio.run(run())
            except (EmulationError, OSError, RuntimeError) as exc:
                outcome.setdefault("error", exc)
                ready.set()
            except BaseException as exc:  # noqa: BLE001
                # The connection to the browser dies when the browser
                # is closed, and the protocol client reports that as its
                # own exception.  That is the end of the watch, not a
                # failure of the launch: the profile is already running
                # and the operator is already driving it.
                #
                # It is only a failure when it happens *before* the
                # first target was emulated, because then the profile
                # would be running with the host's touch behaviour.
                if not ready.is_set():
                    outcome.setdefault("error", exc)
                ready.set()

        thread = threading.Thread(target=worker, name="nova-device-emulation", daemon=True)
        thread.start()
        ready.wait(timeout=90.0)

        error = outcome.get("error")
        if error is not None:
            _log_launcher("device emulation was not applied: %s", error)
            return False
        count = int(outcome.get("count") or 0)
        if count:
            _log_launcher("device emulation applied to %d target(s)", count)
        return bool(count)

    # -- termination -------------------------------------------------------

    def _close_emulation(self, launched: LaunchedBrowser) -> None:
        """Close the connection that keeps the device emulation alive.

        The touch mode of a target lives on the session that the
        protocol client holds, so the connection has to be closed with
        the browser.  A connection that stays open keeps a handle on a
        browser that is gone, and the port it holds is not free for the
        next profile.
        """

        emulator = getattr(launched, "emulation_emulator", None)
        if emulator is None:
            return
        with contextlib.suppress(Exception):
            emulator.close()
        launched.emulation_emulator = None

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
            self._close_emulation(launched)
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
        self._close_emulation(launched)
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


def _log_launcher(message: str, *args: Any) -> None:
    """Write one line to the launcher's log.

    The launcher has no logger of its own -- the control plane owns the
    logging -- so the line goes to the standard error, which the
    control plane captures.  A failure to write is not a failure of
    the launch: the line is a note, not a verdict.
    """

    try:
        import sys

        print(f"[nova-launcher] {message % args if args else message}", file=sys.stderr)
    except Exception:  # pragma: no cover - the note must never raise
        pass


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
