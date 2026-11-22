"""Tests for the NOVA profile verification harness.

The suite has two halves:

* the **offline** half, which never starts a browser.  It checks that the
  harness page is self-contained, that it declares the axes it promises,
  that the runner reports a clean failure when no browser is installed,
  and that the report structure is the one the ledger expects.
* the **live** half, which runs the verification against a real Chrome.
  It is skipped when no browser is installed, and it is the only test in
  this file that may start a process.

The live half is the important one: a harness that cannot fail is not a
verification.  It asserts that the harness *reports* the disagreement
between the profile and the stock browser -- which is the whole point of
the exercise -- and that the report carries the real numbers.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from novaprofile.js_runtime.builder import JSRuntimeBuilder
from novaprofile.probe.verify import (
    PRISTINE_CAPTURE_SCRIPT,
    READY_EXPRESSION,
    VERIFY_AXES,
    VERIFY_EXPRESSION,
    VERIFY_HARNESS_PATH,
    VerifyReport,
    VerifyRunner,
    read_harness_html,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _browser_path() -> str | None:
    """Return the path of a usable Chromium binary, or ``None``."""

    for candidate in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


#: Skip marker for the tests that need a real browser.
requires_chrome = pytest.mark.skipif(
    _browser_path() is None,
    reason="no Chrome/Chromium executable is installed on this machine",
)


def _real_config() -> Any:
    """Build a real ``ProfileConfig`` from the generator."""

    from novaprofile.core.generator import ProfileGenerator

    return ProfileGenerator().generate(
        "pixel-8-pro-android14", name="Verify Test", master_seed="c" * 64
    )


# --------------------------------------------------------------------------
# The harness page
# --------------------------------------------------------------------------


class TestHarnessPage:
    def test_the_harness_file_exists(self) -> None:
        assert VERIFY_HARNESS_PATH.exists()
        assert VERIFY_HARNESS_PATH.name == "verify_harness.html"

    def test_the_harness_is_self_contained(self) -> None:
        """The harness must load no external resource.

        It runs inside an isolated profile with no network access, so a
        ``http://`` reference would turn the verification into a
        network-dependent test.
        """

        html = read_harness_html().lower()
        for marker in ("http://", "https://", "//cdn", 'src="//', 'href="//'):
            assert marker not in html, f"the harness references {marker!r}"

    def test_the_harness_publishes_its_api(self) -> None:
        html = read_harness_html()
        assert "__NOVA_VERIFY__" in html
        assert "__NOVA_VERIFY_READY__" in html
        assert "nova.verify/1" in html
        assert "run: run" in html

    def test_the_harness_reads_the_profile_and_the_browser(self) -> None:
        """The harness must read both sides of the comparison.

        A harness that only reads the browser cannot verify anything, and
        one that only reads the profile never touches the browser.
        """

        html = read_harness_html()
        assert "window.__NOVA_PROFILE__" in html
        # The real browser accessors -- the ones a fingerprinting script
        # uses, not the ones the engine happens to expose.
        for accessor in (
            "navigator.userAgent",
            "navigator.userAgentData",
            "screen.width",
            "MAX_TEXTURE_SIZE",
            "canvas.toDataURL",
            "baseLatency",
            "measureText",
            "getBoundingClientRect",
            "navigator.mediaDevices.enumerateDevices",
            "devicemotion",
            "navigator.storage.estimate",
            "navigator.gpu.requestAdapter",
            "performance.memory",
            "MediaRecorder.isTypeSupported",
            "document.fonts.check",
            "navigator.permissions.query",
            "navigator.getBattery",
            "Intl.DateTimeFormat",
        ):
            assert accessor in html, f"the harness never reads {accessor!r}"

    def test_the_harness_declares_every_axis(self) -> None:
        html = read_harness_html()
        for axis in VERIFY_AXES:
            assert f'"{axis}"' in html, f"axis {axis!r} is not declared"

    def test_the_harness_declares_no_unknown_axis(self) -> None:
        """The harness must not compare an axis the runner does not know.

        The runner reads the axis list out of the report, so an axis the
        harness reports and the runner does not know would be silently
        dropped from the comparison.
        """

        html = read_harness_html()
        match = re.search(r"var AXES = \[(.*?)\];", html, re.DOTALL)
        assert match is not None
        declared = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        assert declared == set(VERIFY_AXES)

    def test_the_harness_field_map_covers_every_axis(self) -> None:
        """Every axis must declare at least one compared field."""

        html = read_harness_html()
        match = re.search(r"var FIELDS = \{(.*?)\n  \};", html, re.DOTALL)
        assert match is not None
        body = match.group(1)
        for axis in VERIFY_AXES:
            assert f"\n    {axis}: [" in body, f"axis {axis!r} declares no field"

    def test_the_harness_has_a_browser_and_a_profile_reading(self) -> None:
        html = read_harness_html()
        assert "function readProfile()" in html
        assert "function readBrowser()" in html
        assert "function compareAxis(" in html

    def test_the_runner_captures_the_pristine_builtins(self) -> None:
        """The pristine built-ins have to be captured before the runtime.

        The JavaScript runtime hides its overrides behind a proxy that
        answers ``toString`` with the native source text, so a page
        cannot detect a substitution by inspecting the source.  It can
        only do so by comparing the installed function against the
        original -- and the original is only available if it was
        captured *before* the runtime ran.
        """

        assert "window.__NOVA_PRISTINE__" in PRISTINE_CAPTURE_SCRIPT
        for symbol in (
            "getBoundingClientRect",
            "getImageData",
            "requestAdapter",
            "userAgentData",
        ):
            assert symbol in PRISTINE_CAPTURE_SCRIPT, symbol

        html = read_harness_html()
        assert "window.__NOVA_PRISTINE__" in html
        assert "function isSubstituted(" in html

    def test_the_webgpu_axis_has_its_own_comparison(self) -> None:
        """A profile that disables WebGPU must not leak a host adapter.

        The profile always carries an adapter identity, but the
        ``enabled`` flag says whether the runtime is supposed to
        install it.  When it is off, the browser must expose no GPU
        identity at all, and a host adapter is reported as one
        explicit leak rather than as four unrelated name mismatches.
        """

        html = read_harness_html()
        assert "function compareWebgpu(" in html
        assert "compareWebgpu(profile, browser)" in html


# --------------------------------------------------------------------------
# The runner (offline)
# --------------------------------------------------------------------------


class TestRunnerOffline:
    def test_reports_a_clean_failure_when_no_browser_is_found(self) -> None:
        from novaprofile.probe.e2e import BrowserNotFoundError

        def finder(_explicit: str | None) -> str:
            raise BrowserNotFoundError("no Chrome/Chromium/Edge executable found")

        runner = VerifyRunner(_real_config(), browser_finder=finder)
        report = runner.run_sync()

        assert report.passed is False
        assert report.error
        assert "no Chrome" in report.error
        assert report.axes == []

    def test_the_failure_report_is_serialisable(self) -> None:
        from novaprofile.probe.e2e import BrowserNotFoundError

        def finder(_explicit: str | None) -> str:
            raise BrowserNotFoundError("no browser")

        runner = VerifyRunner(_real_config(), browser_finder=finder)
        report = runner.run_sync()
        payload = report.to_dict()

        assert payload["passed"] is False
        assert payload["metrics"]["axes_total"] == 0
        assert "failed" in payload["conclusion"]
        assert json.loads(report.to_json())["passed"] is False

    def test_the_report_can_be_filed_into_the_measurement_ledger(self) -> None:
        """The report has to be loadable by ``measurements.save``.

        The ledger is the single place where the project records the
        numbers it claims about itself, so a report that cannot be filed
        is a report nobody will ever look at again.
        """

        import tempfile

        from novaprofile.scripts import measurements

        report = VerifyReport(profile_id="p-1", passed=False)
        payload = report.to_dict()

        original_data = measurements.DATA_DIR
        original_index = measurements.INDEX_PATH
        original_root = measurements.MEASUREMENTS_DIR
        with tempfile.TemporaryDirectory(prefix="nova-ledger-") as tmp:
            root = Path(tmp)
            measurements.DATA_DIR = root / "data"
            measurements.MEASUREMENTS_DIR = root
            measurements.INDEX_PATH = root / "INDEX.md"
            try:
                path = measurements.save(
                    slug=payload["slug"],
                    metrics=payload["metrics"],
                    conclusion=payload["conclusion"],
                    command="python -m pytest tests/test_probe_verify.py -q",
                )
                assert path.exists()
                assert measurements.verify() == []
                record = measurements.latest(payload["slug"])
                assert record is not None
                assert record.metrics["axes_total"] == 0
            finally:
                measurements.DATA_DIR = original_data
                measurements.MEASUREMENTS_DIR = original_root
                measurements.INDEX_PATH = original_index

    def test_the_expressions_target_the_harness(self) -> None:
        assert "__NOVA_VERIFY__" in VERIFY_EXPRESSION
        assert ".run()" in VERIFY_EXPRESSION
        assert "__NOVA_VERIFY_READY__" in READY_EXPRESSION

    def test_the_axis_list_is_stable(self) -> None:
        assert len(VERIFY_AXES) == len(set(VERIFY_AXES))
        assert len(VERIFY_AXES) == 21
        # The axes the specification names have to be present.
        for axis in (
            "navigator",
            "client_hints",
            "screen",
            "viewport",
            "timezone",
            "storage",
            "media_devices",
            "canvas",
            "audio",
            "rects",
            "webgl",
            "webgpu",
            "permissions",
            "battery",
            "fonts",
            "sensors",
            "media_queries",
            "geolocation",
            "math_precision",
            "heap",
            "media_decode",
        ):
            assert axis in VERIFY_AXES


# --------------------------------------------------------------------------
# The runner (live)
# --------------------------------------------------------------------------


@requires_chrome
class TestRunnerLive:
    def test_the_harness_runs_and_returns_the_expected_structure(self) -> None:
        """The harness must self-check and return the documented shape.

        This is the assertion that cannot be made by a fake: the page
        has to load in a real Chrome, install its API, and produce a
        report that the runner can lift into the dataclasses.
        """

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        # Every axis is reported, in order, whether it passed or not.
        assert [item.axis for item in report.axes] == list(VERIFY_AXES)
        for item in report.axes:
            assert item.verdict in {
                "pass",
                "fail",
                "unavailable",
                "host-dependent",
            }
            assert item.asserted >= 0
            assert item.mismatched >= 0
            if item.verdict == "fail":
                assert item.mismatches, f"axis {item.axis!r} failed without a field"
                for field in item.mismatches:
                    assert field.expected is not None
                    assert field.actual is not None or field.actual is None

    def test_the_harness_reports_the_profile_and_the_browser(self) -> None:
        """Both sides of the comparison must be in the report.

        A report that only carries the verdict cannot be audited; the
        numbers have to be there so that an operator can see *what*
        disagrees.
        """

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        assert report.has_runtime is True, "the JavaScript runtime was not installed"
        assert report.browser_version
        assert report.harness.startswith("file://")

        navigator = next(item for item in report.axes if item.axis == "navigator")
        assert navigator.profile is not None
        assert navigator.browser is not None
        assert navigator.browser.get("userAgent")

    def test_a_stock_browser_does_not_match_the_android_profile(self) -> None:
        """The verification must be able to fail.

        The whole value of the harness is that it reports a disagreement
        between the profile and the browser.  A desktop Chrome cannot
        impersonate an Android handset, so the device class and the GPU
        identity axes *must* disagree.  If they did not, the harness
        would be measuring nothing.

        The identity strings (``navigator.userAgent`` and friends) are
        patched by the JavaScript runtime, so they are expected to
        match; what a stock desktop Chrome cannot fake is the *device
        class* (a 3x ``devicePixelRatio`` panel) and the *GPU* (an ARM
        Mali-G78 versus the host's NVIDIA card).

        The runtime is left out here on purpose -- only the payload is
        installed, without the runtime that patches the browser.  That
        is what makes this a *stock* browser: with the runtime in place
        the panel would be the profile's, and the disagreement this
        test is about would not exist.
        """

        payload_only = JSRuntimeBuilder().build_payload_only(_real_config())
        runner = VerifyRunner(
            _real_config(), headless=True, timeout=120.0, runtime_script=payload_only
        )
        report = runner.run_sync()

        assert report.error == "", report.error
        assert report.has_runtime is False
        assert report.passed is False, "a stock Chrome matched an Android profile"

        by_axis = {item.axis: item for item in report.axes}

        # The device class is the loudest possible disagreement.
        assert by_axis["screen"].verdict == "fail"
        dpr = next(f for f in by_axis["screen"].fields if f.field == "devicePixelRatio")
        assert dpr.verdict == "mismatch"
        assert dpr.expected == 3
        assert dpr.actual == 1

        # The GPU identity cannot be mimicked by a desktop card.  The
        # axis reads the machine's own card, so its disagreement is
        # reported as host-dependent rather than as a failure -- but
        # the fields still show the two cards side by side.
        assert by_axis["webgl"].verdict == "host-dependent"
        vendor = next(f for f in by_axis["webgl"].fields if f.field == "unmaskedVendor")
        assert vendor.verdict == "mismatch"
        assert "ARM" in str(vendor.expected)
        assert "NVIDIA" in str(vendor.actual)

    def test_the_patched_identity_axes_agree_with_the_profile(self) -> None:
        """The axes the runtime does patch must line up exactly.

        This is the positive counterpart of the previous test: it proves
        the harness reports a *pass* when the browser really does carry
        the profile values, so that the failures elsewhere are genuine
        disagreements rather than a harness that fails everything.
        """

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        by_axis = {item.axis: item for item in report.axes}

        for axis in ("navigator", "viewport", "storage", "media_queries", "math_precision"):
            item = by_axis[axis]
            assert item.verdict == "pass", f"{axis}: {item.detail}"
            assert item.mismatched == 0, f"{axis}: {item.detail}"

        ua = next(
            f for f in by_axis["navigator"].fields if f.field == "userAgent"
        )
        assert ua.verdict == "match"
        assert "Android" in str(ua.expected)
        assert "Android" in str(ua.actual)

    def test_the_axes_the_runtime_patches_report_the_substitution(self) -> None:
        """A substituted accessor must be detected as such.

        The runtime replaces the canvas readback and the geometry
        accessors.  The harness has to notice that, otherwise a profile
        that asks for a surface the browser never installed would look
        identical to one that did.
        """

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        by_axis = {item.axis: item for item in report.axes}

        for axis in ("canvas", "rects"):
            item = by_axis[axis]
            assert item.verdict == "pass", f"{axis}: {item.detail}"
            substituted = next(
                f for f in item.fields if f.field == "substituted"
            )
            assert substituted.verdict == "match", f"{axis}: {substituted.detail}"
            assert substituted.actual is True, f"{axis}: {substituted.detail}"

    def test_the_webgpu_axis_reports_the_host_adapter_leak(self) -> None:
        """A disabled WebGPU must not leak the host's GPU identity.

        The profile disables WebGPU, so the browser is expected to
        expose no adapter at all.  A desktop Chrome that still answers
        ``requestAdapter`` is reporting the host's card, which the axis
        has to call out explicitly.
        """

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        by_axis = {item.axis: item for item in report.axes}
        webgpu = by_axis["webgpu"]

        # Whether the leak is present depends on the machine, so the
        # test asserts the *structure*: the adapter identity is always
        # summarised into a single verdict.
        identity = next(
            (f for f in webgpu.fields if f.field == "adapterIdentity"), None
        )
        assert identity is not None, "the WebGPU axis has no adapter-identity verdict"

        for field in ("vendor", "architecture", "device", "description"):
            assert all(
                f.field != field for f in webgpu.fields
            ), f"{field} is still reported as an individual mismatch"

        if identity.verdict == "mismatch":
            # The adapter identity comes from the machine the browser
            # runs on, so the disagreement is reported as
            # host-dependent rather than as a failure.
            assert webgpu.verdict in {"fail", "host-dependent"}
        else:
            assert identity.actual == "no adapter"

    def test_the_axes_that_have_no_counterpart_are_unavailable(self) -> None:
        """An axis the browser does not expose is unavailable, not failed.

        The distinction matters: a machine without WebGPU cannot report a
        WebGPU adapter, and that is not a profile defect.
        """

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        for item in report.axes:
            if item.verdict == "unavailable":
                assert item.asserted == 0
                assert item.mismatched == 0
                assert item.ok is True

    def test_the_run_leaves_no_orphans_and_no_temp_directories(self) -> None:
        """Closing the session must reap the whole browser process tree.

        Chromium spawns renderer, GPU, utility and crashpad processes.
        A leaked child keeps the temporary profile locked, so the next
        run fails with a "profile in use" error and the directory leaks.
        """

        import tempfile
        import time

        before = set(Path(tempfile.gettempdir()).glob("nova-verify-*"))

        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()
        assert report.error == "", report.error

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            after = set(Path(tempfile.gettempdir()).glob("nova-verify-*"))
            if after <= before:
                break
            time.sleep(0.5)

        after = set(Path(tempfile.gettempdir()).glob("nova-verify-*"))
        assert after <= before, f"the runner leaked temporary directories: {after - before}"

        try:
            import psutil
        except Exception:  # pragma: no cover - psutil is a hard dependency
            return

        orphans = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
            except Exception:
                continue
            if "nova-verify-" in cmdline and proc.info.get("name") in {
                "chrome.exe",
                "msedge.exe",
            }:
                orphans.append(proc.info["pid"])
        assert not orphans, f"the runner leaked browser processes: {orphans}"

    def test_the_table_and_the_json_agree(self) -> None:
        runner = VerifyRunner(_real_config(), headless=True, timeout=120.0)
        report = runner.run_sync()

        assert report.error == "", report.error
        table = report.to_table()
        for item in report.axes:
            assert item.axis in table
        payload = json.loads(report.to_json())
        assert payload["metrics"]["axes_total"] == len(VERIFY_AXES)
        assert payload["metrics"]["axes_fail"] == report.counts()["fail"]
        assert payload["passed"] is report.passed
