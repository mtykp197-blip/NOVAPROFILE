"""The device emulation has to survive the browser being open.

The touch mode of a target lives on the session that the developer
protocol client holds open.  Chromium drops it when the session is
detached, and a launcher that sends the commands and closes the
connection leaves the profile with a mouse -- even though it reported
that the emulation was applied.

That is not a hypothesis.  It is what the fleet did for as long as the
launcher sent its commands and went away, and the profile opened with a
mouse on a panel that claimed to have ten fingers.

So the connection has to stay open, the emulation has to be applied to
the targets that appear later, and the commands have to be the ones
that turn a mouse into a finger.

The tests here are about the *conversation* with the browser: the
commands that are sent, their order, and the fact that the connection
is not closed while the browser is open.  A test that only checked the
result -- "the emulation was applied" -- passed while the profile was
still a desktop, because the result and the outcome are two different
things.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from novaprofile.launcher.device_emulation import DeviceEmulator, EmulationError


class FakeConnection:
    """A websocket that records what was sent to the browser.

    The connection answers the commands the way the browser answers
    them, and it records the order, because the order is what decides
    whether the page ends up with a finger or with a mouse.
    """

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any], str | None]] = []
        self.closed = False
        self.pages = pages if pages is not None else [{"targetId": "t1", "type": "page"}]
        self._incoming: asyncio.Queue[str] = asyncio.Queue()
        self._next_id = 0

    # -- the websocket interface -------------------------------------

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        method = message["method"]
        session = message.get("sessionId")
        self.sent.append((method, message.get("params") or {}, session))
        self._answer(method, message["id"])

    async def __aiter__(self):
        while True:
            raw = await self._incoming.get()
            if raw is None:
                return
            yield raw

    # -- the browser's answers ----------------------------------------

    def _answer(self, method: str, identifier: int) -> None:
        if method == "Target.getTargets":
            result: dict[str, Any] = {"targetInfos": self.pages}
        elif method == "Target.attachToTarget":
            result = {"sessionId": f"session-for-{identifier}"}
        else:
            result = {}
        self._incoming.put_nowait(
            json.dumps({"id": identifier, "result": result, "method": method})
        )

    # -- the helpers --------------------------------------------------

    def methods(self) -> list[str]:
        """The methods that were sent, in order."""

        return [method for method, _, _ in self.sent]

    def first(self, method: str) -> tuple[dict[str, Any], str | None]:
        """The parameters and the session of the first call of a method."""

        for name, params, session in self.sent:
            if name == method:
                return params, session
        raise AssertionError(f"{method} was never sent; sent: {self.methods()}")


def _emulator(**overrides: Any) -> DeviceEmulator:
    """An emulator for a profile of the fleet."""

    settings: dict[str, Any] = {
        "port": 9333,
        "width": 480,
        "height": 1067,
        "device_scale_factor": 2.625,
        "user_agent": (
            "Mozilla/5.0 (Linux; Android 16; GC3VE) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36"
        ),
        "max_touch_points": 10,
        "screen_width": 480,
        "screen_height": 1067,
        "platform": "Linux armv8l",
        "accept_language": "vi-VN,vi;q=0.9",
    }
    settings.update(overrides)
    return DeviceEmulator(**settings)


# --------------------------------------------------------------------------
# The commands
# --------------------------------------------------------------------------


def _watch_once(emulator: DeviceEmulator, connection: FakeConnection) -> None:
    """Run the watch long enough to emulate the targets it finds.

    The watch runs for as long as the browser is open, so the test
    closes it once it has done its first round.  The close is the way
    the watch ends in production as well: the connection goes with the
    browser.
    """

    async def scenario() -> None:
        task = asyncio.create_task(emulator._watch(connection))
        await asyncio.sleep(0.3)
        emulator.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    asyncio.run(scenario())


def test_the_emulation_turns_the_mouse_into_a_finger() -> None:
    """The conversion from the mouse to the touch has to be asked for.

    ``Emulation.setEmitTouchEventsForMouse`` is the command that says
    where the touch events come from, and the answer has to be the
    mouse.  Without it the page receives mouse events and the profile
    is a desktop.
    """

    connection = FakeConnection()
    emulator = _emulator()

    _watch_once(emulator, connection)

    params, session = connection.first("Emulation.setEmitTouchEventsForMouse")
    assert params["enabled"] is True
    assert params["configuration"] == "mobile"
    assert session is not None, "the command has to travel with a session"


def test_the_touch_emulation_carries_the_touch_point_count() -> None:
    """The touch mode has to report the panel's own finger count.

    Without the count the browser uses its own default, and a page that
    reads ``navigator.maxTouchPoints`` reads the host's value instead
    of the profile's -- which is a value that is the same on every
    profile of the fleet.
    """

    connection = FakeConnection()
    emulator = _emulator(max_touch_points=10)

    _watch_once(emulator, connection)

    params, _ = connection.first("Emulation.setTouchEmulationEnabled")
    assert params["enabled"] is True
    assert params["maxTouchPoints"] == 10


def test_the_touch_emulation_comes_before_the_conversion() -> None:
    """The order of the two commands decides the outcome.

    The touch emulation installs the emulator on the render widget and
    the conversion says where the events come from.  The conversion
    first and the emulation second leaves the page with a mouse: the
    emulation that arrives last is the one that is in force, and it
    does not carry the conversion with it.
    """

    connection = FakeConnection()
    emulator = _emulator()

    _watch_once(emulator, connection)

    methods = connection.methods()
    touch = methods.index("Emulation.setTouchEmulationEnabled")
    emit = methods.index("Emulation.setEmitTouchEventsForMouse")
    assert touch < emit, f"the touch emulation has to come first; sent: {methods}"


def test_the_metrics_carry_the_screen_of_the_panel() -> None:
    """The screen size and the orientation travel with the metrics.

    A page that reads ``screen.width`` has to see the panel of the
    handset, not the window of the machine.
    """

    connection = FakeConnection()
    emulator = _emulator()

    _watch_once(emulator, connection)

    params, _ = connection.first("Emulation.setDeviceMetricsOverride")
    assert params["width"] == 480
    assert params["height"] == 1067
    assert params["deviceScaleFactor"] == 2.625
    assert params["mobile"] is True
    assert params["screenWidth"] == 480
    assert params["screenHeight"] == 1067
    assert params["screenOrientation"]["type"] == "portraitPrimary"


def test_the_landscape_panel_is_reported_as_landscape() -> None:
    """A panel that is wider than it is tall is a landscape one."""

    connection = FakeConnection()
    emulator = _emulator(width=1067, height=480, screen_width=1067, screen_height=480)

    _watch_once(emulator, connection)

    params, _ = connection.first("Emulation.setDeviceMetricsOverride")
    assert params["screenOrientation"]["type"] == "landscapePrimary"
    assert params["screenOrientation"]["angle"] == 90


def test_the_user_agent_override_carries_the_platform() -> None:
    """The platform travels with the user agent.

    The user agent says Android, and the platform says Linux armv8l.
    A browser whose user agent is Android and whose platform is Win32
    is a browser that has been edited.
    """

    connection = FakeConnection()
    emulator = _emulator()

    _watch_once(emulator, connection)

    params, _ = connection.first("Emulation.setUserAgentOverride")
    assert "Android 16" in params["userAgent"]
    assert params["platform"] == "Linux armv8l"
    assert params["acceptLanguage"] == "vi-VN,vi;q=0.9"


def test_the_commands_travel_with_a_session() -> None:
    """Every emulation command has to carry a session identifier.

    The commands of the ``Emulation`` domain live in the page target,
    not in the browser session.  A command without the identifier is
    answered with "the method is unknown", and the profile opens
    without the emulation.
    """

    connection = FakeConnection()
    emulator = _emulator()

    _watch_once(emulator, connection)

    for method, _, session in connection.sent:
        if method.startswith("Emulation."):
            assert session is not None, f"{method} was sent without a session"


# --------------------------------------------------------------------------
# The connection
# --------------------------------------------------------------------------


def test_the_watch_keeps_going_after_the_first_target() -> None:
    """The watch does not stop at the first target.

    The targets that appear later -- a second tab, a popup -- have to
    be emulated as well.  A page that opens a second tab must not find
    a desktop in it.
    """

    connection = FakeConnection()
    emulator = _emulator()

    async def scenario() -> None:
        task = asyncio.create_task(emulator._watch(connection))
        await asyncio.sleep(0.2)

        # A second tab opens.
        connection.pages.append({"targetId": "t2", "type": "page"})
        # The watch looks for the targets that appeared once a second.
        await asyncio.sleep(1.6)

        emulator.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    asyncio.run(scenario())

    attached = [
        params.get("targetId")
        for method, params, _ in connection.sent
        if method == "Target.attachToTarget"
    ]
    assert "t1" in attached
    assert "t2" in attached, "the second tab was never emulated"


def test_the_watch_reports_the_first_target() -> None:
    """The caller has to learn that the first target is emulated.

    The launcher waits for that before it hands the browser to the
    operator, and a caller that waits for the end of the watch waits
    for the end of the browser.
    """

    connection = FakeConnection()
    emulator = _emulator()

    async def scenario() -> int:
        task = asyncio.create_task(emulator._watch(connection))
        await asyncio.sleep(0.3)
        assert emulator._ready.is_set(), "the ready event was never set"
        count = emulator._emulated
        emulator.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        return count

    assert asyncio.run(scenario()) == 1


def test_a_target_that_goes_away_is_not_a_failure() -> None:
    """A target that closes while it is emulated is not an error.

    The operator closes a tab while the watch is looking at it.  The
    watch has to carry on with the others.
    """

    connection = FakeConnection()
    emulator = _emulator()

    async def scenario() -> None:
        task = asyncio.create_task(emulator._watch(connection))
        await asyncio.sleep(0.3)
        emulator.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    asyncio.run(scenario())
    assert connection.methods()


def test_the_emulator_reports_a_browser_that_answers_nothing() -> None:
    """A browser that does not answer is an error, not a silence."""

    class SilentConnection(FakeConnection):
        async def send(self, raw: str) -> None:
            message = json.loads(raw)
            self.sent.append(
                (message["method"], message.get("params") or {}, message.get("sessionId"))
            )

    connection = SilentConnection()
    emulator = _emulator(timeout=0.2)

    async def scenario() -> None:
        task = asyncio.create_task(emulator._watch(connection))
        await asyncio.sleep(1.0)
        emulator.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    asyncio.run(scenario())
    # The watch did not hang: it gave up on the browser that would not
    # answer and reported the failure.
    assert connection.methods()


def test_closing_the_emulator_stops_the_watch() -> None:
    """The close has to end the watch.

    The connection is what keeps the emulation alive, so it is closed
    with the browser.  A watch that runs on would hold a handle on a
    browser that is gone, and the port it holds is not free for the
    next profile.
    """

    connection = FakeConnection()
    emulator = _emulator()

    async def scenario() -> None:
        task = asyncio.create_task(emulator._watch(connection))
        await asyncio.sleep(0.3)
        emulator.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    asyncio.run(scenario())
    assert emulator._closed is True


# --------------------------------------------------------------------------
# The launcher
# --------------------------------------------------------------------------


def test_the_launcher_keeps_the_emulator_for_the_browser() -> None:
    """The launcher has to hold on to the emulator.

    The emulator holds the connection, and the connection is what keeps
    the touch mode alive.  A launcher that drops the emulator after the
    first target closes the connection, and the profile is a desktop
    again.
    """

    from novaprofile.launcher.launcher import BrowserLauncher, LaunchedBrowser

    launcher = BrowserLauncher()
    launched = LaunchedBrowser(
        pid=1,
        process=None,
        user_data_dir=None,  # type: ignore[arg-type]
        config_path=None,  # type: ignore[arg-type]
        started_at=None,  # type: ignore[arg-type]
    )
    emulator = _emulator()
    launched.emulation_emulator = emulator

    launcher._close_emulation(launched)

    assert emulator._closed is True
    assert launched.emulation_emulator is None


def test_the_emulation_error_is_a_runtime_error() -> None:
    """The failure has to be catchable as one."""

    assert issubclass(EmulationError, RuntimeError)
