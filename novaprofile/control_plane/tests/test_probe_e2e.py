"""Tests for the NOVA end-to-end verification harness.

The suite is deterministic, never requires a real browser and never
touches the network:

* the surface extractor is tested against the deterministic simulator,
  which is the reference side of the comparison;
* the comparison engine and the tolerance policy are pure functions and
  are tested directly;
* the DevTools client is tested against a hand-built RFC-6455 frame --
  no socket is ever opened;
* the runner is tested with a browser finder that always fails, which
  exercises the "no browser installed" path without launching one.

The live browser path is opt-in through ``NOVA_PROBE_LIVE=1`` and is the
only test in this file that may start a process.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from nova_probe_fixtures import make_config

from novaprofile.probe.e2e import (
    GUARDED_AXES,
    LIKELY_CAUSES,
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    READ_EXPRESSION,
    SURFACE_SPECS,
    BrowserNotFoundError,
    ComparisonEngine,
    DevToolsClient,
    E2EReport,
    E2ERunner,
    LocalTestServer,
    SurfaceExtractor,
    Tolerance,
    build_frame,
    compare_axis,
    derive_expected_values,
    find_browser,
    parse_frame,
    report_digest,
    tolerance_for,
)
from novaprofile.probe.simulator import simulate_payload

# --------------------------------------------------------------------------
# The mapping between the guarded axes and the signature parts
# --------------------------------------------------------------------------

#: Which ``sig_*`` key of ``ProfileConfig.signature_parts`` guards which
#: axis of the harness.
#:
#: The fourteen ``sig_*`` keys cover thirteen of the fifteen axes directly.
#: Two groupings need spelling out:
#:
#: * The two WebGL keys -- ``sig_webgl_draw`` (the pixels the GPU draws)
#:   and ``sig_webgl_params`` (the parameter table it advertises) --
#:   together form the single ``webgl`` axis: a real mismatch in either
#:   half invalidates the axis, so they are compared as one.
#: * ``sig_device`` is the device identity -- the build fingerprint, the
#:   serial *and* the screen geometry -- so it guards three axes of the
#:   harness: ``identity`` (the user agent and the navigator block),
#:   ``client_hints`` (the model and the platform version) and ``screen``
#:   (the panel size and the pixel ratio).
#:
#: Every other ``sig_*`` key maps onto exactly one axis.
SIGNATURE_PART_TO_AXIS: dict[str, str] = {
    "sig_canvas": "canvas",
    "sig_webgl_draw": "webgl",
    "sig_webgl_params": "webgl",
    "sig_rects": "rects",
    "sig_devices": "media_devices",
    "sig_audio": "audio",
    "sig_device": "identity",
    "sig_sensors": "sensors",
    "sig_storage": "storage",
    "sig_webgpu": "webgpu",
    "sig_fonts": "fonts",
    "sig_math": "math",
    "sig_heap": "heap",
    "sig_media_decode": "media_decode",
}

#: The axes that are guarded *through* ``sig_device`` rather than through
#: a signature key of their own.
AXES_GUARDED_THROUGH_DEVICE: frozenset[str] = frozenset({"client_hints", "screen"})


def _signature_keys() -> set[str]:
    """Return the keys of ``ProfileConfig.signature_parts``."""

    return set(_real_signature_parts().keys())


# --------------------------------------------------------------------------
# The surface extractor
# --------------------------------------------------------------------------


class TestSurfaceExtractor:
    def test_covers_exactly_the_fifteen_guarded_axes(self) -> None:
        assert len(GUARDED_AXES) == 15
        assert len(set(GUARDED_AXES)) == 15
        assert set(GUARDED_AXES) == {
            "identity",
            "client_hints",
            "screen",
            "webgl",
            "canvas",
            "audio",
            "fonts",
            "rects",
            "media_devices",
            "sensors",
            "storage",
            "webgpu",
            "math",
            "heap",
            "media_decode",
        }

    def test_every_axis_has_a_surface_spec(self) -> None:
        assert set(SURFACE_SPECS) == set(GUARDED_AXES)
        for axis in GUARDED_AXES:
            spec = SURFACE_SPECS[axis]
            assert spec.axis == axis
            assert spec.expression, f"axis {axis!r} has no JS expression"
            assert spec.description

    def test_the_axis_set_covers_every_signature_part(self) -> None:
        """Every ``sig_*`` key must be guarded by one of the fifteen axes.

        This is the invariant the task calls for: the axes the harness
        verifies are exactly the axes the collision guard protects.
        """

        signature_keys = set(_real_signature_parts().keys())
        assert signature_keys == set(SIGNATURE_PART_TO_AXIS)
        guarded = set(SIGNATURE_PART_TO_AXIS.values())
        # Every signature part maps onto an axis of the harness ...
        assert guarded <= set(GUARDED_AXES)
        # ... and the only axes that are not the target of a signature key
        # of their own are the two that are guarded through
        # ``sig_device`` -- the device identity.
        assert set(GUARDED_AXES) - guarded == set(AXES_GUARDED_THROUGH_DEVICE)

    def test_the_script_contains_the_documented_expressions(self) -> None:
        script = SurfaceExtractor(make_config()).script()
        # The concrete reads the axes use.
        assert "navigator.userAgent" in script
        assert "navigator.userAgentData" in script
        assert "screen.width" in script
        assert "MAX_TEXTURE_SIZE" in script
        assert "canvas.toDataURL" in script
        assert "baseLatency" in script
        assert "measureText" in script
        assert "getBoundingClientRect" in script
        assert "navigator.mediaDevices.enumerateDevices" in script
        assert "devicemotion" in script
        assert "navigator.storage.estimate" in script
        assert "navigator.gpu.requestAdapter" in script
        # The placeholder must have been substituted.
        assert "__NOVA_FONT_CANDIDATES__" not in script

    def test_the_read_expression_targets_the_injected_reader(self) -> None:
        assert "__NOVA_E2E__" in READ_EXPRESSION
        assert ".read()" in READ_EXPRESSION

    def test_expected_values_come_from_the_simulator(self) -> None:
        config = make_config()
        expected = derive_expected_values(config)
        simulator = simulate_payload(config)
        assert expected["identity"]["userAgent"] == simulator["axes"]["navigator"]["userAgent"]
        assert expected["screen"]["width"] == simulator["axes"]["screen"]["width"]
        assert expected["webgl"]["unmaskedRenderer"] == (
            simulator["axes"]["webgl"]["unmaskedRenderer"]
        )
        assert expected["canvas"]["hash"] == simulator["axes"]["canvas"]["hash"]
        assert expected["storage"]["quota"] == simulator["axes"]["storage"]["storage"]["quota"]

    def test_extract_marks_an_absent_surface_as_empty(self) -> None:
        extractor = SurfaceExtractor(make_config())
        measured = {
            "webgpu": {"available": False},
            "identity": {"available": True, "userAgent": "ua"},
            "screen": {"available": True, "width": 1080},
        }
        extracted = extractor.extract(measured)
        assert extracted["webgpu"] == {}
        assert extracted["identity"]["userAgent"] == "ua"
        # The axes that were not measured at all are empty too.
        assert extracted["canvas"] == {}

    def test_extract_keeps_every_axis(self) -> None:
        extractor = SurfaceExtractor(make_config())
        extracted = extractor.extract({})
        assert set(extracted) == set(GUARDED_AXES)
        assert all(value == {} for value in extracted.values())

    def test_extract_drops_the_available_flag(self) -> None:
        extractor = SurfaceExtractor(make_config())
        extracted = extractor.extract({"identity": {"available": True, "userAgent": "ua"}})
        assert "available" not in extracted["identity"]

    def test_extract_flattens_the_nested_containers(self) -> None:
        extractor = SurfaceExtractor(make_config())
        extracted = extractor.extract(
            {
                "webgl": {"available": True, "parameters": {"maxTextureSize": 16384}},
                "webgpu": {
                    "available": True,
                    "info": {"vendor": "nvidia"},
                    "limits": {"maxBindGroups": 4},
                },
                "sensors": {
                    "available": True,
                    "motionSample": {
                        "interval": 16,
                        "accelerationIncludingGravity": {"x": 0, "y": 0, "z": 9.81},
                    },
                },
                "storage": {
                    "available": True,
                    "storage": {"quota": 1024},
                    "connection": {"rtt": 50},
                },
            }
        )
        assert extracted["webgl"]["maxTextureSize"] == 16384
        assert "parameters" not in extracted["webgl"]
        assert extracted["webgpu"]["vendor"] == "nvidia"
        assert extracted["webgpu"]["maxBindGroups"] == 4
        assert extracted["sensors"]["gravityZ"] == 9.81
        assert extracted["sensors"]["samplingInterval"] == 16
        assert extracted["storage"]["quota"] == 1024
        assert extracted["storage"]["rtt"] == 50

    def test_the_flattened_names_match_the_expected_names(self) -> None:
        """Every asserted field must exist on both sides of the comparison.

        A field that the browser reports but the simulator does not would
        be silently ignored, and a field that the simulator declares but
        the browser does not report would be a permanent false mismatch.
        The two sets have to line up.
        """

        config = make_config()
        expected = derive_expected_values(config)
        extractor = SurfaceExtractor(config)
        # The browser payload of a machine that exposes everything.
        measured = {
            axis: {"available": True, **_sample_payload(axis)} for axis in GUARDED_AXES
        }
        actual = extractor.extract(measured)
        for axis in GUARDED_AXES:
            asserted = {k for k, v in expected[axis].items() if v is not None}
            measured_fields = set(actual[axis])
            assert asserted <= measured_fields, (
                f"axis {axis!r}: the simulator asserts {sorted(asserted - measured_fields)}, "
                "which the extractor never measures"
            )

    def test_two_configs_produce_different_expectations(self) -> None:
        first = derive_expected_values(make_config())
        second_config = make_config()
        second_config.seed_config.master_seed = "b" * 64
        second = derive_expected_values(second_config)
        # The identity stays the same, the noise axes move.
        assert first["identity"] == second["identity"]
        assert first["canvas"] != second["canvas"]


def _real_signature_parts() -> dict[str, list[str]]:
    """Return ``ProfileConfig.signature_parts`` of a real profile.

    The method is an instance method, so a stand-in object is used when
    the real ``ProfileConfig`` cannot be instantiated without a
    generator.
    """

    from novaprofile.schema.profile import ProfileConfig

    config = _real_config()
    if config is not None:
        return config.signature_parts()

    # The real ProfileConfig needs a full device preset.  The keys of the
    # method are static, so a minimal object that calls the unbound
    # method is enough to read them.
    class _Stub:
        def signature_parts(self) -> dict[str, list[str]]:
            return ProfileConfig.signature_parts(self)  # type: ignore[arg-type]

    return ProfileConfig.signature_parts(_Stub())  # type: ignore[arg-type]


def _real_config() -> Any:
    """Build a real ``ProfileConfig`` or return ``None``."""

    try:
        from novaprofile.core.generator import ProfileGenerator
    except Exception:
        return None
    try:
        generator = ProfileGenerator()
        return generator.generate(
            "pixel-7-android14", name="E2E Test", master_seed="a" * 64
        )
    except Exception:
        return None


#: A browser payload that exercises every field the extractor measures.
#:
#: The values are arbitrary -- the test only checks that the *field
#: names* line up between the two sides of the comparison.
_SAMPLE_PAYLOAD: dict[str, dict[str, Any]] = {
    "identity": {
        "userAgent": "ua",
        "appVersion": "av",
        "appName": "an",
        "appCodeName": "ac",
        "product": "p",
        "productSub": "ps",
        "vendor": "v",
        "vendorSub": "vs",
        "platform": "pl",
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "maxTouchPoints": 5,
        "language": "en-US",
        "webdriver": False,
        "pdfViewerEnabled": True,
    },
    "client_hints": {
        "platform": "Android",
        "platformVersion": "14.0.0",
        "architecture": "arm",
        "bitness": "64",
        "model": "Pixel 7",
        "mobile": True,
        "wow64": False,
        "brands": [{"brand": "Chromium", "version": "126"}],
    },
    "screen": {
        "width": 1080,
        "height": 2400,
        "availWidth": 1080,
        "availHeight": 2400,
        "colorDepth": 24,
        "pixelDepth": 24,
        "devicePixelRatio": 2.625,
        "innerWidth": 412,
        "innerHeight": 915,
    },
    "webgl": {
        "unmaskedVendor": "Google Inc. (ARM)",
        "unmaskedRenderer": "ANGLE (ARM, Mali-G78)",
        "version": "WebGL 2.0",
        "shadingLanguageVersion": "WebGL GLSL ES 3.00",
        "parameters": {
            "maxTextureSize": 16384,
            "maxVaryingVectors": 15,
            "maxFragmentUniformVectors": 224,
            "maxVertexUniformVectors": 256,
            "maxTextureImageUnits": 16,
        },
    },
    "canvas": {"hash": "aaaa", "dataUrlHash": "bbbb"},
    "audio": {
        "sampleRate": 44100,
        "length": 44100,
        "numberOfChannels": 1,
        "mean": 0.2,
        "peak": 0.4,
        "sum": 100.0,
        "baseLatency": 0.01,
        "outputLatency": 0.0,
        "sampleRateLive": 44100,
    },
    "fonts": {"widths": {}, "genericWidths": {}, "installed": ["Roboto"]},
    "rects": {
        "host": {"x": 1.0, "y": 2.0},
        "inner": {"x": 3.0, "y": 4.0},
        "range": {"x": 5.0, "y": 6.0},
        "measureText": {"width": 10.0},
        "devicePixelRatio": 2.625,
    },
    "media_devices": {
        "devices": [{"deviceId": "d", "kind": "audioinput"}],
        "count": 2,
        "kinds": ["audioinput", "videoinput"],
    },
    "sensors": {
        "hasDeviceMotion": True,
        "hasDeviceOrientation": True,
        "motionSample": {
            "interval": 16.68,
            "acceleration": {"x": 0.0, "y": 0.0, "z": 0.0},
            "accelerationIncludingGravity": {"x": 0.0, "y": 0.0, "z": 9.81},
            "rotationRate": {"alpha": 0.0, "beta": 0.0, "gamma": 0.0},
        },
    },
    "storage": {
        "storage": {"available": True, "quota": 3221225472, "usage": 1024},
        "connection": {
            "effectiveType": "4g",
            "rtt": 50,
            "downlink": 10.0,
            "saveData": False,
            "type": "wifi",
        },
    },
    "webgpu": {
        "info": {
            "vendor": "ARM",
            "architecture": "Mali-G710 MC10",
            "device": "Tensor G2",
            "description": "Mali-G710 MC10",
        },
        "limits": {"maxTextureDimension2D": 8192},
        "features": [],
        "isFallbackAdapter": False,
    },
    "math": {
        "values": {
            "acos": 1.0471975511965979,
            "asin": 0.5235987755982989,
            "sinh": 0.5210953054937474,
            "cosh": 1.1276259652063807,
            "tanh": 0.46211715726000974,
            "atan": 0.4636476090008061,
        },
    },
    "heap": {
        "jsHeapSizeLimit": 2147483648,
    },
    "media_decode": {
        "supported": [
            'video/mp4;codecs="avc1.42E01E"',
            'video/webm;codecs="vp9"',
        ],
    },
}


def _sample_payload(axis: str) -> dict[str, Any]:
    """Return the sample browser payload of ``axis``."""

    return _SAMPLE_PAYLOAD[axis]


# --------------------------------------------------------------------------
# The comparison engine
# --------------------------------------------------------------------------


class TestComparisonEngine:
    def test_identical_values_match(self) -> None:
        result = compare_axis("identity", {"userAgent": "ua"}, {"userAgent": "ua"})
        assert result.verdict == "match"
        assert result.ok is True
        assert result.diagnostic
        assert result.likely_cause == ""

    def test_different_values_mismatch(self) -> None:
        result = compare_axis("identity", {"userAgent": "ua"}, {"userAgent": "other"})
        assert result.verdict == "mismatch"
        assert result.ok is False
        assert "identity" in result.diagnostic
        assert "ua" in result.diagnostic
        assert "other" in result.diagnostic

    def test_absent_surface_is_unavailable(self) -> None:
        result = compare_axis("webgpu", {}, {})
        assert result.verdict == "unavailable"
        # An unavailable surface is not a failure.
        assert result.ok is True

    def test_a_field_that_is_not_asserted_does_not_fail(self) -> None:
        result = compare_axis("identity", {"userAgent": None}, {"userAgent": "anything"})
        # The field is not asserted, so it cannot produce a mismatch --
        # but the axis as a whole asserts nothing either, so it is
        # reported as ``unavailable`` rather than as a false ``match``.
        assert result.verdict == "unavailable"
        assert result.ok is True
        assert result.fields[0].detail == "not asserted"
        assert result.fields[0].verdict == "match"

    def test_an_axis_with_one_asserted_field_reports_match(self) -> None:
        result = compare_axis(
            "identity", {"userAgent": "ua", "platform": None}, {"userAgent": "ua", "platform": "x"}
        )
        assert result.verdict == "match"
        assert result.ok is True

    def test_numeric_and_string_representations_compare_equal(self) -> None:
        result = compare_axis("screen", {"width": 1080}, {"width": 1080.0})
        assert result.verdict == "match"

    def test_booleans_compare_as_booleans(self) -> None:
        result = compare_axis("identity", {"webdriver": False}, {"webdriver": False})
        assert result.verdict == "match"
        result = compare_axis("identity", {"webdriver": False}, {"webdriver": True})
        assert result.verdict == "mismatch"

    def test_container_fields_compare_recursively(self) -> None:
        expected = {"host": {"x": 1.0, "y": 2.0}}
        result = compare_axis("rects", expected, {"host": {"x": 1.0, "y": 2.0}})
        assert result.verdict == "match"
        result = compare_axis("rects", expected, {"host": {"x": 1.0, "y": 3.0}})
        assert result.verdict == "mismatch"

    def test_the_engine_reports_every_axis(self) -> None:
        engine = ComparisonEngine()
        results = engine.compare({}, {})
        assert [item.axis for item in results] == list(GUARDED_AXES)
        assert all(item.verdict == "unavailable" for item in results)

    def test_the_engine_mixes_verdicts(self) -> None:
        engine = ComparisonEngine()
        expected = {
            "identity": {"userAgent": "ua"},
            "screen": {"width": 1080},
            "webgpu": {},
        }
        actual = {
            "identity": {"userAgent": "ua"},
            "screen": {"width": 800},
            "webgpu": {},
        }
        results = engine.compare(expected, actual)
        verdicts = {item.axis: item.verdict for item in results}
        assert verdicts["identity"] == "match"
        assert verdicts["screen"] == "mismatch"
        assert verdicts["webgpu"] == "unavailable"

    def test_a_timing_field_is_never_compared(self) -> None:
        result = compare_axis(
            "audio",
            {"baseLatency": 0.01},
            {"baseLatency": 99.0},
        )
        assert result.verdict == "match"
        assert "timing" in result.fields[0].detail

    def test_the_report_serialises(self) -> None:
        engine = ComparisonEngine()
        results = engine.compare(
            {"identity": {"userAgent": "ua"}}, {"identity": {"userAgent": "x"}}
        )
        report = E2EReport(
            profile_id="p-1",
            passed=False,
            comparisons=results,
            browser="chrome.exe",
        )
        payload = report.to_dict()
        assert payload["passed"] is False
        assert payload["profile_id"] == "p-1"
        assert payload["metrics"]["axes_total"] == 15
        assert payload["metrics"]["axes_mismatch"] == 1
        assert payload["conclusion"]
        # The report must be JSON-serialisable -- it is filed into the
        # measurement ledger.
        text = report.to_json()
        assert json.loads(text)["profile_id"] == "p-1"
        assert report_digest(report)

    def test_the_report_can_be_filed_into_the_measurement_ledger(self) -> None:
        """The report has to be loadable by ``measurements.save``.

        The ledger is the single place where the project records the
        numbers it claims about itself, so a report that cannot be filed
        is a report nobody will ever look at again.
        """

        from novaprofile.scripts import measurements

        engine = ComparisonEngine()
        results = engine.compare(
            {"identity": {"userAgent": "ua"}}, {"identity": {"userAgent": "x"}}
        )
        report = E2EReport(
            profile_id="p-1",
            passed=False,
            comparisons=results,
            browser="chrome.exe",
        )
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
                    command="python -m pytest tests/test_probe_e2e.py -q",
                )
                assert path.exists()
                # The checksum of the written record must verify.
                assert measurements.verify() == []
                record = measurements.latest(payload["slug"])
                assert record is not None
                assert record.metrics["axes_total"] == 15
                assert record.metrics["axes_mismatch"] == 1
                assert record.reusable is True
            finally:
                measurements.DATA_DIR = original_data
                measurements.MEASUREMENTS_DIR = original_root
                measurements.INDEX_PATH = original_index


# --------------------------------------------------------------------------
# The tolerance policy
# --------------------------------------------------------------------------


class TestTolerancePolicy:
    def test_every_axis_has_a_tolerance(self) -> None:
        for axis in GUARDED_AXES:
            tolerance = tolerance_for(axis)
            assert tolerance.mode in {"exact", "relative", "ignore"}
            assert tolerance.reason, f"axis {axis!r} has no documented reason"

    def test_identity_screen_and_storage_are_exact(self) -> None:
        assert tolerance_for("identity").mode == "exact"
        assert tolerance_for("screen").mode == "exact"
        assert tolerance_for("storage").mode == "exact"

    def test_the_noise_axes_are_relative(self) -> None:
        assert tolerance_for("webgl").mode == "relative"
        assert tolerance_for("webgpu").mode == "relative"
        assert tolerance_for("audio").mode == "relative"
        assert tolerance_for("sensors").mode == "relative"

    def test_the_tolerance_accepts_legitimate_noise(self) -> None:
        # A real GPU reports a slightly different MAX_TEXTURE_SIZE.
        result = compare_axis("webgl", {"maxTextureSize": 16384}, {"maxTextureSize": 16400})
        assert result.verdict == "match"
        assert "tolerance" in result.fields[0].detail

    def test_the_tolerance_rejects_a_real_mismatch(self) -> None:
        # 4096 against 16384 is a factor of four -- far outside any noise.
        result = compare_axis("webgl", {"maxTextureSize": 16384}, {"maxTextureSize": 4096})
        assert result.verdict == "mismatch"

    def test_the_tolerance_does_not_swallow_an_exact_axis(self) -> None:
        result = compare_axis("screen", {"width": 1080}, {"width": 1081})
        assert result.verdict == "mismatch"

    def test_the_tolerance_does_not_swallow_a_near_miss_on_a_relative_axis(self) -> None:
        # 1 % of 16384 is ~164 -- a difference of 500 is a real difference.
        result = compare_axis("webgl", {"maxTextureSize": 16384}, {"maxTextureSize": 15884})
        assert result.verdict == "mismatch"

    def test_the_absolute_floor_protects_small_values(self) -> None:
        # A gravity reading of 9.81 may jitter by 5 %, but 0.0 is a real
        # difference and not noise.
        result = compare_axis("sensors", {"gravityZ": 9.81}, {"gravityZ": 0.0})
        assert result.verdict == "mismatch"

    def test_the_rects_tolerance_is_one_device_pixel(self) -> None:
        tolerance = tolerance_for("rects")
        assert tolerance.mode == "relative"
        assert tolerance.absolute == 1.0
        assert tolerance.relative == 0.0
        result = compare_axis("rects", {"width": 280.0}, {"width": 280.5})
        assert result.verdict == "match"
        result = compare_axis("rects", {"width": 280.0}, {"width": 284.0})
        assert result.verdict == "mismatch"

    def test_a_timing_value_is_ignored(self) -> None:
        tolerance = Tolerance("ignore", reason="the value depends on the machine")
        assert tolerance.allows(1.0, 1000.0) is True

    def test_an_unknown_axis_defaults_to_exact(self) -> None:
        assert tolerance_for("not-an-axis").mode == "exact"


# --------------------------------------------------------------------------
# The diagnostics
# --------------------------------------------------------------------------


class TestDiagnostics:
    def test_the_diagnostic_names_the_axis_and_both_values(self) -> None:
        result = compare_axis("identity", {"userAgent": "expected-ua"}, {"userAgent": "actual-ua"})
        assert "identity" in result.diagnostic
        assert "expected-ua" in result.diagnostic
        assert "actual-ua" in result.diagnostic

    def test_a_missing_substitution_names_the_native_core(self) -> None:
        result = compare_axis("identity", {"userAgent": "expected-ua"}, {"userAgent": "actual-ua"})
        assert result.likely_cause == "the native core did not substitute this accessor"

    def test_a_pixel_ratio_mismatch_names_the_pixel_ratio(self) -> None:
        result = compare_axis(
            "screen", {"devicePixelRatio": 2.625}, {"devicePixelRatio": 1.0}
        )
        assert result.verdict == "mismatch"
        assert "pixel ratio" in result.likely_cause

    def test_a_missing_runtime_names_the_document_start(self) -> None:
        result = compare_axis("rects", {"width": 280.0}, {"width": 0.0})
        assert result.verdict == "mismatch"
        assert "document_start" in result.likely_cause

    def test_every_axis_has_a_documented_cause(self) -> None:
        assert set(LIKELY_CAUSES) == set(GUARDED_AXES)
        for axis, cause in LIKELY_CAUSES.items():
            assert cause, f"axis {axis!r} has no documented cause"

    def test_the_cause_is_not_empty_for_every_mismatch(self) -> None:
        engine = ComparisonEngine()
        expected = {axis: {"probe": "expected"} for axis in GUARDED_AXES}
        actual = {axis: {"probe": "actual"} for axis in GUARDED_AXES}
        results = engine.compare(expected, actual)
        for item in results:
            if item.verdict == "mismatch":
                assert item.likely_cause


# --------------------------------------------------------------------------
# The DevTools client
# --------------------------------------------------------------------------


class TestDevToolsClient:
    def test_parses_a_hand_built_text_frame(self) -> None:
        payload = b'{"id":1,"result":{"ok":true}}'
        frame = build_frame(OP_TEXT, payload, mask=True)
        parsed, consumed = parse_frame(frame)
        assert parsed is not None
        assert consumed == len(frame)
        assert parsed.fin is True
        assert parsed.opcode == OP_TEXT
        assert parsed.masked is True
        assert parsed.payload == payload
        assert parsed.json() == {"id": 1, "result": {"ok": True}}

    def test_parses_a_short_frame(self) -> None:
        frame = build_frame(OP_TEXT, b"hi", mask=True)
        parsed, consumed = parse_frame(frame)
        assert parsed is not None
        assert consumed == len(frame)
        assert parsed.text() == "hi"

    def test_parses_a_medium_frame(self) -> None:
        payload = b"x" * 1000
        frame = build_frame(OP_TEXT, payload, mask=True)
        parsed, consumed = parse_frame(frame)
        assert parsed is not None
        assert consumed == len(frame)
        assert parsed.payload == payload

    def test_parses_a_large_frame(self) -> None:
        payload = b"y" * 70000
        frame = build_frame(OP_TEXT, payload, mask=True)
        parsed, consumed = parse_frame(frame)
        assert parsed is not None
        assert consumed == len(frame)
        assert len(parsed.payload) == 70000

    def test_parses_the_control_opcodes(self) -> None:
        for opcode in (OP_PING, OP_PONG, OP_CLOSE):
            frame = build_frame(opcode, b"", mask=True)
            parsed, _consumed = parse_frame(frame)
            assert parsed is not None
            assert parsed.opcode == opcode

    def test_an_incomplete_frame_yields_none(self) -> None:
        frame = build_frame(OP_TEXT, b"hello", mask=True)
        for cut in range(1, len(frame)):
            parsed, consumed = parse_frame(frame[:cut])
            assert parsed is None
            assert consumed == 0

    def test_an_empty_buffer_yields_none(self) -> None:
        parsed, consumed = parse_frame(b"")
        assert parsed is None
        assert consumed == 0

    def test_the_mask_is_applied_and_removed(self) -> None:
        payload = b"secret"
        key = b"\x01\x02\x03\x04"
        frame = build_frame(OP_TEXT, payload, mask=True, mask_key=key)
        # The bytes on the wire are not the payload.
        assert payload not in frame
        parsed, _consumed = parse_frame(frame)
        assert parsed is not None
        assert parsed.payload == payload

    def test_a_zero_mask_key_leaves_the_payload_readable(self) -> None:
        # A server may mask with zeroes; the parser has to cope with it.
        payload = b"secret"
        frame = build_frame(OP_TEXT, payload, mask=True)
        parsed, _consumed = parse_frame(frame)
        assert parsed is not None
        assert parsed.payload == payload

    def test_two_frames_are_read_in_sequence(self) -> None:
        buffer = build_frame(OP_TEXT, b"one", mask=True) + build_frame(OP_TEXT, b"two", mask=True)
        parsed, consumed = parse_frame(buffer)
        assert parsed is not None
        assert parsed.text() == "one"
        rest = buffer[consumed:]
        parsed2, consumed2 = parse_frame(rest)
        assert parsed2 is not None
        assert parsed2.text() == "two"
        assert consumed2 == len(rest)

    def test_a_reserved_bit_is_rejected(self) -> None:
        from novaprofile.probe.e2e import WebSocketProtocolError

        frame = bytearray(build_frame(OP_TEXT, b"x", mask=True))
        frame[0] |= 0x40
        with pytest.raises(WebSocketProtocolError):
            parse_frame(bytes(frame))

    def test_an_unknown_opcode_is_rejected(self) -> None:
        from novaprofile.probe.e2e import WebSocketProtocolError

        frame = bytearray(build_frame(OP_TEXT, b"x", mask=True))
        frame[0] = (frame[0] & 0xF0) | 0x5
        with pytest.raises(WebSocketProtocolError):
            parse_frame(bytes(frame))

    def test_a_malformed_mask_key_is_rejected(self) -> None:
        from novaprofile.probe.e2e import WebSocketProtocolError

        with pytest.raises(WebSocketProtocolError):
            build_frame(OP_TEXT, b"x", mask=True, mask_key=b"\x00")

    def test_the_client_does_not_connect_in_the_constructor(self) -> None:
        # Building the client must not touch the network.
        client = DevToolsClient("ws://127.0.0.1:1/devtools/browser/does-not-exist")
        assert client is not None


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


class TestRunner:
    def test_reports_a_clean_failure_when_no_browser_is_found(self) -> None:
        def finder(_explicit: str | None) -> str:
            raise BrowserNotFoundError("no Chrome/Chromium/Edge executable found")

        runner = E2ERunner(make_config(), browser_finder=finder)
        report = runner.run_sync()

        assert report.passed is False
        assert report.error
        assert "no Chrome" in report.error
        assert report.comparisons == []

    def test_the_failure_report_is_serialisable(self) -> None:
        def finder(_explicit: str | None) -> str:
            raise BrowserNotFoundError("no browser")

        runner = E2ERunner(make_config(), browser_finder=finder)
        report = runner.run_sync()
        payload = report.to_dict()
        assert payload["passed"] is False
        assert payload["metrics"]["axes_total"] == 0
        assert "failed" in payload["conclusion"]
        assert json.loads(report.to_json())["passed"] is False

    def test_the_finder_reports_the_paths_it_tried(self) -> None:
        with pytest.raises(BrowserNotFoundError) as excinfo:
            find_browser("definitely-not-a-browser-binary-xyz")
        assert "definitely-not-a-browser-binary-xyz" in str(excinfo.value)

    def test_the_local_server_serves_the_page_on_the_loopback(self) -> None:
        import urllib.request

        with LocalTestServer() as server:
            assert server.url.startswith("http://127.0.0.1:")
            with urllib.request.urlopen(server.url, timeout=5) as response:  # noqa: S310
                body = response.read().decode("utf-8")
            assert "NOVA e2e surface page" in body
            assert "nova-rects-probe" in body

    def test_the_local_server_refuses_other_routes(self) -> None:
        import urllib.error
        import urllib.request

        with LocalTestServer() as server, pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(  # noqa: S310
                server.url + "elsewhere",
                timeout=5,
            )


# --------------------------------------------------------------------------
# The live browser test (opt-in)
# --------------------------------------------------------------------------


def _live_enabled() -> bool:
    import os

    return os.environ.get("NOVA_PROBE_LIVE", "").strip() in {"1", "true", "TRUE", "yes"}


@pytest.mark.skipif(
    not _live_enabled(),
    reason="live browser e2e disabled; set NOVA_PROBE_LIVE=1 to enable",
)
def test_live_browser_matches_the_simulator() -> None:
    """Run the whole harness against a real Chromium.

    The live browser cannot impersonate the Android profile, so most axes
    are expected to mismatch -- the test asserts that the harness
    *reports* the disagreement rather than crashing.
    """

    runner = E2ERunner(make_config(), headless=True)
    report = runner.run_sync()
    assert report.error == "", report.error
    assert len(report.comparisons) == 12
    assert report.to_json()


@pytest.mark.skipif(
    not _live_enabled(),
    reason="live browser e2e disabled; set NOVA_PROBE_LIVE=1 to enable",
)
def test_live_browser_leaves_no_orphans_and_no_temp_directories() -> None:
    """Closing the session must reap the whole browser process tree.

    Chromium spawns renderer, GPU, utility and crashpad processes.  A
    leaked child keeps the temporary profile locked, so the next run
    fails with a "profile in use" error and the directory leaks.
    """

    import os
    import tempfile
    import time
    from pathlib import Path

    before = set(Path(tempfile.gettempdir()).glob("nova-e2e-*"))

    runner = E2ERunner(make_config(), headless=True)
    report = runner.run_sync()
    assert report.error == "", report.error

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        after = set(Path(tempfile.gettempdir()).glob("nova-e2e-*"))
        if after <= before:
            break
        time.sleep(0.5)

    after = set(Path(tempfile.gettempdir()).glob("nova-e2e-*"))
    assert after <= before, f"the harness leaked temporary directories: {after - before}"

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
        if "nova-e2e-" in cmdline and proc.info.get("name") in {
            "chrome.exe",
            "msedge.exe",
        }:
            orphans.append(proc.info["pid"])
    assert not orphans, f"the harness leaked browser processes: {orphans}"
    assert os.environ.get("NOVA_PROBE_LIVE") == "1"
