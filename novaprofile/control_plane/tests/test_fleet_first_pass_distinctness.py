"""A page that reads several axes at once must not find a pair.

The fleet is made of many handsets of the same model.  A page is
allowed to know that -- every real Galaxy S23 on earth shares a
graphics card and a chip count -- and no amount of fingerprinting
changes that.  What the page must never see is *two* profiles that
agree on the readings it takes in one pass.

The readings are cheap and they are taken together: the user agent,
the core count, the memory, the language, the panel.  A script can
compare all five before the first paint, so a fleet where any two
profiles agree on all five has given the site the pair it was
looking for.

The generator therefore draws the narrow axes -- the browser major,
the core count, the memory -- *stratified*: each unit of a family is
dealt its own slice of the choice space.  These tests lock that in.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.hardware_approximation import (
    approximate_device_memory,
    approximate_hardware_concurrency,
)
from novaprofile.js_runtime.builder import JSRuntimeBuilder

#: The readings a page takes in its first pass, in the order it takes
#: them.  The order matters: a site that stops after the first few
#: is the site that groups the most profiles together.
FIRST_PASS: tuple[tuple[str, ...], ...] = (
    ("navigator", "userAgent"),
    ("navigator", "hardwareConcurrency"),
    ("navigator", "deviceMemory"),
    ("navigator", "language"),
    ("screen", "width"),
    ("screen", "height"),
    ("screen", "devicePixelRatio"),
)

#: The narrow axes: the ones with only a handful of possible values,
#: and therefore the ones where an independent draw clumps.
NARROW: tuple[tuple[str, ...], ...] = (
    ("navigator", "userAgent"),
    ("navigator", "hardwareConcurrency"),
    ("navigator", "deviceMemory"),
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


def _key(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _family(preset_id: str, units: int = 8) -> list[dict]:
    generator = ProfileGenerator()
    index = next(
        i for i, preset in enumerate(DEVICE_PRESETS) if preset.preset_id == preset_id
    )
    return [
        _payload(
            generator.generate(
                preset_id,
                master_seed=f"{index * 1000 + unit:064x}",
                stratum=unit,
            )
        )
        for unit in range(units)
    ]


@pytest.fixture(scope="module")
def fleet() -> list[tuple[str, dict]]:
    """The shipping fleet: eight units of every device in the catalogue."""

    generator = ProfileGenerator()
    result: list[tuple[str, dict]] = []
    for index, preset in enumerate(DEVICE_PRESETS):
        for unit in range(8):
            result.append(
                (
                    f"{preset.preset_id}#{unit}",
                    _payload(
                        generator.generate(
                            preset.preset_id,
                            master_seed=f"{index * 1000 + unit:064x}",
                            stratum=unit,
                        )
                    ),
                )
            )
    return result


class TestFirstPassNeverGroups:
    def test_no_pair_agrees_on_the_whole_first_pass(self, fleet) -> None:
        buckets: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for name, payload in fleet:
            buckets[tuple(_key(_at(payload, path)) for path in FIRST_PASS)].append(name)

        repeated = {key: names for key, names in buckets.items() if len(names) > 1}
        assert repeated == {}, (
            f"{len(repeated)} pairs agree on every reading of the first pass: "
            f"{list(repeated.values())[:3]}"
        )

    def test_no_pair_agrees_on_the_first_three_readings(self, fleet) -> None:
        """The three cheapest readings are enough to group a fleet.

        The user agent, the core count and the memory cost three
        property reads.  If two profiles agree on all three, a site
        that reads nothing else has already found its pair.
        """

        buckets: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for name, payload in fleet:
            buckets[tuple(_key(_at(payload, path)) for path in NARROW)].append(name)

        repeated = {key: names for key, names in buckets.items() if len(names) > 1}
        assert repeated == {}, (
            f"{len(repeated)} pairs agree on the agent, the cores and the memory: "
            f"{list(repeated.values())[:3]}"
        )

    @pytest.mark.parametrize(
        "path",
        [
            ("navigator", "userAgent"),
            ("navigator", "hardwareConcurrency"),
            ("navigator", "deviceMemory"),
        ],
    )
    def test_a_narrow_reading_spreads_a_family_when_the_model_allows_it(
        self, path
    ) -> None:
        """Inside one model, a narrow axis must be spread, not clumped.

        The core count and the memory have only a handful of possible
        values, and the browser major only a dozen.  Eight handsets of
        one model fit in those spaces comfortably; the draw has to
        spread them over it rather than clumping them in it.

        A model that ships in one memory size -- a Pixel 8 Pro is an
        8 GB phone, and there is no 12 GB one -- has only one value to
        report, and reporting a second would be a lie the owner's own
        settings page can contradict.  The test therefore asks the
        family to use every value the catalogue offers, and no more.
        """

        for preset in DEVICE_PRESETS:
            units = _family(preset.preset_id)
            values = {_key(_at(unit, path)) for unit in units}

            if path[-1] == "userAgent":
                # The browser major is a draw, not a catalogue option,
                # so a family always has room to spread over it.
                assert len(values) == len(units), (
                    f"{preset.preset_id}: the user agent takes only "
                    f"{len(values)} values across {len(units)} units"
                )
                continue

            offered = (
                # The value that reaches the page is the *approximated*
                # memory, so a model that sells 8 GB and 12 GB phones
                # reports eight gigabytes for both of them -- Chromium
                # rounds to a power of two and clamps at eight.  What
                # the page can distinguish is what the approximation
                # leaves distinct.
                len(
                    {
                        approximate_device_memory(option)
                        for option in preset.ram_options
                    }
                )
                if path[-1] == "deviceMemory"
                else len(
                    {
                        approximate_hardware_concurrency(option)
                        for option in preset.cpu_options
                    }
                )
            )
            assert len(values) == min(offered, len(units)), (
                f"{preset.preset_id}: {'.'.join(path)} takes only "
                f"{len(values)} values across {len(units)} units, but the "
                f"catalogue offers {offered}"
            )

    def test_the_browser_major_covers_the_band(self) -> None:
        """The units of a family must spread over the whole band.

        A family that only ever reports two browser majors is a family
        of two, wearing eight labels.
        """

        generator = ProfileGenerator()
        for index, preset in enumerate(DEVICE_PRESETS):
            majors = set()
            for unit in range(8):
                config = generator.generate(
                    preset.preset_id,
                    master_seed=f"{index * 1000 + unit:064x}",
                    stratum=unit,
                )
                majors.add(config.browser_version.major)
            assert len(majors) >= 6, (
                f"{preset.preset_id}: only {len(majors)} browser majors across 8 units"
            )


class TestTheDrawIsStillAPureFunction:
    def test_the_same_stratum_gives_the_same_profile(self) -> None:
        """A profile must survive a restart with an identical print.

        The stratum is part of the derivation, so regenerating the
        same unit yields the same profile -- nothing about the fleet
        layout is allowed to make a profile drift between runs.
        """

        first = _family("pixel-8-pro-android14")
        second = _family("pixel-8-pro-android14")
        assert [_signature(unit) for unit in first] == [
            _signature(unit) for unit in second
        ]

    def test_different_strata_give_different_profiles(self) -> None:
        """Two units of one model are two different phones."""

        units = _family("galaxy-s24-ultra-android14")
        signatures = [_signature(unit) for unit in units]
        assert len(set(signatures)) == len(signatures)

    def test_a_lone_profile_is_drawn_independently(self) -> None:
        """A profile that is not part of a fleet still works.

        The stratum is optional.  Without one, every choice is drawn
        on its own, which is what a single handset on a desk needs.
        """

        generator = ProfileGenerator()
        config = generator.generate("pixel-7-android14", master_seed="ab" * 32)
        assert config.navigator.hardware_concurrency > 0
        assert config.browser_version.major > 0


def _signature(payload: dict) -> str:
    return json.dumps(
        [_at(payload, path) for path in FIRST_PASS], sort_keys=True, default=str
    )
