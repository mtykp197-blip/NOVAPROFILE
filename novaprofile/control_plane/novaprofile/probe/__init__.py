"""Runtime fingerprint probe for the NOVA profile engine.

The probe measures the fingerprint axes of a running browser (or of a
deterministic simulation thereof) and compares them against the expected
values of a :class:`~novaprofile.schema.profile.ProfileConfig`.

Modules
-------
``measurements``
    The JavaScript snippets that measure each axis inside the page.
``harness``
    Generation and on-disk storage of the static harness page.
``probe``
    The CDP client, the pure comparison logic and :class:`RuntimeProbe`.
``simulator``
    A deterministic, browser-free simulation used by CI.
``e2e``
    The end-to-end harness: it reads the twelve axes out of a real
    browser and compares them against the profile.
"""

from __future__ import annotations

from .e2e import (
    GUARDED_AXES,
    ComparisonEngine,
    E2EReport,
    SurfaceExtractor,
    compare_axis,
    derive_expected_values,
    tolerance_for,
)
from .harness import (
    HARNESS_HTML_PATH,
    REQUIRED_MARKERS,
    harness_path,
    is_self_contained,
    read_harness_html,
    write_harness_html,
)
from .measurements import (
    AUDIO_JS,
    AXIS_ORDER,
    AXIS_SNIPPETS,
    CANVAS_HASH_JS,
    FONT_CANDIDATES,
    FONTS_JS,
    IFRAME_SYNC_JS,
    MEDIA_DEVICES_JS,
    NAVIGATOR_JS,
    RECTS_JS,
    SCREEN_JS,
    SENSORS_JS,
    STORAGE_JS,
    TIMEZONE_JS,
    WEBGL_READPIXELS_JS,
    WEBSOCKET_TO_WORKER_JS,
    WORKER_JS,
    build_harness_html,
    render_snippet,
)
from .probe import (
    HARNESS_RUN_EXPRESSION,
    LIVE_ENV_VAR,
    CdpClient,
    CdpError,
    CdpSession,
    ProbeReport,
    ProbeResult,
    ProbeSession,
    RuntimeProbe,
    collision_stats,
    compare_expectations,
    compute_signature,
    evaluate_axis_expectations,
    find_free_port,
    live_probe_enabled,
)
from .simulator import (
    SimulatedProbe,
    SimulatedSession,
    simulate_payload,
    simulated_axis_values,
)

__all__ = [
    "AXIS_ORDER",
    "AXIS_SNIPPETS",
    "AUDIO_JS",
    "CANVAS_HASH_JS",
    "CdpClient",
    "CdpError",
    "CdpSession",
    "ComparisonEngine",
    "E2EReport",
    "FONT_CANDIDATES",
    "FONTS_JS",
    "GUARDED_AXES",
    "HARNESS_HTML_PATH",
    "HARNESS_RUN_EXPRESSION",
    "IFRAME_SYNC_JS",
    "LIVE_ENV_VAR",
    "MEDIA_DEVICES_JS",
    "NAVIGATOR_JS",
    "ProbeReport",
    "ProbeResult",
    "ProbeSession",
    "RECTS_JS",
    "REQUIRED_MARKERS",
    "RuntimeProbe",
    "SCREEN_JS",
    "SENSORS_JS",
    "STORAGE_JS",
    "SimulatedProbe",
    "SimulatedSession",
    "SurfaceExtractor",
    "TIMEZONE_JS",
    "WEBGL_READPIXELS_JS",
    "WEBSOCKET_TO_WORKER_JS",
    "WORKER_JS",
    "build_harness_html",
    "collision_stats",
    "compare_axis",
    "compare_expectations",
    "compute_signature",
    "derive_expected_values",
    "evaluate_axis_expectations",
    "find_free_port",
    "harness_path",
    "is_self_contained",
    "live_probe_enabled",
    "read_harness_html",
    "render_snippet",
    "simulate_payload",
    "simulated_axis_values",
    "tolerance_for",
    "write_harness_html",
]
