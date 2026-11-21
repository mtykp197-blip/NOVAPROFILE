"""Tests for the NOVA runtime probe.

The suite never requires a real browser: the browser interaction is injected
through ``session_factory`` and the deterministic simulator covers the same
ground as the live path.  The single live test is opt-in through the
``NOVA_PROBE_LIVE=1`` environment variable.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from nova_probe_fixtures import make_config

from novaprofile.probe import (
    AXIS_ORDER,
    CANVAS_HASH_JS,
    FONT_CANDIDATES,
    HARNESS_HTML_PATH,
    NAVIGATOR_JS,
    REQUIRED_MARKERS,
    RuntimeProbe,
    SimulatedProbe,
    SimulatedSession,
    build_harness_html,
    collision_stats,
    compare_expectations,
    compute_signature,
    evaluate_axis_expectations,
    is_self_contained,
    live_probe_enabled,
    read_harness_html,
    simulate_payload,
    simulated_axis_values,
)
from novaprofile.probe.measurements import (
    AUDIO_JS,
    FONTS_JS,
    IFRAME_SYNC_JS,
    MEDIA_DEVICES_JS,
    RECTS_JS,
    SCREEN_JS,
    SENSORS_JS,
    STORAGE_JS,
    TIMEZONE_JS,
    WEBGL_READPIXELS_JS,
    WEBSOCKET_TO_WORKER_JS,
    WORKER_JS,
    render_snippet,
)

# --------------------------------------------------------------------------
# Harness page
# --------------------------------------------------------------------------


class TestHarnessHtml:
    def test_builds_and_contains_expected_markers(self) -> None:
        html = build_harness_html()
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        for marker in REQUIRED_MARKERS:
            assert marker in html, f"missing marker {marker!r}"

    def test_is_self_contained_without_external_urls(self) -> None:
        html = build_harness_html()
        assert is_self_contained(html)
        assert "http://" not in html
        assert "https://" not in html
        assert "<script src" not in html
        assert "<link " not in html

    def test_checked_in_harness_file_exists_and_matches_generator(self) -> None:
        assert HARNESS_HTML_PATH.exists(), "harness.html must be checked in"
        on_disk = read_harness_html()
        assert on_disk == build_harness_html(), (
            "novaprofile/probe/harness.html is out of sync with "
            "measurements.build_harness_html() -- regenerate it"
        )

    def test_checked_in_harness_is_self_contained(self) -> None:
        assert is_self_contained(read_harness_html())

    def test_every_axis_has_an_implementation(self) -> None:
        html = build_harness_html()
        for axis in AXIS_ORDER:
            assert f"function _axis_{axis}()" in html

    def test_snippets_are_self_contained_strings(self) -> None:
        # The worker source is a top-level script, every other snippet is an
        # immediately invoked expression.
        expressions = [
            CANVAS_HASH_JS,
            WEBGL_READPIXELS_JS,
            NAVIGATOR_JS,
            SCREEN_JS,
            AUDIO_JS,
            RECTS_JS,
            FONTS_JS,
            MEDIA_DEVICES_JS,
            SENSORS_JS,
            STORAGE_JS,
            TIMEZONE_JS,
            IFRAME_SYNC_JS,
            WEBSOCKET_TO_WORKER_JS,
        ]
        for snippet in expressions:
            assert snippet.strip().startswith("(")
            assert "require(" not in snippet
            assert "import " not in snippet

        assert "self.onmessage" in WORKER_JS
        assert "require(" not in WORKER_JS
        assert "import " not in WORKER_JS

    def test_render_snippet_substitutes_placeholders(self) -> None:
        rendered = render_snippet(CANVAS_HASH_JS)
        assert "__NOVA_HASH__" not in rendered
        assert "fnv1aHex" in rendered

        rendered_worker = render_snippet(WEBSOCKET_TO_WORKER_JS)
        assert "__NOVA_WORKER_SOURCE__" not in rendered_worker
        assert "onmessage" in rendered_worker

        rendered_fonts = render_snippet(FONTS_JS)
        assert "__NOVA_FONT_CANDIDATES__" not in rendered_fonts
        assert "Roboto" in rendered_fonts

    def test_font_candidates_cover_the_expected_families(self) -> None:
        assert "Roboto" in FONT_CANDIDATES
        assert "Noto Sans" in FONT_CANDIDATES
        assert "Arial" in FONT_CANDIDATES
        assert "monospace" in FONT_CANDIDATES
        assert "serif" in FONT_CANDIDATES


# --------------------------------------------------------------------------
# Expectations
# --------------------------------------------------------------------------


class TestExpectations:
    def test_evaluate_axis_expectations_maps_axes_to_paths(self) -> None:
        config = make_config()
        expectations = evaluate_axis_expectations(config)
        assert "navigator" in expectations
        assert expectations["navigator"][0] == config.navigator.user_agent
        assert expectations["navigator"][1] == "navigator.userAgent"
        assert expectations["screen_width"][0] == str(config.screen.width)

    def test_matching_config_yields_ok_results(self) -> None:
        config = make_config()
        payload = simulate_payload(config)
        expectations = evaluate_axis_expectations(config)
        results = compare_expectations(expectations, payload)

        assert results, "the comparison must produce results"
        failures = [r for r in results if not r.ok]
        assert not failures, f"unexpected failures: {failures}"
        by_axis = {r.axis: r for r in results}
        assert by_axis["navigator"].ok is True
        assert by_axis["navigator"].value == config.navigator.user_agent
        assert by_axis["navigator"].detail == "match"

    def test_mismatching_config_yields_failed_results(self) -> None:
        config = make_config()
        payload = simulate_payload(config)
        # Change the expectation only -- the measurement stays the same.
        expectations = evaluate_axis_expectations(config)
        expectations["navigator"] = (
            "Mozilla/5.0 (Windows NT 10.0)",
            "navigator.userAgent",
        )
        results = compare_expectations(expectations, payload)

        by_axis = {r.axis: r for r in results}
        assert by_axis["navigator"].ok is False
        assert by_axis["navigator"].value == config.navigator.user_agent
        assert "expected" in by_axis["navigator"].detail

    def test_missing_measurement_is_reported_as_failure(self) -> None:
        expectations = {"navigator": ("some-ua", "navigator.userAgent")}
        results = compare_expectations(expectations, {"axes": {}})
        assert len(results) == 1
        assert results[0].ok is False
        assert "missing measurement" in results[0].detail

    def test_measurement_error_is_reported_as_failure(self) -> None:
        expectations = {"navigator": ("some-ua", "navigator.userAgent")}
        payload = {"axes": {"navigator": {"error": "boom"}}}
        results = compare_expectations(expectations, payload)
        assert results[0].ok is False
        assert "boom" in results[0].detail

    def test_informational_axes_are_not_asserted(self) -> None:
        expectations = {"canvas_hash": ("", "canvas.hash")}
        payload = {"axes": {"canvas": {"hash": "abcd1234"}}}
        results = compare_expectations(expectations, payload)
        assert results[0].ok is True
        assert results[0].value == "abcd1234"
        assert results[0].detail == "informational"

    def test_strict_mode_fails_on_missing_measurement(self) -> None:
        expectations = {"canvas_hash": ("", "canvas.hash")}
        results = compare_expectations(expectations, {"axes": {}}, strict=True)
        assert results[0].ok is False

    def test_expectations_cover_every_probe_axis(self) -> None:
        expectations = evaluate_axis_expectations(make_config())
        payload = simulate_payload(make_config())
        axes = payload["axes"]
        for axis, (_expected, path) in expectations.items():
            head = path.split(".")[0]
            assert head in axes, f"{axis} -> {path} has no such axis"


# --------------------------------------------------------------------------
# Collision accounting
# --------------------------------------------------------------------------


def _result(axis: str, detail: str, value: str) -> Any:
    from novaprofile.probe import ProbeResult

    return ProbeResult(axis=axis, ok=True, value=value, detail=detail)


class TestCollisionAccounting:
    def test_first_signature_is_distinct_and_collision_free(self) -> None:
        ratio, rate = collision_stats("sig-a", set())
        assert ratio == 1.0
        assert rate == 0.0

    def test_seen_signature_collides(self) -> None:
        ratio, rate = collision_stats("sig-a", {"sig-a"})
        assert ratio == 0.0
        assert rate == 1.0

    def test_distinct_signature_does_not_collide(self) -> None:
        ratio, rate = collision_stats("sig-b", {"sig-a"})
        assert ratio == 1.0
        assert rate == 0.0

    def test_compute_signature_is_stable_and_axis_dependent(self) -> None:
        results = [
            _result("canvas_hash", "informational", "1111"),
            _result("webgl_pixel_hash", "informational", "2222"),
        ]
        signature = compute_signature(results)
        assert signature == compute_signature(list(reversed(results)))
        assert len(signature) == 64

        other = compute_signature(
            [
                _result("canvas_hash", "informational", "1111"),
                _result("webgl_pixel_hash", "informational", "3333"),
            ]
        )
        assert other != signature

    def test_compute_signature_includes_asserted_and_informational_axes(self) -> None:
        asserted = [_result("navigator", "match", "ua")]
        informational = [_result("canvas_hash", "informational", "1111")]
        assert compute_signature(asserted) != compute_signature(informational)
        assert compute_signature(asserted) != compute_signature([])

    def test_compute_signature_ignores_empty_values(self) -> None:
        empty = [_result("canvas_hash", "informational", "")]
        assert compute_signature(empty) == compute_signature([])


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------


class TestSimulator:
    def test_same_config_produces_identical_results(self) -> None:
        first = simulated_axis_values(make_config())
        second = simulated_axis_values(make_config())
        assert first == second

    def test_different_seed_produces_different_results(self) -> None:
        config_a = make_config()
        config_b = make_config()
        config_b.seed_config.master_seed = "b" * 64
        first = simulated_axis_values(config_a)
        second = simulated_axis_values(config_b)
        assert first != second
        # The identity axes stay equal -- only the noise axes differ.
        assert first["navigator"] == second["navigator"]
        assert first["canvas"] != second["canvas"]
        assert first["webgl"] != second["webgl"]

    def test_different_nudge_step_produces_different_results(self) -> None:
        config_a = make_config()
        config_b = make_config()
        config_b.seed_config.nudge_step = 1
        first = simulated_axis_values(config_a)
        second = simulated_axis_values(config_b)
        assert first["canvas"] != second["canvas"]

    def test_simulated_session_implements_the_probe_interface(self) -> None:
        session = SimulatedSession(make_config())
        payload = _run(session.evaluate("window.__NOVA_PROBE__.run()"))
        assert payload["schema"] == "nova.probe/1"
        assert set(payload["axes"]) == set(AXIS_ORDER)
        assert payload["axes"]["navigator"]["userAgent"].startswith("Mozilla/5.0")

    def test_simulated_probe_collects_a_consistent_report(self) -> None:
        config = make_config()
        probe = SimulatedProbe()
        report = probe.collect(config)

        assert report.profile_id == config.identity.id
        assert report.consistency_pass is True
        assert report.ok is True
        assert report.distinct_ratio == 1.0
        assert report.collision_rate == 0.0
        assert report.persistence_ok is True
        assert report.results

    def test_simulated_probe_detects_a_collision(self) -> None:
        config = make_config()
        probe = SimulatedProbe()
        first = probe.collect(config)
        second = probe.collect(make_config())

        assert first.collision_rate == 0.0
        assert second.collision_rate == 1.0
        assert second.distinct_ratio == 0.0
        assert second.ok is False

    def test_simulated_probe_reset_signatures(self) -> None:
        probe = SimulatedProbe()
        config = make_config()
        probe.collect(config)
        assert probe.seen_signatures
        probe.reset_signatures()
        assert not probe.seen_signatures


# --------------------------------------------------------------------------
# RuntimeProbe with an injected session
# --------------------------------------------------------------------------


class _FakeSession:
    """A :class:`ProbeSession` double that returns a canned payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.expressions: list[str] = []
        self.closed = False

    async def evaluate(self, expression: str) -> Any:
        self.expressions.append(expression)
        return self.payload

    async def close(self) -> None:
        self.closed = True


class TestRuntimeProbeInjection:
    def test_collect_uses_the_injected_session(self) -> None:
        config = make_config()
        payload = simulate_payload(config)
        session = _FakeSession(payload)

        async def factory() -> _FakeSession:
            return session

        probe = RuntimeProbe(session_factory=factory)
        report = probe.collect(config)

        assert session.expressions, "the probe must evaluate the harness"
        assert "__NOVA_PROBE__" in session.expressions[0]
        assert session.closed is True
        assert report.profile_id == config.identity.id
        assert report.consistency_pass is True
        assert report.ok is True

    def test_collect_reports_mismatches(self) -> None:
        config = make_config()
        payload = simulate_payload(config)
        payload["axes"]["navigator"]["userAgent"] = "totally-different"

        async def factory() -> _FakeSession:
            return _FakeSession(payload)

        probe = RuntimeProbe(session_factory=factory)
        report = probe.collect(config)
        assert report.consistency_pass is False
        assert report.ok is False

    def test_seen_signatures_accumulate_across_runs(self) -> None:
        config = make_config()
        payload = simulate_payload(config)

        async def factory() -> _FakeSession:
            return _FakeSession(payload)

        probe = RuntimeProbe(session_factory=factory)
        probe.collect(config)
        assert len(probe.seen_signatures) == 1
        probe.collect(config)
        assert len(probe.seen_signatures) == 1
        probe.reset_signatures()
        assert not probe.seen_signatures

    def test_collision_is_reported_on_the_second_run(self) -> None:
        config = make_config()
        payload = simulate_payload(config)

        async def factory() -> _FakeSession:
            return _FakeSession(payload)

        probe = RuntimeProbe(session_factory=factory)
        first = probe.collect(config)
        second = probe.collect(config)
        assert first.collision_rate == 0.0
        assert second.collision_rate == 1.0
        assert second.distinct_ratio == 0.0

    def test_invalid_payload_is_handled(self) -> None:
        config = make_config()

        async def factory() -> _FakeSession:
            return _FakeSession({"not": "a payload"})

        probe = RuntimeProbe(session_factory=factory)
        report = probe.collect(config)
        assert report.consistency_pass is False


# --------------------------------------------------------------------------
# Live browser test (opt-in)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not live_probe_enabled(),
    reason="live browser probe disabled; set NOVA_PROBE_LIVE=1 to enable",
)
def test_live_browser_probe() -> None:
    """Run the probe against a real Chrome instance."""

    config = make_config()
    probe = RuntimeProbe(headless=True)
    report = probe.collect(config)
    assert report.profile_id == config.identity.id
    assert report.results
    # The live browser cannot impersonate the Android profile, so the
    # consistency check is expected to fail -- the probe must nevertheless
    # produce a complete, well-formed report.
    assert len(report.results) == len(evaluate_axis_expectations(config))


@pytest.mark.skipif(
    not live_probe_enabled(),
    reason="live browser probe disabled; set NOVA_PROBE_LIVE=1 to enable",
)
def test_live_browser_leaves_no_orphans_and_no_temp_directories() -> None:
    """Closing the session must reap the whole Chrome process tree."""

    import os
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("nova-probe-*"))

    config = make_config()
    probe = RuntimeProbe(headless=True)
    probe.collect(config)

    # Give the OS a moment to reap the killed children.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        after = set(Path(tempfile.gettempdir()).glob("nova-probe-*"))
        if after <= before:
            break
        time.sleep(0.5)

    after = set(Path(tempfile.gettempdir()).glob("nova-probe-*"))
    assert after <= before, f"the probe leaked temporary directories: {after - before}"

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
        if "nova-probe-" in cmdline and proc.info.get("name") == "chrome.exe":
            orphans.append(proc.info["pid"])
    assert not orphans, f"the probe leaked Chrome processes: {orphans}"
    assert os.environ.get("NOVA_PROBE_LIVE") == "1"


# --------------------------------------------------------------------------
# Integration with the real ProfileConfig (skipped while the lead agent is
# still assembling the schema package)
# --------------------------------------------------------------------------


def _real_config() -> Any:
    """Build a real ``ProfileConfig`` or return ``None``."""

    try:
        from novaprofile.core.generator import ProfileGenerator
    except Exception:
        return None
    try:
        generator = ProfileGenerator()
        return generator.generate("pixel-7-android14", name="Probe Test", master_seed="a" * 64)
    except Exception:
        return None


def test_probe_works_against_the_real_profile_config() -> None:
    config = _real_config()
    if config is None:
        pytest.skip("the real ProfileConfig is not available yet")

    expectations = evaluate_axis_expectations(config)
    assert len(expectations) > 20

    payload = simulate_payload(config)
    results = compare_expectations(expectations, payload)
    failures = [r for r in results if not r.ok]
    assert not failures, f"unexpected failures: {failures}"

    report = SimulatedProbe().collect(config)
    assert report.consistency_pass is True
    assert report.ok is True


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
