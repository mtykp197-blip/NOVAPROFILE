"""The profile the browser shows must be the profile the simulator describes.

The simulator (:mod:`novaprofile.probe.simulator`) is the model of what a
profile *would* report.  It is the reference side of every comparison the
control plane makes: the probe, the collision guard, the measurement
ledger.  But a model can be beautifully self-consistent and still be
*wrong* -- it can describe a browser that does not exist.

This test closes that gap by measuring the **real thing**: it generates a
profile, installs that profile's JavaScript runtime into a real Chromium
at ``document_start``, runs the checked-in probe harness inside that
browser, and then compares the readings the *browser* produced against
the readings the *simulator* produces for the very same config.

The rule this test enforces is the strict one: for every axis it names,
the value the browser reports must be **equal** to the value the
simulator reports.  Not "close", not "within a tolerance" -- equal, for
the identity axes a site reads in its first pass.

Why this test cannot be satisfied by the code it tests
--------------------------------------------------------

The two sides of the comparison come from two different places.  The
expected values come from the simulator, which is a pure-Python model
that never opens a browser.  The actual values come from a Chromium
process that is launched by this test, driven over the DevTools protocol,
and measured by a harness that is a checked-in static HTML file.  Neither
side is derived from the other, so a disagreement here is a fact about
the world and not an artefact of the test.

That is why the test also carries a **negative control**: it launches a
browser with profile *A*'s runtime and compares the readings against
profile *B*'s simulator, and demands that the comparison *fails*.  A
test that cannot fail proves nothing, and a test that compares a browser
against itself would prove nothing either.

Mismatches that were found and fixed
-------------------------------------

    These tests were first run against Chrome 157 on Windows and found the
following genuine disagreements between the browser and the simulator.
They were **recorded, not hidden** -- the simulator was not patched to
make them agree, because that would conceal the defect.  Each one has
since been fixed at the source, in the runtime, and each is now
asserted below so that it cannot come back.

``navigator.webdriver``
    The JavaScript runtime used to skip this accessor
    (``novaprofile/js_runtime/builder.py``: ``if (key === 'webdriver')
    { return; }``), on the reasoning that the native core handles it
    better.  Under a plain headless Chromium there is no native core, so
    the stock ``navigator.webdriver === true`` survived into the page
    while the simulator reported ``false``.  This was a **real leak**: a
    site that reads ``navigator.webdriver`` saw "automated" on every
    profile that runs through the JavaScript runtime alone.  The runtime
    now overrides it; the guard that stands down when the native core
    is already in place is kept.

``timezone.offsetMinutes``
    The harness reads the offset as ``-new Date(...).getTimezoneOffset()``.
    The runtime proxied ``Date`` but not
    ``Date.prototype.getTimezoneOffset``, so the browser reported the
    *host* machine's offset (for example 420 for Asia/Bangkok) rather
    than the profile's.  Reproduced on three different presets
    (pixel-8-pro-android14, galaxy-s23-android14, galaxy-a54-android13).
    The prototype method is now overridden as well.

``timezone.locale``
    The runtime's ``Intl.DateTimeFormat`` proxy rewrote ``timeZone`` in
    ``resolvedOptions()`` but left ``locale`` alone, so the browser
    reported the host's ``en-US`` where the simulator reported the
    profile's (``es-US``, ``pt-PT``, ``fil-PH``).  The formatter now
    defaults to the profile's locale, and ``Intl.NumberFormat`` and
    ``Intl.Collator`` -- which resolved to the host's locale in the
    same way -- are covered by the same rule.

``timezone.hourCycle``
    Same root cause: the browser reported ``undefined`` (the harness
    serialises it as ``null``) where the simulator reported the
    profile's ``h12`` / ``h23``.  The hour cycle travels with the
    locale now.

Mismatches that are still open
-------------------------------

``navigator.userAgentData``
    The simulator always reports ``mobile: true`` and a populated brand
    list.  The browser reports the host's real ``userAgentData``, which
    on a desktop Chromium says ``mobile: false``.  The runtime does not
    substitute the client-hint surface.

Skipping
--------

A machine without a Chromium-based browser cannot run this test, and
that is not a failure of the product -- it is a fact about the machine.
The test therefore *skips* with an actionable message rather than
failing.  The browser is located through the same search the launcher
uses (Chrome, then Edge), so a workstation that has either of them
installed runs the test for real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.js_runtime.builder import build_script
from novaprofile.probe.e2e import DevToolsClient, find_browser
from novaprofile.probe.harness import HARNESS_HTML_PATH
from novaprofile.probe.simulator import simulate_payload
from novaprofile.schema.profile import ProfileConfig

#: The expression that runs the checked-in probe harness in the page.
HARNESS_RUN_EXPRESSION = (
    "window.__NOVA_PROBE__ && typeof window.__NOVA_PROBE__.run === 'function'"
    " ? window.__NOVA_PROBE__.run() : Promise.resolve({ error: 'harness-missing' })"
)

#: The expression that reports whether the harness finished installing.
HARNESS_READY_EXPRESSION = "window.__NOVA_PROBE_READY__ === true"

#: The ten axes this test asserts on.
#:
#: Each entry is ``(label, path-in-the-axis-payload)``.  The path is the
#: same in the browser payload and in the simulator payload, because both
#: are produced by the same ``nova.probe/1`` schema: the simulator builds
#: the payload from the config, and the harness builds it from the live
#: accessors.
ASSERTED_AXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("userAgent", ("navigator", "userAgent")),
    ("platform", ("navigator", "platform")),
    ("hardwareConcurrency", ("navigator", "hardwareConcurrency")),
    ("deviceMemory", ("navigator", "deviceMemory")),
    ("language", ("navigator", "language")),
    ("webdriver", ("navigator", "webdriver")),
    ("screen.width", ("screen", "width")),
    ("screen.height", ("screen", "height")),
    ("devicePixelRatio", ("screen", "devicePixelRatio")),
    ("timezone", ("timezone", "timeZone")),
    ("timezone.locale", ("timezone", "locale")),
    ("timezone.hourCycle", ("timezone", "hourCycle")),
    ("timezone.offsetMinutes", ("timezone", "offsetMinutes")),
    ("webgl.renderer", ("webgl", "unmaskedRenderer")),
)

#: The presets the test runs over.  Three families with three different
#: panels, three different locales and three different GPU SKUs, so that
#: a pass is a statement about the fleet and not about one handset.
PRESETS: tuple[tuple[str, str], ...] = (
    ("pixel-8-pro-android14", "1" * 64),
    ("galaxy-s23-android14", "2" * 64),
    ("galaxy-a54-android13", "3" * 64),
)


# --------------------------------------------------------------------------
# Locating the browser
# --------------------------------------------------------------------------


def _browser_path() -> str | None:
    """Return the path of a Chromium-based browser, or ``None``.

    The search is the one the launcher already uses: the known Chrome
    locations first, then Edge, then whatever is on ``PATH``.  When
    nothing is found the test skips -- a machine without a browser cannot
    measure a fingerprint, and that is not a product defect.
    """

    try:
        return find_browser()
    except Exception:
        return None


_BROWSER: str | None = _browser_path()

if _BROWSER is None:
    _SKIP_REASON = (
        "no Chrome/Chromium/Edge executable is installed on this machine; "
        "the end-to-end launch probe needs a real browser to measure"
    )
else:
    _SKIP_REASON = ""


# --------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowserMeasurement:
    """The readings one browser run produced."""

    payload: dict[str, Any]
    browser: str
    browser_version: str

    @property
    def axes(self) -> Mapping[str, Any]:
        """The axes the harness reported."""

        axes = self.payload.get("axes")
        return axes if isinstance(axes, Mapping) else {}


def _fetch_json(url: str) -> dict[str, Any]:
    """Fetch a JSON document from the local DevTools endpoint."""

    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - loopback only
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the browser *and* every child it spawned."""

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


def _read_devtools_port(user_data_dir: Path, timeout: float = 30.0) -> int:
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
    raise TimeoutError("the browser did not report a DevTools port in time")


async def _measure(config: ProfileConfig, *, timeout: float = 120.0) -> BrowserMeasurement:
    """Run the probe harness in a real browser carrying ``config``.

    The steps mirror the verification runner: build the profile's
    JavaScript runtime, launch a Chromium with a throw-away profile,
    install the runtime at ``document_start``, load the checked-in
    harness page and read its report.
    """

    script = build_script(config)
    browser = _BROWSER
    assert browser is not None  # guarded by the caller

    user_data_dir = Path(tempfile.mkdtemp(prefix="nova-e2e-probe-"))
    command = [
        browser,
        "--headless=new",
        "--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-extensions",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--mute-audio",
        "about:blank",
    ]
    process = subprocess.Popen(  # noqa: S603 - explicit, local binary
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client: DevToolsClient | None = None
    try:
        port = await asyncio.to_thread(_read_devtools_port, user_data_dir)
        version = await asyncio.to_thread(_fetch_json, f"http://127.0.0.1:{port}/json/version")
        ws_url = str(version.get("webSocketDebuggerUrl", ""))
        if not ws_url:
            raise RuntimeError("the browser did not report a websocket debugger URL")

        client = DevToolsClient(ws_url, timeout=timeout)
        await client.connect()

        target = await client.send("Target.createTarget", {"url": "about:blank"})
        target_id = target.get("targetId")
        if not isinstance(target_id, str):
            raise RuntimeError("Target.createTarget did not return a targetId")
        attached = await client.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        raw_session = attached.get("sessionId")
        session_id = raw_session if isinstance(raw_session, str) else None

        await client.send("Page.enable", session_id=session_id)
        await client.send("Runtime.enable", session_id=session_id)

        # The runtime has to be installed *before* the first navigation,
        # otherwise the harness would measure a stock browser and never
        # see the profile at all.
        await client.add_script_to_evaluate_on_new_document(script, session_id=session_id)

        url = HARNESS_HTML_PATH.resolve().as_uri()
        await client.send("Page.navigate", {"url": url}, session_id=session_id)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ready = await client.evaluate(HARNESS_READY_EXPRESSION, session_id=session_id)
            except Exception:
                ready = False
            if ready:
                break
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError("the probe harness did not initialise in time")

        payload = await client.evaluate(HARNESS_RUN_EXPRESSION, session_id=session_id)
        if not isinstance(payload, Mapping):
            raise RuntimeError("the harness did not return a JSON object")
        if payload.get("error"):
            raise RuntimeError(str(payload.get("error")))

        return BrowserMeasurement(
            payload=dict(payload),
            browser=browser,
            browser_version=str(version.get("Browser", "")),
        )
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
        await asyncio.to_thread(_terminate_tree, process)
        shutil.rmtree(user_data_dir, ignore_errors=True)


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    """Read a dotted path out of an axis payload."""

    current: Any = payload
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


# --------------------------------------------------------------------------
# The tests
# --------------------------------------------------------------------------


@pytest.mark.skipif(_BROWSER is None, reason=_SKIP_REASON)
@pytest.mark.parametrize(("preset_id", "seed"), PRESETS, ids=[p for p, _ in PRESETS])
def test_the_browser_agrees_with_the_simulator_on_every_axis(preset_id: str, seed: str) -> None:
    """The profile, measured in a real browser, equals the profile, modelled.

    The browser side comes from a Chromium process this test launched and
    a checked-in harness page; the expected side comes from the
    simulator, which never opens a browser.  Where they disagree, the
    disagreement is real.
    """

    config = ProfileGenerator().generate(
        preset_id, name=f"E2E {preset_id}", master_seed=seed
    )

    measurement = asyncio.run(_measure(config))
    simulated = simulate_payload(config).get("axes", {})

    assert measurement.axes, "the harness reported no axes"

    mismatches: list[str] = []
    for label, path in ASSERTED_AXES:
        measured_value = _dig(measurement.axes, path)
        expected_value = _dig(simulated, path)
        if measured_value != expected_value:
            mismatches.append(
                f"{label}: browser={measured_value!r} != simulator={expected_value!r}"
            )

    assert mismatches == [], (
        "the browser and the simulator disagree on these axes: " + "; ".join(mismatches)
    )


@pytest.mark.skipif(_BROWSER is None, reason=_SKIP_REASON)
def test_the_comparison_can_actually_fail() -> None:
    """The negative control: a foreign profile has to be detected.

    The browser is given profile *A*'s runtime and the readings are
    compared against profile *B*'s simulator.  If this comparison passed,
    the comparison would be measuring nothing -- it would be reading the
    simulator on both sides, or the browser on both sides, or it would
    be asserting so little that two different handsets look the same.

    This is the difference between a test and a tautology.
    """

    preset_a, seed_a = PRESETS[0]
    preset_b, seed_b = PRESETS[1]

    config_a = ProfileGenerator().generate(
        preset_a, name="E2E control A", master_seed=seed_a
    )
    config_b = ProfileGenerator().generate(
        preset_b, name="E2E control B", master_seed=seed_b
    )

    measurement = asyncio.run(_measure(config_a))
    simulated_b = simulate_payload(config_b).get("axes", {})

    differing: list[str] = []
    for label, path in ASSERTED_AXES:
        measured_value = _dig(measurement.axes, path)
        expected_value = _dig(simulated_b, path)
        if measured_value != expected_value:
            differing.append(label)

    # Two different handset families must not look identical across the
    # ten axes.  Some axes legitimately coincide -- the platform string
    # is "Linux armv7l" on both -- but the identity of the device has to
    # show up somewhere, and in practice it shows up in most of them.
    assert differing, (
        "the browser reading of profile A was indistinguishable from the "
        "simulator reading of profile B on every asserted axis -- the "
        "comparison is not discriminating between profiles"
    )
    assert len(differing) >= 4, (
        "only "
        + ", ".join(differing)
        + " differed between two different handset families; the comparison "
        "is too weak to catch a substituted profile"
    )


@pytest.mark.skipif(_BROWSER is None, reason=_SKIP_REASON)
def test_the_browser_reports_the_profile_and_not_the_host() -> None:
    """The measured values must come from the profile, not from the host.

    A browser that reports its own identity while the simulator describes
    the handset would pass every comparison that only ever compares the
    browser against the simulator for the *same* profile -- the two
    would agree, because the simulator would have been taught the host's
    values.  So the check has to be independent: the measured values are
    compared against the values *the config declares*, straight out of
    the profile, with no simulator in between.
    """

    preset_id, seed = PRESETS[0]
    config = ProfileGenerator().generate(
        preset_id, name="E2E direct", master_seed=seed
    )

    measurement = asyncio.run(_measure(config))

    declared: list[tuple[str, Any, Any]] = [
        (
            "userAgent",
            _dig(measurement.axes, ("navigator", "userAgent")),
            config.navigator.user_agent,
        ),
        ("platform", _dig(measurement.axes, ("navigator", "platform")), config.navigator.platform),
        (
            "hardwareConcurrency",
            _dig(measurement.axes, ("navigator", "hardwareConcurrency")),
            config.navigator.hardware_concurrency,
        ),
        (
            "deviceMemory",
            _dig(measurement.axes, ("navigator", "deviceMemory")),
            config.navigator.device_memory,
        ),
        ("language", _dig(measurement.axes, ("navigator", "language")), config.navigator.language),
        ("screen.width", _dig(measurement.axes, ("screen", "width")), config.screen.width),
        ("screen.height", _dig(measurement.axes, ("screen", "height")), config.screen.height),
        (
            "devicePixelRatio",
            _dig(measurement.axes, ("screen", "devicePixelRatio")),
            config.screen.device_pixel_ratio,
        ),
        ("timezone", _dig(measurement.axes, ("timezone", "timeZone")), config.timezone.iana_name),
        (
            "webgl.renderer",
            _dig(measurement.axes, ("webgl", "unmaskedRenderer")),
            config.webgl.unmasked_renderer,
        ),
    ]

    wrong: list[str] = []
    for label, measured_value, declared_value in declared:
        if measured_value != declared_value:
            wrong.append(
                f"{label}: browser={measured_value!r} != profile={declared_value!r}"
            )

    assert wrong == [], (
        "the browser reported the host's values instead of the profile's: "
        + "; ".join(wrong)
    )


@pytest.mark.skipif(_BROWSER is None, reason=_SKIP_REASON)
def test_the_runtime_is_installed_at_document_start() -> None:
    """The harness has to run with the profile's runtime in place.

    Without this check every other test in this file could pass for the
    wrong reason: a browser that never received the runtime would still
    answer the harness, and the assertions would be comparing the host's
    readings against the simulator.  The runtime publishes the profile
    it was built from, and the harness' own identity axis proves the
    override is in force.
    """

    preset_id, seed = PRESETS[0]
    config = ProfileGenerator().generate(
        preset_id, name="E2E runtime", master_seed=seed
    )

    measurement = asyncio.run(_measure(config))

    measured_ua = _dig(measurement.axes, ("navigator", "userAgent"))
    assert measured_ua == config.navigator.user_agent

    # The stock Chrome on this machine is not an Android browser, so the
    # agreement above cannot be a coincidence of the host.
    assert "Android" in str(config.navigator.user_agent)
    assert measured_ua == config.navigator.user_agent


@pytest.mark.skipif(_BROWSER is None, reason=_SKIP_REASON)
def test_the_simulator_and_the_browser_cover_the_same_axes() -> None:
    """The two sides of the comparison have to describe the same world.

    The simulator and the harness publish the same ``nova.probe/1``
    schema.  If one of them grew an axis the other did not, the
    comparison would silently stop covering it, and the test would keep
    passing while measuring less and less.
    """

    preset_id, seed = PRESETS[0]
    config = ProfileGenerator().generate(
        preset_id, name="E2E axes", master_seed=seed
    )

    measurement = asyncio.run(_measure(config))
    simulated = simulate_payload(config).get("axes", {})

    browser_axes = set(measurement.axes)
    simulator_axes = set(simulated)

    missing_from_simulator = browser_axes - simulator_axes
    missing_from_browser = simulator_axes - browser_axes

    assert missing_from_simulator == set(), (
        "the simulator does not model these axes the browser reports: "
        + ", ".join(sorted(missing_from_simulator))
    )
    assert missing_from_browser == set(), (
        "the simulator models these axes the browser did not report: "
        + ", ".join(sorted(missing_from_browser))
    )

    # Every asserted axis must be present on both sides, otherwise the
    # comparison would be asserting nothing for it.
    for label, path in ASSERTED_AXES:
        assert _dig(simulated, path) is not None, (
            f"the simulator does not model the asserted axis {label!r}"
        )
        assert _dig(measurement.axes, path) is not None, (
            f"the browser did not report the asserted axis {label!r}"
        )
