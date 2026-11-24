"""Make the browser behave like the handset the profile describes.

A profile that claims to be an Android handset has to *be* one, not
merely say so.  ``navigator.maxTouchPoints`` says the panel takes ten
fingers, but a browser that is not in touch mode still hands the page a
mouse: the page sees ``ontouchstart`` missing, the pointer events report
a mouse, and ``matchMedia("(pointer: coarse)")`` says the device has a
fine pointer.  Those three readings contradict the touch-point count,
and a script that reads all of them knows the profile is a desktop
pretending to be a handset.

The touch mode is therefore turned on in the browser itself.  The
browser's own device emulation does it, and the browser's own driver --
the one that drives Chrome for the mobile web tests -- uses exactly
three commands:

``Emulation.setDeviceMetricsOverride``
    Gives the page the panel's own resolution and pixel ratio, and
    marks the page as a mobile one.

``Emulation.setTouchEmulationEnabled``
    Makes the mouse *behave* as a finger: the page receives touch
    events, the cursor is drawn as a fingertip, and a drag is a swipe.
    This is the command that turns the operator's mouse into a finger,
    and it is the reason the emulation is applied through the protocol
    rather than through a command line switch.

``Emulation.setEmitTouchEventsForMouse``
    Says where those touch events come from -- and the answer is the
    mouse.  Chromium implements it by handing the browser's own input
    pipeline a touch emulator in the ``kEmulatingTouchFromMouse`` mode,
    which is the mode the browser's own tests describe as "mouse press
    becomes touch start".

``Emulation.setUserAgentOverride``
    Tells the network stack about the handset, so that the request the
    page makes and the answer the page sees agree.

Why the protocol and not a switch
---------------------------------

Chromium has no command line switch that turns a desktop window into a
touch device.  The touch mode of the browser is a per-target setting of
the developer tools, and the only way to set it without a human in
front of the screen is the protocol that the developer tools use.  The
alternative -- a real touchscreen -- is not available on the machine the
browser runs on: Windows only delivers ``WM_TOUCH`` to a window on a
machine that has a digitiser attached to it.

The connection has to stay open
-------------------------------

The touch mode lives on the *attached session*, not on the target: a
target that is attached to a developer protocol client carries the
emulation of that client.  Chromium drops the emulation of a session
when the session is detached, and the launcher closes its websocket as
soon as it has sent the commands -- which detaches the session and
takes the touch mode with it.  The profile then opens, the launcher
reports that the emulation was applied, and the operator drags a mouse
in a browser that the page believes to be a handset.

So the connection stays open.  The emulator keeps one websocket to the
browser for as long as the profile is open, applies the emulation to
every target it sees, and applies it again to the targets that appear
later: a second tab, a popup, a link with ``target=_blank``.  A page
that opens a second tab must not find a desktop in it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.error
import urllib.request
from typing import Any

#: The default port of the developer protocol.
#
# The port is chosen per profile from a range, so that two profiles
# that are open at the same time do not fight over it.
DEFAULT_DEBUG_PORT = 9333

#: How long to wait for the browser to open the protocol, in seconds.
CONNECT_TIMEOUT = 20.0

#: How long to wait for the emulation to be applied, in seconds.
APPLY_TIMEOUT = 20.0

#: How often the watcher looks for targets that appeared, in seconds.
POLL_INTERVAL = 1.0


def _fetch_json(url: str, timeout: float) -> Any:
    """Read one JSON document from ``url``."""

    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class EmulationError(RuntimeError):
    """Raised when the emulation could not be applied."""


class DeviceEmulator:
    """Keep the browser's device emulation on.

    The emulator connects to the browser's developer protocol, finds
    every page target, and applies the commands that turn the target
    into the handset the profile describes.  The connection is held
    open, and the targets that appear later are emulated as well.
    """

    def __init__(
        self,
        *,
        port: int = DEFAULT_DEBUG_PORT,
        width: int = 0,
        height: int = 0,
        device_scale_factor: float = 1.0,
        mobile: bool = True,
        user_agent: str = "",
        max_touch_points: int = 10,
        screen_width: int = 0,
        screen_height: int = 0,
        platform: str = "",
        accept_language: str = "vi-VN,vi;q=0.9",
        timeout: float = APPLY_TIMEOUT,
    ) -> None:
        self.port = port
        self.width = width
        self.height = height
        self.device_scale_factor = device_scale_factor
        self.mobile = mobile
        self.user_agent = user_agent
        self.max_touch_points = max(1, int(max_touch_points or 1))
        self.screen_width = screen_width or width
        self.screen_height = screen_height or height
        self.platform = platform
        self.accept_language = accept_language
        self.timeout = timeout
        #: Set once the first target has been emulated.
        self._ready = asyncio.Event()
        #: The number of the emulated targets.
        self._emulated = 0
        #: Whether the connection to the browser was closed.
        self._closed = False

    # -- the protocol -----------------------------------------------------

    def _ws_url(self) -> str:
        """The websocket the browser exposes the protocol on."""

        version = _fetch_json(
            f"http://127.0.0.1:{self.port}/json/version", timeout=CONNECT_TIMEOUT
        )
        url = str(version.get("webSocketDebuggerUrl", ""))
        if not url:
            raise EmulationError("the browser did not report a websocket URL")
        return url

    def close(self) -> None:
        """Close the connection to the browser.

        The connection is what keeps the touch mode of a target alive,
        so it is closed when the browser is closed.
        """

        self._closed = True
        self._ready.set()

    # -- the commands -----------------------------------------------------

    def _metrics(self) -> dict[str, Any]:
        """The device metrics of the handset the profile describes.

        The screen size and the orientation travel with the metrics:
        a page that reads ``screen.width`` and ``screen.height`` has to
        see the panel of the handset, not the window of the machine.
        """

        metrics: dict[str, Any] = {"mobile": self.mobile}
        if self.width > 0:
            metrics["width"] = self.width
        if self.height > 0:
            metrics["height"] = self.height
        metrics["deviceScaleFactor"] = self.device_scale_factor
        if self.screen_width > 0 and self.screen_height > 0:
            metrics["screenWidth"] = self.screen_width
            metrics["screenHeight"] = self.screen_height
            landscape = self.screen_width > self.screen_height
            metrics["screenOrientation"] = {
                "type": "landscapePrimary" if landscape else "portraitPrimary",
                "angle": 90 if landscape else 0,
            }
        return metrics

    def _user_agent_params(self) -> dict[str, Any]:
        """The user agent override of the profile."""

        if not self.user_agent:
            return {}
        params: dict[str, Any] = {
            "userAgent": self.user_agent,
            "acceptLanguage": self.accept_language,
        }
        if self.platform:
            params["platform"] = self.platform
        return params

    # -- the session ------------------------------------------------------

    async def _apply_to_session(self, send: Any, session: str) -> None:
        """Apply the commands to one target."""

        await send(
            "Emulation.setDeviceMetricsOverride",
            self._metrics(),
            session=session,
        )

        # The mouse becomes a finger.
        #
        # The two commands are not the same thing.  ``setTouch``
        # ``EmulationEnabled`` turns the touch mode on: the page may
        # now receive touch events.  ``setEmitTouchEventsForMouse``
        # says where those events come from -- and the answer is the
        # mouse.  Chromium implements the second one by handing the
        # browser's own input pipeline a touch emulator in the
        # ``kEmulatingTouchFromMouse`` mode, which is the mode the
        # browser's own tests describe as "mouse press becomes touch
        # start".
        #
        # The configuration is ``mobile`` rather than the platform's
        # own, because the platform here is a desktop and the profile
        # claims a handset: the gestures the page receives have to be
        # the ones a handset's panel produces.
        #
        # The touch emulation comes first and the conversion second:
        # the conversion installs the emulator that the touch mode
        # switches on, and the order of the two decides whether the
        # page ends up with a finger or with a mouse.
        await send(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": True, "maxTouchPoints": self.max_touch_points},
            session=session,
        )
        await send(
            "Emulation.setEmitTouchEventsForMouse",
            {"enabled": True, "configuration": "mobile"},
            session=session,
        )

        user_agent_params = self._user_agent_params()
        if user_agent_params:
            await send(
                "Emulation.setUserAgentOverride",
                user_agent_params,
                session=session,
            )

    # -- the watcher ------------------------------------------------------

    async def _watch(self, connection: Any) -> int:
        """Emulate every target, and keep emulating the ones that appear.

        The count of the emulated targets is returned once the first
        target has been emulated.  The coroutine keeps running until
        the connection is closed, so the caller has to keep the
        connection open: the emulation of a target lives on the session
        that holds it, and Chromium drops it when the session goes.
        """

        pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        next_id = 0
        sessions: set[str] = set()
        first_batch = True

        async def read_loop() -> None:
            async for raw in connection:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                identifier = message.get("id")
                if isinstance(identifier, int):
                    future = pending.pop(identifier, None)
                    if future is not None and not future.done():
                        future.set_result(message)

        reader = asyncio.create_task(read_loop())

        async def send(
            method: str,
            params: dict[str, Any] | None = None,
            session: str | None = None,
        ) -> dict[str, Any]:
            nonlocal next_id
            next_id += 1
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            pending[next_id] = future
            message: dict[str, Any] = {
                "id": next_id,
                "method": method,
                "params": params or {},
            }
            if session:
                # The commands of the ``Emulation`` domain live in
                # the page target, not in the browser session.  The
                # session identifier has to travel with the
                # command, or the browser answers that it does not
                # know the method.
                message["sessionId"] = session
            await connection.send(json.dumps(message))
            reply = await asyncio.wait_for(future, timeout=self.timeout)
            if "error" in reply:
                raise EmulationError(f"{method} failed: {reply['error']}")
            return reply.get("result", {})

        try:
            emulated = 0
            while not self._closed:
                targets = await send("Target.getTargets")
                if self._closed:
                    break
                pages = [
                    item
                    for item in targets.get("targetInfos", [])
                    if item.get("type") == "page"
                ]
                for page in pages:
                    target_id = page.get("targetId")
                    if not target_id or target_id in sessions:
                        continue
                    attached = await send(
                        "Target.attachToTarget",
                        {"targetId": target_id, "flatten": True},
                    )
                    session = attached.get("sessionId")
                    if not session:
                        continue
                    try:
                        await self._apply_to_session(send, session)
                    except EmulationError:
                        # A target that goes away while it is being
                        # emulated is not a failure: the watcher sees
                        # the next one.
                        continue
                    sessions.add(target_id)
                    emulated += 1

                if emulated and first_batch:
                    # The first target has been emulated: the caller can
                    # hand the browser to the operator.  The watch
                    # continues, and the targets that appear later are
                    # emulated as well.
                    first_batch = False
                    self._emulated = emulated
                    self._ready.set()

                await asyncio.sleep(POLL_INTERVAL)
        finally:
            reader.cancel()
            with contextlib.suppress(BaseException):
                await reader

    async def apply(self) -> int:
        """Apply the emulation and keep it applied.

        The connection to the browser is held open for as long as the
        coroutine runs: the emulation lives on the attached session, so
        the session has to stay attached for as long as the profile is
        open.  The caller therefore runs this coroutine as a task and
        waits for the ready event rather than for the return value.
        """

        from websockets.asyncio.client import connect  # local import: optional dep

        ws_url = self._ws_url()
        async with connect(ws_url, open_timeout=self.timeout) as connection:
            watch = asyncio.create_task(self._watch(connection))
            try:
                await self._ready.wait()
                return self._emulated
            finally:
                watch.cancel()
                with contextlib.suppress(BaseException):
                    await watch

    async def run_forever(self, on_ready: Any = None) -> int:
        """Apply the emulation and keep it applied until the browser dies.

        The coroutine returns when the connection to the browser is
        gone, which is when the browser is closed.

        ``on_ready`` is called with the number of the emulated targets
        as soon as the first one has been emulated, so that the caller
        can hand the browser to the operator without waiting for the
        watch to end.
        """

        from websockets.asyncio.client import connect  # local import: optional dep

        ws_url = self._ws_url()
        async with connect(ws_url, open_timeout=self.timeout) as connection:
            watch = asyncio.create_task(self._watch(connection))
            try:
                await self._ready.wait()
                if on_ready is not None:
                    on_ready(self._emulated)
                # The watch keeps running: the connection has to stay
                # open for as long as the browser is open.  The
                # coroutine ends when the connection dies.
                await watch
                return self._emulated
            finally:
                watch.cancel()
                with contextlib.suppress(BaseException):
                    await watch


async def apply_to_browser(
    *,
    port: int,
    width: int = 0,
    height: int = 0,
    device_scale_factor: float = 1.0,
    user_agent: str = "",
    max_touch_points: int = 10,
    screen_width: int = 0,
    screen_height: int = 0,
    platform: str = "",
    accept_language: str = "vi-VN,vi;q=0.9",
    attempts: int = 20,
    delay: float = 0.5,
) -> int:
    """Apply the emulation, waiting for the browser to be ready.

    The browser needs a moment before it answers the protocol, and the
    first page needs a moment before it exists.  The function therefore
    retries: the emulation is applied as soon as there is a page to
    apply it to.

    The function returns once the first target has been emulated.  The
    watcher that keeps the emulation alive afterwards is started by the
    caller, which owns the browser and therefore owns the lifetime of
    the connection.
    """

    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        emulator = DeviceEmulator(
            port=port,
            width=width,
            height=height,
            device_scale_factor=device_scale_factor,
            user_agent=user_agent,
            max_touch_points=max_touch_points,
            screen_width=screen_width,
            screen_height=screen_height,
            platform=platform,
            accept_language=accept_language,
        )
        try:
            count = await emulator.apply()
        except (EmulationError, OSError, urllib.error.URLError, asyncio.TimeoutError) as exc:
            last_error = exc
            await asyncio.sleep(delay)
            continue
        if count > 0:
            return count
        await asyncio.sleep(delay)
    if last_error is not None:
        raise EmulationError(f"the device emulation was not applied: {last_error}")
    return 0


__all__ = [
    "DEFAULT_DEBUG_PORT",
    "DeviceEmulator",
    "EmulationError",
    "apply_to_browser",
]
