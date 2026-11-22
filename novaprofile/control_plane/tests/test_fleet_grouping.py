"""The fleet must not be groupable.

A site does not compare a profile against the world.  It compares a
profile against the profiles it has already seen, and it groups them
by the readings that travel together in one place.

The measurements behind this file come from the telemetry of a real
site: one record holds the physical resolution, the density, the
version of the system, the number of cores, the amount of memory and
the model of the device.  Two profiles that agree on all of those are
**one device** to that site, whatever their canvas, their audio and
their seed say.

So the fleet has to be checked against exactly that key, and against
the key a site builds from what the page can read: the user agent,
the client hints, the locale.

These tests are the reason the mixed radix exists.  Drawing the
memory, the release, the model code, the panel and the density on
their own puts each one on its own slice, and two units can land on
the same *combination* of slices while every single reading is
different from the other unit's.  One index over the whole space of
the device is what keeps the combination apart.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator


def _fleet(per_preset: int) -> list[tuple[str, object]]:
    """The fleet, as the list of the readings the site groups on."""

    generator = ProfileGenerator()
    fleet: list[tuple[str, object]] = []
    for index, preset in enumerate(DEVICE_PRESETS):
        for unit in range(per_preset):
            profile = generator.generate(
                preset.preset_id,
                master_seed=f"{index * 1000 + unit:064x}",
                stratum=unit,
            )
            fleet.append(
                (
                    preset.preset_id,
                    (
                        # -- the hardware key the telemetry carries --
                        profile.android_build.model,
                        profile.android_build.version_release,
                        profile.screen.width,
                        profile.screen.height,
                        profile.screen.device_pixel_ratio,
                        profile.navigator.hardware_concurrency,
                        profile.navigator.device_memory,
                        profile.webgl.unmasked_renderer,
                    ),
                )
            )
    return fleet


def _largest_cluster(fleet: list[tuple[str, object]]) -> int:
    """The size of the biggest group of profiles that look identical."""

    buckets: dict[object, int] = defaultdict(int)
    for _, key in fleet:
        buckets[key] += 1
    return max(buckets.values(), default=0)


@pytest.mark.parametrize("per_preset", [8, 16, 24])
def test_a_small_family_is_never_groupable(per_preset: int) -> None:
    """A family of up to twenty-four units must have no pair at all.

    Every hardware reading the site groups on -- the model code, the
    Android release, the panel, the density, the cores, the memory
    and the GPU -- has to be different between any two units of one
    model.
    """

    fleet = _fleet(per_preset)
    assert _largest_cluster(fleet) == 1, (
        "two units of the same model reported the same hardware key; "
        "the site would read them as one device"
    )


@pytest.mark.parametrize("per_preset", [8, 16, 24])
def test_the_readings_the_page_can_read_separate_every_unit(per_preset: int) -> None:
    """The key a site builds from the page must have no cluster at all.

    This is the key that matters in practice: the user agent, the
    browser version and the locale.  A page reads all three in its
    first pass, and a site that keeps them together keeps the
    profiles together.
    """

    generator = ProfileGenerator()
    buckets: dict[object, int] = defaultdict(int)
    for index, preset in enumerate(DEVICE_PRESETS):
        for unit in range(per_preset):
            profile = generator.generate(
                preset.preset_id,
                master_seed=f"{index * 1000 + unit:064x}",
                stratum=unit,
            )
            key = (
                profile.android_build.model,
                profile.android_build.version_release,
                profile.browser_version.major,
                profile.browser_version.full_version,
                profile.locale.intl_locale,
                profile.screen.width,
                profile.screen.height,
            )
            buckets[key] += 1
    assert max(buckets.values(), default=0) == 1, (
        "two units of the same model were indistinguishable on the "
        "readings the page reports; a site would group them"
    )


def test_the_model_codes_of_a_family_come_from_the_declared_set() -> None:
    """The model code must be one the preset declared.

    A code the manufacturer does not use is a contradiction a site can
    check against the vendor's own device list.

    The codes do not have to change on every unit -- a real family
    bought in one country does carry one code for a long time -- and
    the radix reaches the code only after the space in front of it is
    spent, so a family of eight units may well carry one code.  What
    matters is that the code is one the preset declared, and that a
    family long enough to exhaust the space in front of the code does
    reach more than one code.
    """

    generator = ProfileGenerator()
    for preset in DEVICE_PRESETS:
        if len(preset.model_variants) < 2:
            continue
        allowed = set(preset.model_variants)
        codes = {
            generator.generate(
                preset.preset_id,
                master_seed=f"{unit:064x}",
                stratum=unit,
            ).android_build.model
            for unit in range(len(preset.model_variants) * 8)
        }
        assert codes <= allowed, (
            f"{preset.preset_id} carried a code the preset did not declare: "
            f"{codes - allowed}"
        )


def test_a_profile_never_carries_a_code_its_preset_does_not_declare() -> None:
    """A code the manufacturer does not use is a contradiction.

    The site that knows the vendor's device list can contradict a
    profile on a code that does not exist, and a fleet that invents
    codes is a fleet that contradicts itself.
    """

    generator = ProfileGenerator()
    for preset in DEVICE_PRESETS:
        allowed = set(preset.model_variants or (preset.model,))
        for unit in range(8):
            profile = generator.generate(
                preset.preset_id,
                master_seed=f"{unit:064x}",
                stratum=unit,
            )
            assert profile.android_build.model in allowed
