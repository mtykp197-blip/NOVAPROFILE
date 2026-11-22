"""Deterministic simulation of the browser for CI.

The simulator replaces the real Chrome session with a pure-Python model that
derives every measured axis from the :class:`~novaprofile.schema.profile.ProfileConfig`.
Its purpose is to let CI assert the *accounting* of the probe -- distinct
ratio, collision rate, consistency pass -- without a browser being installed.

Determinism guarantees:

* the same profile config always produces byte-identical measurements;
* two configs that differ only in their seed produce different measurements;
* the simulator never touches the network, the filesystem or the clock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .measurements import AXIS_ORDER
from .probe import (
    ProbeReport,
    collision_stats,
    compare_expectations,
    compute_signature,
    evaluate_axis_expectations,
)

#: Fields of the profile config that participate in the deterministic seed.
_SEED_FIELDS: tuple[str, ...] = (
    "seed_config.master_seed",
    "seed_config.seed_epoch",
    "seed_config.nudge_step",
    "identity.id",
    "navigator.user_agent",
    "screen.width",
    "screen.height",
    "webgl.unmasked_renderer",
    "timezone.iana_name",
)


def _getattr_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    return current


def _brands(config: Any, path: str) -> list[dict[str, str]]:
    """Return the brand list of a profile in the shape the browser reports it.

    A real browser reports the brands it sends in the ``Sec-CH-UA`` header.
    The simulator used to report an empty list, which made every brand
    comparison in the probe pass for the wrong reason.
    """

    raw = _getattr_path(config, path)
    if not raw:
        return []

    brands: list[dict[str, str]] = []
    for item in raw:
        brand = _getattr_path(item, "brand")
        version = _getattr_path(item, "version")
        if brand is None:
            continue
        brands.append({"brand": str(brand), "version": str(version or "")})
    return brands


def _derive(config: Any, context: str) -> str:
    """Derive a deterministic hex digest from the config and ``context``."""

    parts: list[str] = []
    for field_path in _SEED_FIELDS:
        value = _getattr_path(config, field_path)
        parts.append(f"{field_path}={value}")
    parts.append(f"context={context}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest


def _digest_int(config: Any, context: str, length: int = 8) -> int:
    digest = _derive(config, context)
    return int(digest[:length], 16)


def _digest_float(config: Any, context: str, low: float, high: float) -> float:
    digest = _derive(config, context)
    raw = int(digest[8:16], 16) / float(1 << 32)
    return low + (high - low) * raw


def _digest_bool(config: Any, context: str) -> bool:
    return bool(_digest_int(config, context, 2) & 1)


def _digest_choice(config: Any, context: str, options: list[Any]) -> Any:
    if not options:
        return None
    index = _digest_int(config, context, 4) % len(options)
    return options[index]


class SimulatedSession:
    """A :class:`ProbeSession` that answers from a deterministic model.

    The session is constructed with the profile config it should impersonate;
    :meth:`evaluate` recognises the harness expression and returns the
    simulated payload.
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._closed = False
        self.closed = False
        self.calls: list[str] = []

    async def evaluate(self, expression: str) -> Any:
        """Return the simulated harness payload."""

        self.calls.append(expression)
        if self._closed:
            raise RuntimeError("session-closed")
        return self.simulate()

    async def close(self) -> None:
        """Mark the session as closed."""

        self._closed = True
        self.closed = True

    # -- the model ---------------------------------------------------------

    def simulate(self) -> dict[str, Any]:
        """Build the full simulated harness payload."""

        return {
            "schema": "nova.probe/1",
            "version": "1.0.0",
            "axes": self._axes(),
        }

    def _axes(self) -> dict[str, Any]:
        config = self._config
        navigator_payload = {
            "userAgent": _getattr_path(config, "navigator.user_agent") or "",
            "userAgentData": {
                "brands": _brands(config, "user_agent_data.brands"),
                "fullVersionList": _brands(config, "user_agent_data.full_version_list"),
                "mobile": True,
                "platform": _getattr_path(config, "user_agent_data.platform") or "Android",
                "platformVersion": _getattr_path(config, "user_agent_data.platform_version") or "",
                "architecture": _getattr_path(config, "user_agent_data.architecture") or "",
                "bitness": _getattr_path(config, "user_agent_data.bitness") or "",
                "model": _getattr_path(config, "user_agent_data.model") or "",
                "wow64": False,
            },
            "appVersion": _getattr_path(config, "navigator.app_version") or "",
            "appName": _getattr_path(config, "navigator.app_name") or "",
            "appCodeName": _getattr_path(config, "navigator.app_code_name") or "",
            "product": _getattr_path(config, "navigator.product") or "",
            "productSub": _getattr_path(config, "navigator.product_sub") or "",
            "vendor": _getattr_path(config, "navigator.vendor") or "",
            "vendorSub": _getattr_path(config, "navigator.vendor_sub") or "",
            "platform": _getattr_path(config, "navigator.platform") or "",
            "hardwareConcurrency": _getattr_path(config, "navigator.hardware_concurrency"),
            "deviceMemory": _getattr_path(config, "navigator.device_memory"),
            "maxTouchPoints": _getattr_path(config, "navigator.max_touch_points"),
            "language": _getattr_path(config, "navigator.language") or "",
            "languages": list(_getattr_path(config, "navigator.languages") or []),
            "webdriver": False,
            "pdfViewerEnabled": _getattr_path(config, "navigator.pdf_viewer_enabled"),
            "cookieEnabled": True,
            "doNotTrack": None,
            "globalPrivacyControl": None,
            "onLine": True,
            "javaEnabled": False,
            "plugins": [],
            "mimeTypes": [],
        }

        screen_payload = {
            "width": _getattr_path(config, "screen.width"),
            "height": _getattr_path(config, "screen.height"),
            "availWidth": _getattr_path(config, "screen.avail_width"),
            "availHeight": _getattr_path(config, "screen.avail_height"),
            "availLeft": _getattr_path(config, "screen.avail_left"),
            "availTop": _getattr_path(config, "screen.avail_top"),
            "colorDepth": _getattr_path(config, "screen.color_depth"),
            "pixelDepth": _getattr_path(config, "screen.pixel_depth"),
            "devicePixelRatio": _getattr_path(config, "screen.device_pixel_ratio"),
            "innerWidth": _getattr_path(config, "viewport.inner_width"),
            "innerHeight": _getattr_path(config, "viewport.inner_height"),
            "outerWidth": _getattr_path(config, "viewport.outer_width"),
            "outerHeight": _getattr_path(config, "viewport.outer_height"),
            "screenX": _getattr_path(config, "viewport.screen_x"),
            "screenY": _getattr_path(config, "viewport.screen_y"),
            "scrollX": 0,
            "scrollY": 0,
            "visualViewport": {
                "width": _getattr_path(config, "viewport.inner_width"),
                "height": _getattr_path(config, "viewport.inner_height"),
                "scale": _getattr_path(config, "viewport.visual_viewport_scale"),
                "offsetLeft": 0,
                "offsetTop": 0,
                "pageLeft": 0,
                "pageTop": 0,
            },
            "prefersColorScheme": (
                _getattr_path(config, "media_queries.prefers_color_scheme") or "light"
            ),
            "prefersReducedMotion": (
                _getattr_path(config, "media_queries.prefers_reduced_motion") or "no-preference"
            ),
            "prefersContrast": (
                _getattr_path(config, "media_queries.prefers_contrast") or "no-preference"
            ),
            "forcedColors": (_getattr_path(config, "media_queries.forced_colors") or "none"),
            "pointer": _getattr_path(config, "media_queries.pointer") or "coarse",
            "anyPointer": ((_getattr_path(config, "media_queries.any_pointer") or ["coarse"])[0]),
            "hover": _getattr_path(config, "media_queries.hover") or "none",
            "anyHover": ((_getattr_path(config, "media_queries.any_hover") or ["none"])[0]),
            "colorGamut": _getattr_path(config, "media_queries.color_gamut") or "srgb",
            "dynamicRange": (_getattr_path(config, "media_queries.dynamic_range") or "standard"),
            "prefersColorSchemeDark": (
                _getattr_path(config, "media_queries.prefers_color_scheme") == "dark"
            ),
            "prefersLightColorScheme": (
                _getattr_path(config, "media_queries.prefers_color_scheme") != "dark"
            ),
            "prefersReducedMotionReduce": (
                _getattr_path(config, "media_queries.prefers_reduced_motion") == "reduce"
            ),
            "prefersContrastMore": False,
            "forcedColorsActive": (
                _getattr_path(config, "media_queries.forced_colors") == "active"
            ),
            "pointerCoarse": _getattr_path(config, "media_queries.pointer") == "coarse",
            "pointerFine": _getattr_path(config, "media_queries.pointer") == "fine",
            "pointerNone": _getattr_path(config, "media_queries.pointer") == "none",
            "anyPointerCoarse": "coarse"
            in (_getattr_path(config, "media_queries.any_pointer") or []),
            "anyPointerFine": "fine" in (_getattr_path(config, "media_queries.any_pointer") or []),
            "hoverHover": _getattr_path(config, "media_queries.hover") == "hover",
            "hoverNone": _getattr_path(config, "media_queries.hover") == "none",
            "anyHoverHover": "hover" in (_getattr_path(config, "media_queries.any_hover") or []),
            "anyHoverNone": "none" in (_getattr_path(config, "media_queries.any_hover") or []),
            "colorGamutSrgb": _getattr_path(config, "media_queries.color_gamut") == "srgb",
            "colorGamutP3": _getattr_path(config, "media_queries.color_gamut") in {"p3", "rec2020"},
            "colorGamutRec2020": _getattr_path(config, "media_queries.color_gamut") == "rec2020",
            "dynamicRangeStandard": (
                _getattr_path(config, "media_queries.dynamic_range") == "standard"
            ),
            "dynamicRangeHigh": _getattr_path(config, "media_queries.dynamic_range") == "high",
            "videoHigh": False,
        }

        canvas_hash = _derive(config, "canvas")[:16]
        webgl_hash = _derive(config, "webgl")[:16]
        audio_hash = _derive(config, "audio")[:16]

        axes: dict[str, Any] = {
            "canvas": {
                "hash": canvas_hash,
                "dataUrlHash": _derive(config, "canvas.dataurl")[:16],
                "dataUrlLength": 4096,
                "width": 280,
                "height": 120,
                "byteLength": 134400,
                "firstPixels": [242, 244, 247, 255, 26, 115, 232, 255],
                "fontSignature": _derive(config, "canvas.font")[:16],
            },
            "webgl": {
                "context": "webgl2",
                "pixelHash": webgl_hash,
                "unmaskedVendor": _getattr_path(config, "webgl.unmasked_vendor") or "",
                "unmaskedRenderer": _getattr_path(config, "webgl.unmasked_renderer") or "",
                "version": _getattr_path(config, "webgl.version") or "",
                "shadingLanguageVersion": (
                    _getattr_path(config, "webgl.shading_language_version") or ""
                ),
                "maxTextureSize": _getattr_path(config, "webgl.parameters.max_texture_size"),
                "maxVaryingVectors": _getattr_path(config, "webgl.parameters.max_varying_vectors"),
                "maxFragmentUniformVectors": _getattr_path(
                    config, "webgl.parameters.max_fragment_uniform_vectors"
                ),
                "maxVertexUniformVectors": _getattr_path(
                    config, "webgl.parameters.max_vertex_uniform_vectors"
                ),
                "maxTextureImageUnits": _getattr_path(
                    config, "webgl.parameters.max_texture_image_units"
                ),
                "aliasedLineWidthRange": list(
                    _getattr_path(config, "webgl.parameters.aliased_line_width_range") or []
                ),
                "aliasedPointSizeRange": list(
                    _getattr_path(config, "webgl.parameters.aliased_point_size_range") or []
                ),
                "extensionCount": _digest_int(config, "webgl.extensions", 3),
                "extensionHash": _derive(config, "webgl.extensions")[:16],
                "vendor": _getattr_path(config, "webgl.vendor") or "",
                "renderer": _getattr_path(config, "webgl.renderer") or "",
            },
            "navigator": navigator_payload,
            "screen": screen_payload,
            "audio": {
                "sampleRate": 44100,
                "length": 44100,
                "numberOfChannels": 1,
                "mean": round(_digest_float(config, "audio.mean", 0.01, 0.2), 6),
                "peak": round(_digest_float(config, "audio.peak", 0.1, 0.9), 6),
                "sum": round(_digest_float(config, "audio.sum", 100.0, 5000.0), 4),
                "hash": audio_hash,
                "fftHash": _derive(config, "audio.fft")[:16],
                "fft": [],
            },
            "rects": {
                "host": {
                    "x": 16.0,
                    "y": 12.0,
                    "top": 12.0,
                    "right": 296.0,
                    "bottom": 84.0,
                    "left": 16.0,
                    "width": 280.0,
                    "height": 72.0,
                },
                "inner": {
                    "x": 27.0,
                    "y": 20.0,
                    "top": 20.0,
                    "right": 152.0,
                    "bottom": 46.0,
                    "left": 27.0,
                    "width": 125.0,
                    "height": 26.0,
                },
                "hostClientRectCount": 1,
                "innerClientRectCount": 1,
                "range": {
                    "x": 27.0,
                    "y": 20.0,
                    "top": 20.0,
                    "right": 152.0,
                    "bottom": 46.0,
                    "left": 27.0,
                    "width": 125.0,
                    "height": 26.0,
                },
                "rangeRectCount": 1,
                "measureText": {
                    "width": round(_digest_float(config, "rects.measure", 50.0, 120.0), 2),
                    "actualBoundingBoxLeft": -1.0,
                    "actualBoundingBoxRight": 60.0,
                    "actualBoundingBoxAscent": 12.0,
                    "actualBoundingBoxDescent": 3.0,
                    "fontBoundingBoxAscent": 15.0,
                    "fontBoundingBoxDescent": 4.0,
                },
                "devicePixelRatio": _getattr_path(config, "screen.device_pixel_ratio"),
            },
            "fonts": {
                "widths": {},
                "genericWidths": {},
                "installed": sorted(
                    _getattr_path(config, "fonts.families") or ["Roboto", "Noto Sans", "Arial"]
                ),
                "notInstalled": ["Helvetica", "Times New Roman", "Courier New"],
                "installedCount": len(_getattr_path(config, "fonts.families") or []),
                "probed": 16,
            },
            "media_devices": {
                "devices": [
                    {
                        "deviceId": _derive(config, "device.audio")[:32],
                        "kind": "audioinput",
                        "label": "",
                        "groupId": _derive(config, "group.audio")[:32],
                    },
                    {
                        "deviceId": _derive(config, "device.video")[:32],
                        "kind": "videoinput",
                        "label": "",
                        "groupId": _derive(config, "group.video")[:32],
                    },
                ],
                "count": 2,
                "withLabelCount": 0,
                "kinds": ["audioinput", "videoinput"],
                "groups": [
                    _derive(config, "group.audio")[:32],
                    _derive(config, "group.video")[:32],
                ],
            },
            "sensors": {
                "hasDeviceMotion": True,
                "hasDeviceOrientation": True,
                "hasAbsoluteOrientation": False,
                "motionSample": {
                    "interval": round(_digest_float(config, "sensors.interval", 10.0, 20.0), 2),
                    "acceleration": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "accelerationIncludingGravity": {"x": 0.0, "y": 0.0, "z": 9.81},
                    "rotationRate": {"alpha": 0.0, "beta": 0.0, "gamma": 0.0},
                },
                "orientationSample": {
                    "absolute": False,
                    "alpha": round(_digest_float(config, "sensors.alpha", 0.0, 360.0), 2),
                    "beta": round(_digest_float(config, "sensors.beta", -90.0, 90.0), 2),
                    "gamma": round(_digest_float(config, "sensors.gamma", -90.0, 90.0), 2),
                },
            },
            "storage": {
                "storage": {
                    "available": True,
                    "quota": _getattr_path(config, "storage_network.storage_quota_bytes")
                    or 3_221_225_472,
                    "usage": _digest_int(config, "storage.usage", 8),
                    "usageDetails": None,
                },
                "battery": {
                    "available": True,
                    "charging": _digest_bool(config, "battery.charging"),
                    "chargingTime": 3600,
                    "dischargingTime": 14400,
                    "level": round(_digest_float(config, "battery.level", 0.05, 0.99), 2),
                },
                "connection": {
                    "effectiveType": _getattr_path(config, "storage_network.effective_type")
                    or "4g",
                    "rtt": _getattr_path(config, "storage_network.rtt_ms") or 50,
                    "downlink": _getattr_path(config, "storage_network.downlink_mbps") or 10.0,
                    "saveData": False,
                    "type": "wifi",
                    "effectiveTypeSet": True,
                    "rttSet": True,
                    "downlinkSet": True,
                    "saveDataSet": True,
                },
            },
            "timezone": {
                "timeZone": _getattr_path(config, "timezone.iana_name") or "UTC",
                "locale": _getattr_path(config, "locale.intl_locale") or "en-US",
                "calendar": _getattr_path(config, "locale.calendar") or "gregory",
                "numberingSystem": _getattr_path(config, "locale.numbering_system") or "latn",
                "hourCycle": _getattr_path(config, "locale.hour_cycle") or "h23",
                "timeZoneName": _getattr_path(config, "timezone.abbreviation") or "UTC",
                "offsetMinutes": _getattr_path(config, "timezone.offset_minutes") or 0,
                "sampleTime": "22:13:20",
                "sampleDate": "11/14/2023 UTC",
                "supportedLocales": ["en-US", "id-ID", "vi-VN"],
            },
            "math": {
                "available": True,
                "values": {
                    "acos": _getattr_path(config, "math_precision.acos"),
                    "asin": _getattr_path(config, "math_precision.asin"),
                    "sinh": _getattr_path(config, "math_precision.sinh"),
                    "cosh": _getattr_path(config, "math_precision.cosh"),
                    "tanh": _getattr_path(config, "math_precision.tanh"),
                    "atan": _getattr_path(config, "math_precision.atan"),
                },
            },
            "heap": {
                "available": True,
                "jsHeapSizeLimit": _getattr_path(config, "heap_size_limit.js_heap_size_limit"),
            },
            "media_decode": {
                "available": True,
                "supported": sorted(
                    _getattr_path(config, "media_decode.supported") or []
                ),
            },
            "iframe_sync": {
                "iframe": _context_snapshot(navigator_payload, screen_payload, config),
                "srcdocIframe": _context_snapshot(navigator_payload, screen_payload, config),
                "sandboxedIframe": _context_snapshot(navigator_payload, screen_payload, config),
            },
            "worker_sync": {
                "userAgent": navigator_payload["userAgent"],
                "platform": navigator_payload["platform"],
                "language": navigator_payload["language"],
                "languages": navigator_payload["languages"],
                "hardwareConcurrency": navigator_payload["hardwareConcurrency"],
                "deviceMemory": navigator_payload["deviceMemory"],
                "maxTouchPoints": navigator_payload["maxTouchPoints"],
                "vendor": navigator_payload["vendor"],
                "webdriver": False,
                "timezone": _getattr_path(config, "timezone.iana_name") or "UTC",
                "locale": _getattr_path(config, "locale.intl_locale") or "en-US",
                "hash": canvas_hash,
                "hashError": None,
            },
        }

        # Keep the axis order stable and complete.
        ordered: dict[str, Any] = {}
        for axis in AXIS_ORDER:
            ordered[axis] = axes.get(axis, {"error": "missing-axis"})
        return ordered


def _context_snapshot(
    navigator_payload: dict[str, Any],
    screen_payload: dict[str, Any],
    config: Any,
) -> dict[str, Any]:
    return {
        "userAgent": navigator_payload["userAgent"],
        "platform": navigator_payload["platform"],
        "language": navigator_payload["language"],
        "vendor": navigator_payload["vendor"],
        "hardwareConcurrency": navigator_payload["hardwareConcurrency"],
        "deviceMemory": navigator_payload["deviceMemory"],
        "maxTouchPoints": navigator_payload["maxTouchPoints"],
        "screenWidth": screen_payload["width"],
        "screenHeight": screen_payload["height"],
        "devicePixelRatio": screen_payload["devicePixelRatio"],
        "timezone": _getattr_path(config, "timezone.iana_name") or "UTC",
    }


@dataclass
class SimulatedProbe:
    """A drop-in replacement for :class:`~novaprofile.probe.probe.RuntimeProbe`."""

    strict: bool = False
    _seen_signatures: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._seen_signatures is None:
            self._seen_signatures = set()

    # -- properties --------------------------------------------------------

    @property
    def seen_signatures(self) -> set[str]:
        """Signatures already observed by this simulator."""

        return self._seen_signatures

    def reset_signatures(self) -> None:
        """Forget every previously observed signature."""

        self._seen_signatures.clear()

    # -- collection --------------------------------------------------------

    async def collect_async(self, config: Any) -> ProbeReport:
        """Simulate a probe run against ``config``."""

        session = SimulatedSession(config)
        try:
            payload = await session.evaluate("window.__NOVA_PROBE__.run()")
        finally:
            await session.close()

        axes = payload.get("axes", {}) if isinstance(payload, dict) else {}
        expectations = evaluate_axis_expectations(config)
        results = compare_expectations(expectations, payload, strict=self.strict)
        consistency_pass = bool(results) and all(result.ok for result in results)

        signature = compute_signature(results)
        distinct_ratio, collision_rate = collision_stats(signature, self._seen_signatures)
        self._seen_signatures.add(signature)

        persistence_ok = _persistence_ok(axes)
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

        import asyncio

        return asyncio.run(self.collect_async(config))


def _persistence_ok(axes: dict[str, Any]) -> bool:
    worker = axes.get("worker_sync")
    canvas = axes.get("canvas")
    if (
        isinstance(worker, dict)
        and isinstance(canvas, dict)
        and str(worker.get("hash", "")) != str(canvas.get("hash", ""))
    ):
        return False
    navigator_payload = axes.get("navigator")
    iframes = axes.get("iframe_sync")
    if isinstance(navigator_payload, dict) and isinstance(iframes, dict):
        main_agent = str(navigator_payload.get("userAgent", ""))
        for key in ("iframe", "srcdocIframe", "sandboxedIframe"):
            frame = iframes.get(key)
            if isinstance(frame, dict) and str(frame.get("userAgent", "")) != main_agent:
                return False
    return True


def _profile_id(config: Any) -> str:
    value = _getattr_path(config, "identity.id")
    return str(value) if value is not None else ""


def simulate_payload(config: Any) -> dict[str, Any]:
    """Return the simulated harness payload for ``config``."""

    return SimulatedSession(config).simulate()


def simulated_axis_values(config: Any) -> dict[str, str]:
    """Return a flat ``axis -> canonical json`` mapping for ``config``.

    Used by the tests to prove that two configs with different seeds produce
    different results and that identical configs produce identical results.
    """

    payload = simulate_payload(config)
    axes = payload.get("axes", {})
    flat: dict[str, str] = {}
    for axis in sorted(axes):
        flat[axis] = json.dumps(axes[axis], sort_keys=True, separators=(",", ":"))
    return flat


__all__ = [
    "SimulatedProbe",
    "SimulatedSession",
    "simulate_payload",
    "simulated_axis_values",
]
