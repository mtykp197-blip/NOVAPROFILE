"""Runtime probe for the NOVA profile engine.

The probe launches an isolated Chrome instance, loads the static harness page
(:mod:`novaprofile.probe.harness`) and evaluates every fingerprint axis inside
the page.  The gathered measurements are then compared against the expected
values derived from the :class:`~novaprofile.schema.profile.ProfileConfig`.

Two layers are kept strictly apart:

* the **browser interaction** is injected through ``session_factory`` -- the
  default implementation talks CDP over a websocket, but any object exposing
  ``async def evaluate(expression: str) -> object`` can be substituted.  This
  keeps the whole probe testable without a real browser;
* the **comparison logic** is pure and lives in
  :func:`evaluate_axis_expectations` / :func:`compare_expectations`, so the
  pass/fail semantics can be unit-tested directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from .harness import HARNESS_HTML_PATH, build_harness_html

# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """A single axis measurement together with its verdict."""

    axis: str
    ok: bool
    value: str
    detail: str = ""


@dataclass
class ProbeReport:
    """The aggregate outcome of one probe run."""

    profile_id: str
    ok: bool
    results: list[ProbeResult]
    distinct_ratio: float
    collision_rate: float
    persistence_ok: bool
    consistency_pass: bool


class ProbeSession(Protocol):
    """The minimal contract a browser session must fulfil for the probe."""

    async def evaluate(self, expression: str) -> Any:  # pragma: no cover - protocol
        ...

    async def close(self) -> None:  # pragma: no cover - protocol
        ...


SessionFactory = Callable[[], Awaitable[ProbeSession]]

#: Environment variable that enables the opt-in live browser test.
LIVE_ENV_VAR: Final[str] = "NOVA_PROBE_LIVE"

#: Default Chrome locations on Windows / POSIX.
DEFAULT_CHROME_PATHS: Final[tuple[str, ...]] = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

#: JavaScript expression that triggers the harness and returns the raw payload.
HARNESS_RUN_EXPRESSION: Final[str] = (
    "window.__NOVA_PROBE__ && typeof window.__NOVA_PROBE__.run === 'function'"
    " ? window.__NOVA_PROBE__.run() : Promise.resolve({ error: 'harness-missing' })"
)


# --------------------------------------------------------------------------
# CDP client
# --------------------------------------------------------------------------


class CdpError(RuntimeError):
    """Raised when the browser returns a CDP error payload."""


class CdpClient:
    """A tiny, dependency-light Chrome DevTools Protocol client.

    The client speaks the raw websocket protocol exposed by
    ``websockets.asyncio.client.connect``.  Commands are sent with an
    incrementing id and every response is matched against its id, so
    interleaved events (``Target.*``, ``Runtime.*``) never confuse the
    request/response correlation.
    """

    def __init__(self, ws_url: str, *, timeout: float = 30.0) -> None:
        self._ws_url = ws_url
        self._timeout = timeout
        self._connection: Any = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the websocket and start the background reader task."""

        from websockets.asyncio.client import connect  # local import: optional dep

        self._connection = await connect(
            self._ws_url,
            open_timeout=self._timeout,
            max_size=None,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        connection = self._connection
        if connection is None:  # pragma: no cover - defensive
            return
        try:
            async for raw in connection:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # pragma: no cover - connection teardown
            pass
        finally:
            self._fail_pending("connection-closed")

    def _dispatch(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if isinstance(message_id, int):
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)
            return
        # Events are ignored -- the probe only issues request/response pairs.

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(CdpError(reason))
        self._pending.clear()

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and await its response.

        ``session_id`` scopes the command to an attached target.  It is
        required for the page-level domains (``Page``, ``Runtime``) once the
        target has been attached with ``Target.attachToTarget``.
        """

        if self._closed:
            raise CdpError("client-closed")
        connection = self._connection
        if connection is None:
            raise CdpError("client-not-connected")

        message_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future

        payload: dict[str, Any] = {"id": message_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id is not None:
            payload["sessionId"] = session_id
        try:
            await connection.send(json.dumps(payload))
        except Exception:
            self._pending.pop(message_id, None)
            raise

        try:
            response = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise CdpError(f"timeout waiting for {method}") from exc

        if "error" in response:
            error = response["error"]
            message = (
                error.get("message", "unknown CDP error") if isinstance(error, dict) else str(error)
            )
            raise CdpError(f"{method}: {message}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        """Close the websocket and release every pending future."""

        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        connection = self._connection
        self._connection = None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()
        self._fail_pending("client-closed")

    # -- convenience -------------------------------------------------------

    async def evaluate(self, expression: str, *, session_id: str | None = None) -> Any:
        """Evaluate a JavaScript expression in the page and return its value."""

        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
            session_id=session_id,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text", "evaluation-error")
            description = details.get("exception", {}).get("description")
            raise CdpError(f"evaluate failed: {description or text}")
        remote = result.get("result")
        if not isinstance(remote, dict):
            return None
        if remote.get("type") == "undefined":
            return None
        return remote.get("value")


# --------------------------------------------------------------------------
# Real CDP session
# --------------------------------------------------------------------------


class CdpSession:
    """A :class:`ProbeSession` backed by a real Chrome instance over CDP."""

    def __init__(
        self,
        chrome_path: str,
        *,
        headless: bool = True,
        timeout: float = 30.0,
        user_data_dir: Path | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self._chrome_path = chrome_path
        self._headless = headless
        self._timeout = timeout
        self._extra_args = list(extra_args)
        self._user_data_dir = Path(user_data_dir) if user_data_dir else None
        self._owns_user_data_dir = self._user_data_dir is None
        self._process: subprocess.Popen[bytes] | None = None
        self._client: CdpClient | None = None
        self._target_id: str | None = None
        self._session_id: str | None = None

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def find_chrome(explicit: str | None = None) -> str:
        """Locate a Chrome/Chromium executable.

        Raises :class:`FileNotFoundError` when nothing suitable is installed.
        """

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

    def _build_command(self) -> list[str]:
        args = [self._chrome_path]
        if self._headless:
            args.append("--headless=new")
        args.append("--remote-debugging-port=0")
        if self._user_data_dir is not None:
            args.append(f"--user-data-dir={self._user_data_dir}")
        args.append("--no-first-run")
        args.append("--no-default-browser-check")
        args.append("--disable-background-networking")
        args.append("--disable-sync")
        args.append("--disable-extensions")
        args.append("--no-first-run")
        args.append("--disable-gpu")
        args.append("--use-gl=swiftshader")
        args.append("--hide-scrollbars")
        args.append("--mute-audio")
        args.append("--disable-dev-shm-usage")
        args.append("about:blank")
        args.extend(self._extra_args)
        return args

    @staticmethod
    def _read_devtools_port(user_data_dir: Path, timeout: float) -> int:
        """Read the DevTools port Chrome wrote into ``DevToolsActivePort``."""

        port_file = user_data_dir / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_file.exists():
                try:
                    content = port_file.read_text(encoding="utf-8").splitlines()
                except OSError:  # pragma: no cover - transient
                    content = []
                if content:
                    try:
                        return int(content[0].strip())
                    except ValueError:  # pragma: no cover - transient
                        pass
            time.sleep(0.05)
        raise TimeoutError("Chrome did not report a DevTools port in time")

    async def _http_json(self, url: str) -> Any:
        import urllib.request

        def _fetch() -> Any:
            with urllib.request.urlopen(url, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        return await asyncio.to_thread(_fetch)

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start Chrome, connect over CDP and open the harness page."""

        if self._user_data_dir is None:
            self._user_data_dir = Path(tempfile.mkdtemp(prefix="nova-probe-"))
            self._owns_user_data_dir = True
        self._user_data_dir.mkdir(parents=True, exist_ok=True)

        command = self._build_command()
        self._process = subprocess.Popen(  # noqa: S603 - explicit, local binary
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            port = await asyncio.to_thread(
                self._read_devtools_port, self._user_data_dir, self._timeout
            )
            version = await self._http_json(f"http://127.0.0.1:{port}/json/version")
            ws_url = version["webSocketDebuggerUrl"]
            client = CdpClient(ws_url, timeout=self._timeout)
            await client.connect()
            self._client = client
            await self._open_harness()
        except Exception:
            await self.close()
            raise

    async def _open_harness(self) -> None:
        client = self._client
        if client is None:  # pragma: no cover - defensive
            raise CdpError("client-not-connected")
        target = await client.send("Target.createTarget", {"url": "about:blank"})
        target_id = target.get("targetId")
        if not isinstance(target_id, str):
            raise CdpError("Target.createTarget did not return a targetId")
        self._target_id = target_id
        attached = await client.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        session_id = attached.get("sessionId")
        self._session_id = session_id if isinstance(session_id, str) else None

        url = HARNESS_HTML_PATH.resolve().as_uri()
        session = self._session_id
        await client.send("Page.enable", session_id=session)
        await client.send("Runtime.enable", session_id=session)
        await client.send("Page.navigate", {"url": url}, session_id=session)

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                probe = await client.evaluate(
                    "typeof window.__NOVA_PROBE__ === 'object' && window.__NOVA_PROBE__ !== null",
                    session_id=session,
                )
            except CdpError:
                probe = False
            if probe:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("harness page did not initialise in time")

    async def evaluate(self, expression: str) -> Any:
        """Evaluate an expression in the harness page."""

        client = self._client
        if client is None:
            raise CdpError("session-not-started")
        return await client.evaluate(expression, session_id=self._session_id)

    async def close(self) -> None:
        """Tear the browser down and clean the temporary profile."""

        if self._client is not None:
            await self._client.close()
            self._client = None
        process = self._process
        self._process = None
        if process is not None:
            self._terminate_tree(process)
        if self._owns_user_data_dir and self._user_data_dir is not None:
            shutil.rmtree(self._user_data_dir, ignore_errors=True)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
        """Terminate the browser *and* every child it spawned.

        Chromium spawns renderer, GPU, utility and crashpad processes.  Killing
        only the parent leaves those children behind, and they keep the
        ``user_data_dir`` locked -- the next probe run then fails with a
        "profile in use" error and the temporary directory leaks.
        """

        children: list[Any] = []
        try:
            import psutil

            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
        except Exception:  # pragma: no cover - psutil optional at runtime
            children = []

        with contextlib.suppress(Exception):
            process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)

        for child in children:
            with contextlib.suppress(Exception):
                child.terminate()
        for child in children:
            with contextlib.suppress(Exception):
                child.wait(timeout=5)
            with contextlib.suppress(Exception):
                if child.is_running():
                    child.kill()

        if process.poll() is None:  # pragma: no cover - stubborn process
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                process.wait(timeout=5)

    # -- context manager ---------------------------------------------------

    async def __aenter__(self) -> CdpSession:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


# --------------------------------------------------------------------------
# Pure expectation logic
# --------------------------------------------------------------------------


def _getattr_path(obj: Any, path: str) -> Any:
    """Resolve a dotted attribute path, returning ``None`` when missing."""

    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    return current


def _stringify(value: Any) -> str:
    """Render a measured/expected value as a stable string."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ",".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def evaluate_axis_expectations(config: Any) -> dict[str, tuple[str, str]]:
    """Map every probe axis to its ``(expected, actual_path)`` pair.

    The ``actual_path`` entry documents where the *measured* value comes from
    inside the harness payload, e.g. ``"navigator.userAgent"``.  The
    ``expected`` entry is the value the profile config demands; when the config
    does not describe the axis the expected value is the empty string, which
    :func:`compare_expectations` treats as "not asserted".
    """

    expectations: dict[str, tuple[str, str]] = {}

    def add(axis: str, expected: Any, actual_path: str) -> None:
        expectations[axis] = (_stringify(expected), actual_path)

    add("navigator", _getattr_path(config, "navigator.user_agent"), "navigator.userAgent")
    add("navigator_platform", _getattr_path(config, "navigator.platform"), "navigator.platform")
    add("navigator_vendor", _getattr_path(config, "navigator.vendor"), "navigator.vendor")
    add(
        "navigator_concurrency",
        _getattr_path(config, "navigator.hardware_concurrency"),
        "navigator.hardwareConcurrency",
    )
    add(
        "navigator_memory",
        _getattr_path(config, "navigator.device_memory"),
        "navigator.deviceMemory",
    )
    add(
        "navigator_touch",
        _getattr_path(config, "navigator.max_touch_points"),
        "navigator.maxTouchPoints",
    )
    add("navigator_language", _getattr_path(config, "navigator.language"), "navigator.language")
    add(
        "client_hints_model",
        _getattr_path(config, "user_agent_data.model"),
        "navigator.userAgentData.model",
    )
    add(
        "client_hints_platform",
        _getattr_path(config, "user_agent_data.platform"),
        "navigator.userAgentData.platform",
    )
    add(
        "client_hints_platform_version",
        _getattr_path(config, "user_agent_data.platform_version"),
        "navigator.userAgentData.platformVersion",
    )
    add(
        "client_hints_architecture",
        _getattr_path(config, "user_agent_data.architecture"),
        "navigator.userAgentData.architecture",
    )
    add(
        "client_hints_bitness",
        _getattr_path(config, "user_agent_data.bitness"),
        "navigator.userAgentData.bitness",
    )
    add("webdriver", False, "navigator.webdriver")
    add(
        "pdf_viewer",
        _getattr_path(config, "navigator.pdf_viewer_enabled"),
        "navigator.pdfViewerEnabled",
    )

    add("screen_width", _getattr_path(config, "screen.width"), "screen.width")
    add("screen_height", _getattr_path(config, "screen.height"), "screen.height")
    add(
        "screen_avail_width",
        _getattr_path(config, "screen.avail_width"),
        "screen.availWidth",
    )
    add(
        "screen_avail_height",
        _getattr_path(config, "screen.avail_height"),
        "screen.availHeight",
    )
    add(
        "screen_color_depth",
        _getattr_path(config, "screen.color_depth"),
        "screen.colorDepth",
    )
    add(
        "screen_pixel_depth",
        _getattr_path(config, "screen.pixel_depth"),
        "screen.pixelDepth",
    )
    add(
        "screen_device_pixel_ratio",
        _getattr_path(config, "screen.device_pixel_ratio"),
        "screen.devicePixelRatio",
    )
    add(
        "viewport_inner_width",
        _getattr_path(config, "viewport.inner_width"),
        "screen.innerWidth",
    )
    add(
        "viewport_inner_height",
        _getattr_path(config, "viewport.inner_height"),
        "screen.innerHeight",
    )
    add(
        "viewport_visual_scale",
        _getattr_path(config, "viewport.visual_viewport_scale"),
        "screen.visualViewport.scale",
    )
    add(
        "media_color_scheme",
        _getattr_path(config, "media_queries.prefers_color_scheme"),
        "screen.prefersColorScheme",
    )
    add(
        "media_pointer",
        _getattr_path(config, "media_queries.pointer"),
        "screen.pointer",
    )
    add(
        "media_hover",
        _getattr_path(config, "media_queries.hover"),
        "screen.hover",
    )
    add(
        "media_color_gamut",
        _getattr_path(config, "media_queries.color_gamut"),
        "screen.colorGamut",
    )
    add(
        "media_forced_colors",
        _getattr_path(config, "media_queries.forced_colors"),
        "screen.forcedColors",
    )
    add(
        "media_reduced_motion",
        _getattr_path(config, "media_queries.prefers_reduced_motion"),
        "screen.prefersReducedMotion",
    )
    add(
        "media_dynamic_range",
        _getattr_path(config, "media_queries.dynamic_range"),
        "screen.dynamicRange",
    )

    add(
        "timezone",
        _getattr_path(config, "timezone.iana_name"),
        "timezone.timeZone",
    )
    add(
        "timezone_offset",
        _getattr_path(config, "timezone.offset_minutes"),
        "timezone.offsetMinutes",
    )
    add("locale", _getattr_path(config, "locale.primary"), "timezone.locale")
    add(
        "locale_numbering",
        _getattr_path(config, "locale.numbering_system"),
        "timezone.numberingSystem",
    )
    add("locale_calendar", _getattr_path(config, "locale.calendar"), "timezone.calendar")
    add("locale_hour_cycle", _getattr_path(config, "locale.hour_cycle"), "timezone.hourCycle")

    add(
        "webgl_renderer",
        _getattr_path(config, "webgl.unmasked_renderer"),
        "webgl.unmaskedRenderer",
    )
    add(
        "webgl_vendor",
        _getattr_path(config, "webgl.unmasked_vendor"),
        "webgl.unmaskedVendor",
    )
    add("canvas_hash", "", "canvas.hash")
    add("webgl_pixel_hash", "", "webgl.pixelHash")
    add("audio_hash", "", "audio.hash")
    add("rects_host", "", "rects.host")
    add("fonts_installed", "", "fonts.installed")
    add("media_devices_count", "", "media_devices.count")
    add("sensors_motion", "", "sensors.hasDeviceMotion")
    add("storage_quota", "", "storage.storage.quota")
    add("battery_level", "", "storage.battery.level")
    add("connection_effective_type", "", "storage.connection.effectiveType")
    add("iframe_sync", "", "iframe_sync.iframe.userAgent")
    add("worker_sync", "", "worker_sync.userAgent")

    return expectations


def _extract(payload: Any, path: str) -> Any:
    """Extract a dotted path out of the harness payload."""

    current = payload
    for part in path.split("."):
        current = current.get(part) if isinstance(current, dict) else None
        if current is None:
            return None
    return current


def _axis_error(axes: dict[str, Any], path: str) -> str | None:
    """Return the error message of the axis the ``path`` points into.

    The harness reports a failing axis as ``{"error": "..."}``.  The error
    lives on the *axis* object, not on the leaf the path finally addresses,
    so the whole chain has to be inspected.
    """

    current: Any = axes
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        if "error" in current and isinstance(current.get("error"), str):
            return current["error"]
        current = current.get(part)
        if current is None:
            return None
    if isinstance(current, dict):
        error = current.get("error")
        if isinstance(error, str):
            return error
    return None


def compare_expectations(
    expectations: dict[str, tuple[str, str]],
    payload: Any,
    *,
    strict: bool = False,
) -> list[ProbeResult]:
    """Compare measured values against the expected values.

    ``expectations`` maps an axis name to ``(expected, actual_path)``.  An axis
    whose expected value is the empty string is only *reported* -- it is not
    asserted -- unless ``strict`` is set, in which case a missing measurement
    is an explicit failure.

    ``payload`` may be the complete harness payload (``{"axes": {...}}``) or
    the bare ``axes`` mapping; both shapes are accepted.
    """

    results: list[ProbeResult] = []
    if isinstance(payload, dict) and isinstance(payload.get("axes"), dict):
        axes: dict[str, Any] = payload["axes"]
    elif isinstance(payload, dict):
        axes = payload
    else:
        axes = {}

    for axis in sorted(expectations):
        expected, path = expectations[axis]
        measured = _extract(axes, path)
        axis_error = _axis_error(axes, path)
        if axis_error is not None:
            results.append(
                ProbeResult(
                    axis=axis,
                    ok=False,
                    value="",
                    detail=f"measurement error at {path}: {axis_error}",
                )
            )
            continue
        if measured is None:
            if expected == "" and not strict:
                results.append(
                    ProbeResult(
                        axis=axis,
                        ok=True,
                        value="",
                        detail=f"not asserted ({path} unavailable)",
                    )
                )
            else:
                results.append(
                    ProbeResult(
                        axis=axis,
                        ok=False,
                        value="",
                        detail=f"missing measurement at {path}",
                    )
                )
            continue

        actual_text = _stringify(measured)
        if expected == "":
            results.append(
                ProbeResult(
                    axis=axis,
                    ok=True,
                    value=actual_text,
                    detail="informational",
                )
            )
            continue

        ok = actual_text == expected
        results.append(
            ProbeResult(
                axis=axis,
                ok=ok,
                value=actual_text,
                detail="match" if ok else f"expected {expected!r}",
            )
        )
    return results


# --------------------------------------------------------------------------
# Signature helpers
# --------------------------------------------------------------------------


def compute_signature(results: Sequence[ProbeResult]) -> str:
    """Compute a stable signature over the measured axis values.

    Every axis that carries a measured value contributes to the signature --
    the noise-bearing axes (canvas / webgl / audio) *and* the asserted identity
    axes.  Two profiles therefore only share a signature when they are
    indistinguishable across the whole measured surface, which is exactly
    the property the collision guard needs.
    """

    parts: list[str] = []
    for result in results:
        if result.value:
            parts.append(f"{result.axis}={result.value}")
    parts.sort()
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest


def collision_stats(signature: str, seen_signatures: set[str]) -> tuple[float, float]:
    """Return ``(distinct_ratio, collision_rate)`` for a single signature.

    ``distinct_ratio`` is ``1.0`` when the signature has never been observed
    and ``0.0`` when it collides with an already-seen signature.
    ``collision_rate`` is ``1.0`` on a collision and ``0.0`` otherwise.
    """

    collided = signature in seen_signatures
    distinct_ratio = 0.0 if collided else 1.0
    collision_rate = 1.0 if collided else 0.0
    return distinct_ratio, collision_rate


# --------------------------------------------------------------------------
# Runtime probe
# --------------------------------------------------------------------------


class RuntimeProbe:
    """Collect the fingerprint axes of a live browser and compare them."""

    def __init__(
        self,
        chrome_path: str | None = None,
        headless: bool = True,
        *,
        session_factory: Callable[[], Awaitable[ProbeSession]] | None = None,
        strict: bool = False,
    ) -> None:
        self._chrome_path = chrome_path
        self._headless = headless
        self._strict = strict
        self._session_factory = session_factory
        self._seen_signatures: set[str] = set()

    # -- properties --------------------------------------------------------

    @property
    def seen_signatures(self) -> set[str]:
        """The set of signatures already observed by this probe instance."""

        return self._seen_signatures

    def reset_signatures(self) -> None:
        """Forget every previously observed signature."""

        self._seen_signatures.clear()

    # -- session creation --------------------------------------------------

    async def _default_session_factory(self) -> ProbeSession:
        chrome = CdpSession.find_chrome(self._chrome_path)
        session = CdpSession(chrome, headless=self._headless)
        await session.start()
        return session

    async def _open_session(self) -> ProbeSession:
        factory = self._session_factory
        if factory is None:
            factory = self._default_session_factory
        return await factory()

    # -- collection --------------------------------------------------------

    async def collect_async(self, config: Any) -> ProbeReport:
        """Run the harness in a browser and compare against ``config``."""

        session = await self._open_session()
        try:
            payload = await session.evaluate(HARNESS_RUN_EXPRESSION)
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close()

        if not isinstance(payload, dict):
            payload = {"error": "invalid-payload", "axes": {}}
        axes = payload.get("axes")
        if not isinstance(axes, dict):
            axes = {}

        expectations = evaluate_axis_expectations(config)
        results = compare_expectations(expectations, payload, strict=self._strict)
        consistency_pass = bool(results) and all(result.ok for result in results)

        signature = compute_signature(results)
        distinct_ratio, collision_rate = collision_stats(signature, self._seen_signatures)
        self._seen_signatures.add(signature)

        persistence_ok = self._check_persistence(axes)
        ok = consistency_pass and collision_rate == 0.0

        return ProbeReport(
            profile_id=_profile_id(config),
            ok=ok,
            results=results,
            distinct_ratio=distinct_ratio,
            collision_rate=collision_rate,
            persistence_ok=persistence_ok,
            consistency_pass=consistency_pass,
        )

    def collect(self, config: Any) -> ProbeReport:
        """Synchronous wrapper around :meth:`collect_async`."""

        return _run_sync(self.collect_async(config))

    # -- persistence -------------------------------------------------------

    @staticmethod
    def _check_persistence(axes: dict[str, Any]) -> bool:
        """Verify that the cross-context axes agree with the main frame.

        The canvas noise must be identical in the main frame and in every
        embedded/worker context, otherwise the profile leaks its true identity
        the moment a page creates an iframe.
        """

        worker = axes.get("worker_sync")
        if isinstance(worker, dict) and "error" not in worker:
            main_hash = ""
            canvas = axes.get("canvas")
            if isinstance(canvas, dict):
                main_hash = str(canvas.get("hash", ""))
            worker_hash = str(worker.get("hash", ""))
            if main_hash and worker_hash and main_hash != worker_hash:
                return False
        iframes = axes.get("iframe_sync")
        if isinstance(iframes, dict) and "error" not in iframes:
            main_agent = ""
            navigator_payload = axes.get("navigator")
            if isinstance(navigator_payload, dict):
                main_agent = str(navigator_payload.get("userAgent", ""))
            for key in ("iframe", "srcdocIframe", "sandboxedIframe"):
                frame = iframes.get(key)
                if not isinstance(frame, dict) or "error" in frame:
                    continue
                if main_agent and str(frame.get("userAgent", "")) != main_agent:
                    return False
        return True


def _profile_id(config: Any) -> str:
    identity = _getattr_path(config, "identity")
    if identity is None:
        return ""
    value = _getattr_path(identity, "id")
    return str(value) if value is not None else ""


def _run_sync(coro: Awaitable[Any]) -> Any:
    """Run a coroutine from synchronous code.

    A dedicated event loop is used so that the probe can be called from plain
    synchronous test code without requiring ``pytest-asyncio``.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    raise RuntimeError(
        "collect() cannot be called from inside a running event loop; use collect_async() instead"
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def find_free_port(host: str = "127.0.0.1") -> int:
    """Return a currently free TCP port on ``host``."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def harness_html_path() -> Path:
    """Return the path of the checked-in harness page."""

    return HARNESS_HTML_PATH


def live_probe_enabled() -> bool:
    """Return ``True`` when the opt-in live browser test is enabled."""

    return os.environ.get(LIVE_ENV_VAR, "").strip() in {"1", "true", "TRUE", "yes"}


__all__ = [
    "CdpClient",
    "CdpError",
    "CdpSession",
    "DEFAULT_CHROME_PATHS",
    "HARNESS_RUN_EXPRESSION",
    "LIVE_ENV_VAR",
    "ProbeReport",
    "ProbeResult",
    "ProbeSession",
    "RuntimeProbe",
    "SessionFactory",
    "build_harness_html",
    "collision_stats",
    "compare_expectations",
    "compute_signature",
    "evaluate_axis_expectations",
    "find_free_port",
    "harness_html_path",
    "live_probe_enabled",
]
