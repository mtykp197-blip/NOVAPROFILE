"""Two profiles of the same model must still be two different phones.

The obvious way to lose the fleet is to hand the operator the same
profile twice with a different label.  The harder way to lose it is
to hand over two profiles of the *same handset model* that agree on
everything a page can read -- same panel, same graphics card, same
font list, same core count, same memory, same audio hardware.

A page that sees two identical fingerprints has learned that two
accounts belong to one machine.  So even within a single device
family, every unit must be individually distinguishable.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.identity import _GPU_VARIANTS
from novaprofile.js_runtime.builder import JSRuntimeBuilder

#: The axes a page can read off a device and compare between visits.
COMPARABLE: tuple[tuple[str, ...], ...] = (
    ("navigator", "userAgent"),
    ("navigator", "platform"),
    ("navigator", "hardwareConcurrency"),
    ("navigator", "deviceMemory"),
    ("navigator", "language"),
    ("navigator", "languages"),
    ("navigator", "maxTouchPoints"),
    ("navigator", "vendor"),
    ("navigator", "doNotTrack"),
    ("screen", "width"),
    ("screen", "height"),
    ("screen", "availWidth"),
    ("screen", "availHeight"),
    ("screen", "colorDepth"),
    ("screen", "pixelDepth"),
    ("screen", "devicePixelRatio"),
    ("viewport", "inner_width"),
    ("viewport", "inner_height"),
    ("viewport", "outer_width"),
    ("viewport", "outer_height"),
    ("timezone", "iana_name"),
    ("timezone", "offset_minutes"),
    ("canvas_2d", "salt"),
    ("canvas_2d", "noise_strength"),
    ("audio", "sample_rate"),
    ("webgl", "unmasked_vendor"),
    ("webgl", "unmasked_renderer"),
    ("webgl", "version"),
    ("webgl", "shading_language_version"),
    ("math_precision", "salt"),
    ("math_precision", "epsilon"),
    ("heap_size_limit", "js_heap_size_limit"),
    ("heap_size_limit", "salt"),
    ("media_decode", "salt"),
    ("sensors", "sampling_hz"),
    ("sensors", "accelerometer"),
    ("sensors", "gyroscope"),
    ("fonts", "installed"),
    ("fonts", "salt"),
    ("media_devices", "devices"),
    ("permissions", "policies"),
)


def _payload(config) -> dict:
    script = JSRuntimeBuilder().build_payload_only(config)
    return json.loads(script.split("=", 1)[1].rsplit(";", 1)[0])


def _at(payload: dict, path: tuple[str, ...]):
    current = payload
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _signature(payload: dict) -> str:
    return json.dumps([_at(payload, path) for path in COMPARABLE], sort_keys=True, default=str)


@pytest.fixture(scope="module")
def families() -> dict[str, list[dict]]:
    """For every device family, a handful of distinct units of it."""

    generator = ProfileGenerator()
    result: dict[str, list[dict]] = {}
    for index, preset in enumerate(DEVICE_PRESETS):
        units = []
        for unit in range(8):
            units.append(
                _payload(
                    generator.generate(
                        preset.preset_id,
                        master_seed=f"{index * 1000 + unit:064x}",
                    )
                )
            )
        result[preset.preset_id] = units
    return result


class TestUnitsOfOneModelStayDistinct:
    def test_every_family_holds_distinct_units(self, families) -> None:
        for family, units in families.items():
            signatures = Counter(_signature(unit) for unit in units)
            repeated = {key: count for key, count in signatures.items() if count > 1}
            assert repeated == {}, f"{family}: {len(repeated)} of its units read as the same device"

    def test_the_entropy_never_repeats_inside_a_family(self, families) -> None:
        """Canvas, math, heap and media noise are the per-device bits.

        They must differ between two units of the same model, or the
        only thing separating them is the account name.
        """

        for family, units in families.items():
            for path in (
                ("canvas_2d", "salt"),
                ("math_precision", "salt"),
                ("heap_size_limit", "salt"),
                ("media_decode", "salt"),
                ("sensors", "accelerometer"),
                ("sensors", "gyroscope"),
            ):
                values = {json.dumps(_at(unit, path), default=str) for unit in units}
                assert len(values) == len(units), (
                    f"{family}: {'.'.join(path)} repeats inside the family"
                )

    def test_the_noise_strength_is_not_a_grouping_key(self, families) -> None:
        """The noise strength is a rounded float and may repeat.

        Two units of the same model can land on the same strength --
        it is a value out of a million rounded to six decimals -- so
        the test is that it does not *partition* the fleet, not that
        it is unique.  The salt is what carries the identity; the
        strength only has to spread.
        """

        for family, units in families.items():
            strengths = {_at(unit, ("canvas_2d", "noise_strength")) for unit in units}
            assert len(strengths) >= max(2, len(units) // 3), (
                f"{family}: the noise strength collapsed to "
                f"{len(strengths)} values across {len(units)} units"
            )

    def test_the_fonts_differ_inside_a_family(self, families) -> None:
        """Font list is one of the few per-device reads on Android.

        Two units of the same model with the same fonts need another
        axis, and there is not a strong one.
        """

        for family, units in families.items():
            fonts = {json.dumps(_at(unit, ("fonts", "installed"))) for unit in units}
            assert len(fonts) >= max(2, len(units) // 2), (
                f"{family}: only {len(fonts)} distinct font lists across {len(units)} units"
            )

    def test_the_time_zones_differ_inside_a_family(self, families) -> None:
        """A time zone is a property of where the owner lives.

        Two handsets in the same city share one, and a fleet that
        spread its zones so thinly that no two profiles shared one
        would be a fleet no operator could actually run.  What matters
        is that the zone does not partition the family.
        """

        for family, units in families.items():
            zones = {json.dumps(_at(unit, ("timezone", "iana_name"))) for unit in units}
            assert len(zones) >= 2, f"{family}: every unit reports the same time zone"

    def test_the_panel_differs_inside_a_family(self, families) -> None:
        """Resolution and scale factor have to move within a model.

        Real handsets of one model ship with several panel variants --
        a different screen revision from the same supplier, a
        different display mode in the settings -- so a family whose
        units all report one panel is a family of one device.
        """

        for family, units in families.items():
            panels = {
                (
                    _at(unit, ("screen", "width")),
                    _at(unit, ("screen", "height")),
                    _at(unit, ("screen", "devicePixelRatio")),
                )
                for unit in units
            }
            assert len(panels) >= 2, (
                f"{family}: only {len(panels)} distinct panels across its units"
            )

    def test_every_panel_is_physically_possible(self, families) -> None:
        """A panel variant has to divide into whole logical pixels.

        The logical screen is the physical panel divided by the scale
        factor, and the browser reports whole numbers of them.  A
        panel that divides into a fractional pixel is a panel the
        device never had.
        """

        from novaprofile.core.device_catalog_market import ratio_for_density
        from novaprofile.core.identity import _DENSITY_VARIANTS, _PANEL_VARIANTS

        for family, units in families.items():
            panels = _PANEL_VARIANTS.get(family, ())
            buckets = _DENSITY_VARIANTS.get(family, ())
            allowed = {
                (
                    int(round(width / ratio_for_density(density))),
                    int(round(height / ratio_for_density(density))),
                )
                for width, height in panels
                for density in buckets
            }
            for unit in units:
                width = _at(unit, ("screen", "width"))
                height = _at(unit, ("screen", "height"))
                ratio = _at(unit, ("screen", "devicePixelRatio"))
                assert isinstance(width, int) and isinstance(height, int)
                assert width > 0 and height > 0
                assert 1.0 <= ratio <= 5.0
                assert _at(unit, ("screen", "availWidth")) <= width
                assert _at(unit, ("screen", "availHeight")) <= height
                if allowed:
                    assert (width, height) in allowed, (
                        f"{family}: the panel {width}x{height} is not one of the family's panels"
                    )

    def test_the_graphics_cards_differ_inside_a_family(self, families) -> None:
        """A family with several GPU SKUs must be able to show them.

        Some families ship one part and that is honest -- the test
        below only applies to the families that declare variants.
        """

        for family, units in families.items():
            cards = {json.dumps(_at(unit, ("webgl", "unmasked_renderer"))) for unit in units}
            declared = len(_GPU_VARIANTS.get(family, ()))
            if declared > 1:
                assert len(cards) >= min(declared, 2), (
                    f"{family}: declares {declared} GPU variants but shows {len(cards)}"
                )
            else:
                assert len(cards) == 1, f"{family}: declares one GPU but shows {len(cards)}"

    def test_the_user_agent_differs_inside_a_family(self, families) -> None:
        """Build numbers move between units of the same model.

        Chrome versions are the loudest differentiator that a page
        reads, and handset makers ship them at different times.
        """

        for family, units in families.items():
            agents = {json.dumps(_at(unit, ("navigator", "userAgent"))) for unit in units}
            assert len(agents) >= 3, f"{family}: only {len(agents)} distinct user agents"

    def test_a_model_family_stays_one_bucket_on_a_single_axis(self, families) -> None:
        """The opposite test, and the one that matters most.

        A web that groups on *one* axis must not be able to tell that
        two profiles come from the same host, because a single axis
        never identifies a device -- it identifies a *class* of device,
        and a class is shared by every real handset of that model on
        earth.  What the web must never see is two profiles that agree
        on the *combination*, and the test above covers that.
        """

        for family, units in families.items():
            # Within a model, one axis may be shared -- it has to be,
            # because a Galaxy S23 always has an Adreno 740.  But the
            # combination of the axes a page reads together has to move.
            combinations = {
                (
                    _at(unit, ("webgl", "unmasked_renderer")),
                    _at(unit, ("screen", "width")),
                    _at(unit, ("timezone", "iana_name")),
                    _at(unit, ("navigator", "language")),
                    _at(unit, ("navigator", "userAgent")),
                )
                for unit in units
            }
            assert len(combinations) >= len(units) - 1, (
                f"{family}: only {len(combinations)} distinct combinations "
                f"across {len(units)} units"
            )
