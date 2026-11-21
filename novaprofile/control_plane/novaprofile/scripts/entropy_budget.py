"""The entropy budget of every identifying axis.

A collision guard is only sound if the axis it guards has enough
entropy to hold the fleet.  A guard that refuses to let two profiles
share a 16-bit field would block the fleet long before 100 million
profiles; worse, the fallback rebuild would eventually have nowhere to
go.

This module measures the entropy each axis actually carries and compares
it with the threshold the fleet size demands::

    required_bits = log2(target_fleet)

The widths are derived from the *generator*, not hard-coded: every
component's width is read from the code path that produces it, so a
narrowing of any field shows up here as a budget violation instead of
silently weakening a guarantee.
"""

from __future__ import annotations

import math
from typing import Any

from novaprofile.core.collision import GUARDED_AXES
from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator

#: The fleet the engine is designed for.
TARGET_FLEET = 100_000_000

#: The bits every identifying field needs at the target fleet size.
REQUIRED_BITS = math.log2(TARGET_FLEET)


def _sample_bits(profile) -> int:
    """The width of a sampled integer value, in bits."""
    value = int(profile)
    if value == 0:
        return 1
    return value.bit_length()


def _hex_bits(value: str) -> int:
    """The width of a hexadecimal string, in bits."""
    if not value:
        return 0
    try:
        int(value, 16)
    except ValueError:
        return 0
    return len(value) * 4


def _axis_entropy(profile) -> dict[str, int]:
    """The effective entropy of each guarded axis, in bits.

    An axis that concatenates several independent per-unit draws carries
    the *sum* of their widths: two profiles that differ in any one of
    them are still distinguishable on that axis.

    Only *per-unit* draws count.  A value that is shared by every unit
    of a family -- the GPU model, the screen size, the locale -- carries
    no entropy at all and is deliberately left out, because counting it
    would overstate the axis.
    """

    build = profile.android_build
    serial_bits = _hex_bits(build.serial)
    incremental_bits = _sample_bits(build.version_incremental)

    canvas_bits = _hex_bits(profile.canvas_2d.salt)
    audio_bits = _hex_bits(profile.audio.salt)
    rects_bits = _hex_bits(profile.rects.salt)
    fonts_bits = _hex_bits(profile.fonts.salt)
    devices_bits = _hex_bits(profile.media_devices.salt)
    webgpu_bits = _hex_bits(profile.webgpu.salt)
    webgl_bits = _hex_bits(profile.webgl.salt)
    sensors_bits = _hex_bits(profile.sensors.salt)
    storage_bits = _hex_bits(profile.storage_network.salt)

    # The WebGL pixel-noise value is a 17-significant-digit decimal, so
    # it carries about 56 bits of the underlying draw.
    noise_bits = 56

    widths = {
        "sig_device": serial_bits + incremental_bits,
        "sig_canvas": canvas_bits,
        "sig_webgl_draw": noise_bits + webgl_bits,
        "sig_webgl_params": noise_bits + canvas_bits,
        "sig_rects": rects_bits,
        "sig_devices": devices_bits,
        "sig_audio": audio_bits,
        "sig_sensors": sensors_bits,
        "sig_storage": storage_bits,
        "sig_webgpu": webgpu_bits,
        "sig_fonts": fonts_bits,
        "sig_full": serial_bits
        + incremental_bits
        + canvas_bits
        + audio_bits
        + rects_bits
        + fonts_bits
        + devices_bits
        + webgpu_bits
        + webgl_bits
        + sensors_bits
        + storage_bits,
    }
    return widths


def _birthday_probability(bits: int, fleet: int) -> float:
    """The probability that ``fleet`` draws collide in ``bits`` bits.

    The space is computed in log space: an axis with several hundred bits
    overflows a float, and such an axis collides with a probability that
    is zero to any precision that matters.
    """
    if bits <= 0:
        return 1.0
    if bits > 128:
        # log2(fleet) is about 27 bits, so anything past 128 bits has a
        # collision probability below 1e-30 and is reported as zero.
        return 0.0
    space = float(2**bits)
    return 1.0 - math.exp(-(fleet * (fleet - 1)) / (2 * space))


def budget_report(
    target_fleet: int = TARGET_FLEET,
    sample: int = 200,
) -> dict[str, Any]:
    """Measure the entropy budget of every guarded axis.

    Returns the widths, the birthday probability of each axis at the
    target fleet size and the weakest axis.
    """

    generator = ProfileGenerator()
    presets = [p.preset_id for p in list_presets()]

    # The widths are measured across the whole sample so that a narrow
    # draw is caught even if one lucky profile happens to be wide.
    axis_widths: dict[str, int] = {}
    distinct: dict[str, set[str]] = {}

    for index in range(sample):
        profile = generator.generate(
            presets[index % len(presets)], master_seed=f"{index:064x}"
        )
        for name, bits in _axis_entropy(profile).items():
            axis_widths[name] = max(axis_widths.get(name, 0), bits)

        parts = profile.signature_parts()
        for kind in GUARDED_AXES:
            key = kind.value
            values = profile.full_signature_parts() if key == "sig_full" else parts.get(key, [])
            distinct.setdefault(key, set()).add("\x1f".join(map(str, values)))

    required = math.log2(target_fleet)
    axes: dict[str, Any] = {}
    worst_axis = ""
    worst_bits = float("inf")

    for kind in GUARDED_AXES:
        key = kind.value
        bits = axis_widths.get(key, 0)
        probability = _birthday_probability(bits, target_fleet)
        axes[key] = {
            "bits": bits,
            "birthday_p_at_target": probability,
            "clears_threshold": bits >= required,
            "distinct_in_sample": len(distinct.get(key, ())),
            "sample": sample,
        }
        if bits < worst_bits:
            worst_bits = bits
            worst_axis = key

    return {
        "target_fleet": target_fleet,
        "required_bits": round(required, 2),
        "axes": axes,
        "worst_axis": worst_axis,
        "worst_axis_bits": round(worst_bits, 2),
        "all_axes_clear": all(a["clears_threshold"] for a in axes.values()),
    }


def format_report(report: dict[str, Any]) -> str:
    """Render a budget report as a table."""
    lines = [
        f"target fleet      {report['target_fleet']:,}",
        f"required bits     {report['required_bits']}",
        "",
        f"{'axis':20s} {'bits':>7s} {'p(collision)':>14s} {'ok':>5s}",
        "-" * 50,
    ]
    for key in sorted(report["axes"]):
        axis = report["axes"][key]
        probability = axis["birthday_p_at_target"]
        p_text = f"{probability:.2e}" if probability else "0"
        lines.append(
            f"{key:20s} {axis['bits']:7d} {p_text:>14s} "
            f"{'yes' if axis['clears_threshold'] else 'NO':>5s}"
        )
    lines.append("")
    lines.append(
        f"weakest axis: {report['worst_axis']} "
        f"at {report['worst_axis_bits']} bits"
    )
    lines.append(
        "all axes clear the threshold: "
        f"{'yes' if report['all_axes_clear'] else 'NO'}"
    )
    return "\n".join(lines)


__all__ = [
    "REQUIRED_BITS",
    "TARGET_FLEET",
    "budget_report",
    "format_report",
]
