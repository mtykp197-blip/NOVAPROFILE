"""The verification harness has to pass every axis against a real browser.

The harness is the only thing that says the profile and the browser
agree.  A harness that reports a disagreement where there is none is
as bad as one that misses a real one, so this runs the whole thing
against a live Chrome and asserts the outcome.

Several of the checks below are regressions.  Each of them was a way
for the browser to disagree with the profile while still *looking*
right:

- the device pixel ratio was read from the wrong key of the payload,
  so the browser kept the host's panel;
- the time zone was patched on ``Date`` but not on ``Intl``, so the
  formatter still named the host's zone;
- the graphics card was only patched on the WebGL 1 prototype, and a
  page that asks for a WebGL 2 context reads the host's card;
- the WebGPU adapter was not answered at all, so the host's adapter
  was exposed even though the profile carries no WebGPU;
- the motion events were never delivered, so the page that waited for
  one received the host's reading at the host's rate.
"""

from __future__ import annotations

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.probe.verify import VerifyRunner


def _config():
    """A real profile from the generator."""

    return ProfileGenerator().generate(
        "pixel-8-pro-android14", name="Verify Live", master_seed="c" * 64
    )


@pytest.fixture(scope="module")
def report():
    """The verification report from a real browser."""

    runner = VerifyRunner(_config(), headless=True, timeout=180.0)
    return runner.run_sync()


class TestTheHarnessPassesEveryAxis:
    """The whole point of the harness is that it can say yes."""

    def test_the_run_succeeded(self, report) -> None:
        assert report.error == "", report.error

    def test_the_runtime_was_installed(self, report) -> None:
        assert report.has_runtime is True

    def test_every_axis_was_reported(self, report) -> None:
        assert len(report.axes) == 21

    def test_no_axis_failed(self, report) -> None:
        failed = [item.axis for item in report.axes if item.verdict == "fail"]
        assert failed == [], f"these axes disagree with the profile: {failed}"

    def test_no_axis_is_unavailable(self, report) -> None:
        unavailable = [item.axis for item in report.axes if item.verdict == "unavailable"]
        assert unavailable == [], f"these axes were not compared: {unavailable}"

    def test_no_axis_depends_on_the_host(self, report) -> None:
        """A host-dependent axis is one the profile cannot settle.

        The profile describes a device, and the verification runs on a
        machine that is not that device.  An axis that still reads the
        machine -- the graphics card, the sensors -- is an axis where
        the browser and the profile cannot be made to agree, and that
        is a gap in the product rather than in the harness.
        """

        host = [item.axis for item in report.axes if item.verdict == "host-dependent"]
        assert host == [], f"these axes still read the host machine: {host}"

    def test_the_report_says_every_axis_passed(self, report) -> None:
        assert report.passed is True
        counts = report.counts()
        assert counts["pass"] == 21
        assert counts["fail"] == 0
        assert counts["host-dependent"] == 0
        assert counts["unavailable"] == 0


class TestTheAxesThatUsedToDisagree:
    """Each of these was a way for the host to show through."""

    def test_the_device_class_matches(self, report) -> None:
        """A 3x panel, not the host's 1x."""

        screen = next(item for item in report.axes if item.axis == "screen")
        ratio = next(f for f in screen.fields if f.field == "devicePixelRatio")
        assert ratio.verdict == "match"
        assert ratio.expected == 3
        assert ratio.actual == 3

    def test_the_time_zone_matches(self, report) -> None:
        """The zone has to come from the profile, not from the host.

        The offset and the name are two readings of one fact, so they
        have to agree with each other as well as with the profile.
        """

        zone = next(item for item in report.axes if item.axis == "timezone")
        name = next(f for f in zone.fields if f.field == "ianaName")
        assert name.verdict == "match"
        assert name.actual == name.expected

    def test_the_graphics_card_matches(self, report) -> None:
        """The vendor and the renderer are the loudest fingerprint.

        They are read through ``WEBGL_debug_renderer_info``, and a
        page that asks for a WebGL 2 context reads a different
        prototype than one that asks for a WebGL 1 context -- both
        have to be patched.
        """

        gl = next(item for item in report.axes if item.axis == "webgl")
        vendor = next(f for f in gl.fields if f.field == "unmaskedVendor")
        renderer = next(f for f in gl.fields if f.field == "unmaskedRenderer")
        assert vendor.verdict == "match"
        assert renderer.verdict == "match"
        assert "ARM" in str(vendor.actual)
        assert "Mali" in str(renderer.actual)

    def test_the_graphics_limits_match(self, report) -> None:
        """The limits are read through the enum values, not by name."""

        gl = next(item for item in report.axes if item.axis == "webgl")
        limit = next(f for f in gl.fields if f.field == "maxTextureSize")
        assert limit.verdict == "match"
        assert limit.actual == limit.expected

    def test_the_profile_carries_no_webgpu_and_the_browser_shows_none(
        self, report
    ) -> None:
        """A disabled surface must not expose the host's adapter.

        The profile says the device does not carry WebGPU.  A browser
        that answers ``requestAdapter`` with the host's card is
        telling the page that the profile is a lie.
        """

        gpu = next(item for item in report.axes if item.axis == "webgpu")
        adapter = next(f for f in gpu.fields if f.field == "adapterIdentity")
        assert adapter.verdict == "match"
        assert "no adapter" in str(adapter.actual)

    def test_the_motion_reading_matches(self, report) -> None:
        """The motion event has to be delivered, with the profile's rate.

        A browser without sensor hardware never delivers the event at
        all, so a page that waits for one has to receive the
        profile's reading at the profile's rate.
        """

        sensors = next(item for item in report.axes if item.axis == "sensors")
        rate = next(f for f in sensors.fields if f.field == "samplingHz")
        reading = next(f for f in sensors.fields if f.field == "accelerationZ")
        assert rate.verdict == "match"
        assert reading.verdict == "match"
        assert rate.actual == 100

    def test_the_client_hints_match(self, report) -> None:
        """The version list is read through a method, not an accessor.

        ``getHighEntropyValues`` answers with the host's version, so
        the profile's version has to replace it -- and the accessor
        that reads the same list has to agree with it.
        """

        hints = next(item for item in report.axes if item.axis == "client_hints")
        versions = next(f for f in hints.fields if f.field == "fullVersionList")
        assert versions.verdict == "match"
        assert versions.actual == versions.expected
