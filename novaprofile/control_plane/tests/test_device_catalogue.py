"""The device catalogue.

The fleet is only as good as the catalogue it draws from.  A catalogue
of a dozen devices gives a vendor a dozen buckets to sort the whole
fleet into, no matter how much the individual profiles differ.

These tests pin the properties the catalogue has to hold:

* every device declares the GPU that actually ships inside it,
* the renderer the profile reports is one the engine knows about,
* the Android release, the SDK level and the security patch agree with
  each other,
* the panel, the ratio and the density are values a real panel reports.
"""

from __future__ import annotations

from collections import Counter

import pytest

from novaprofile.core.device_catalog_market import (
    ANDROID_DPR,
    DENSITY_RATIO,
    SCREEN_DENSITY,
)
from novaprofile.core.devices import DEVICE_PRESETS, all_presets, list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.identity import _GPU_CATALOGUE, _GPU_VARIANTS


@pytest.fixture(scope="module")
def catalogue() -> list:
    return list_presets()


def test_the_catalogue_is_wide_enough_to_hide_a_fleet(catalogue: list) -> None:
    """A vendor must not be able to sort the fleet into a handful of buckets.

    Grouping on the model, the board or the panel is the cheapest
    thing a site can do, so the number of devices has to be large
    enough that a bucket is a small share of the fleet.

    The share is measured on the largest bucket rather than on the
    number of distinct values, because the fleet holds one device per
    RAM configuration of a handset: the number of models is therefore
    smaller than the number of devices by design, and a model that
    ships in three RAM sizes is one bucket of three devices rather
    than three buckets of one.
    """

    assert len(catalogue) >= 40

    for attribute in ("model", "board", "device"):
        counts = Counter(getattr(preset, attribute) for preset in catalogue)
        largest = counts.most_common(1)[0][1]
        assert largest <= len(catalogue) * 0.01, (
            f"{largest} of {len(catalogue)} devices share one {attribute}, "
            "which is a bucket a site could sort the fleet into"
        )


def test_every_device_declares_the_gpu_that_ships_in_it(catalogue: list) -> None:
    """The renderer and the architecture have to be the same piece of hardware.

    A site that cross-checks the WebGL renderer against the
    architecture the client hints advertise catches a mismatch
    immediately.
    """

    for preset in catalogue:
        architecture = preset.abi.gpu_architecture.lower()
        renderer = preset.gpu.unmasked_renderer.lower()
        matches = (
            architecture in renderer
            or renderer in architecture
            or architecture.split()[0] in renderer
        )
        assert matches, (
            f"{preset.preset_id} claims the architecture {architecture!r} but "
            f"reports the renderer {preset.gpu.unmasked_renderer!r}"
        )


def test_every_renderer_is_known_to_the_engine(catalogue: list) -> None:
    """A renderer the catalogue does not know about is a renderer the
    engine cannot give the right limits to."""

    for preset in catalogue:
        variants = _GPU_VARIANTS.get(preset.preset_id, (preset.gpu.unmasked_renderer,))
        for renderer in variants:
            assert renderer in _GPU_CATALOGUE, (
                f"{preset.preset_id} names the renderer {renderer!r}, which the "
                "engine does not know"
            )
            assert _GPU_CATALOGUE[renderer].unmasked_renderer == renderer


def test_every_device_has_an_entry_in_the_variant_table(catalogue: list) -> None:
    """A preset without a variant entry falls back to its own GPU.

    That fallback is what the table exists to avoid: the table is the
    place where the family is allowed to declare the two SKUs it
    shipped in, and a preset that is missing from it loses that
    freedom without anyone noticing.
    """

    for preset in catalogue:
        assert preset.preset_id in _GPU_VARIANTS, (
            f"{preset.preset_id} is missing from the GPU variant table"
        )


def test_the_android_release_and_the_sdk_level_agree(catalogue: list) -> None:
    """A release and an SDK level that disagree are a contradiction.

    The mapping is fixed: Android 12 is 31, 13 is 33, 14 is 34 and 15
    is 35.
    """

    expected = {"12": 31, "13": 33, "14": 34, "15": 35}
    for preset in catalogue:
        assert expected[preset.release] == preset.sdk_int, (
            f"{preset.preset_id} claims Android {preset.release} with the SDK "
            f"level {preset.sdk_int}"
        )


def test_the_security_patch_never_predates_the_release(catalogue: list) -> None:
    """The patch has to be at or after the release."""

    for preset in catalogue:
        assert preset.security_patch >= f"{preset.year}-01-01"


def test_the_panels_report_values_a_real_panel_reports(catalogue: list) -> None:
    """The ratio and the density have to be values a phone actually uses.

    The fleet carries tablets and the low-density panels that go with
    them, so the ratio is not bounded below by the ratio of a modern
    handset.  What has to hold is that the pair is a pair the platform
    actually reports: both values lie in the Android vocabulary, and
    the ratio is the one the density bucket implies.
    """

    for preset in catalogue:
        screen = preset.screen
        assert screen.device_pixel_ratio in ANDROID_DPR
        assert screen.density_dpi in SCREEN_DENSITY
        assert DENSITY_RATIO[screen.density_dpi] == screen.device_pixel_ratio
        assert screen.width >= 320
        assert screen.height >= 320
        assert screen.avail_width <= screen.width
        assert screen.avail_height <= screen.height
        # The option lists have to stay inside the same vocabulary: an
        # option the schema does not hold reaches a generated profile
        # silently and only fails when the config is serialised.
        for ratio in preset.dpr_options:
            assert ratio in ANDROID_DPR
        for density in preset.density_options:
            assert density in SCREEN_DENSITY


def test_the_reference_catalogue_is_kept_separate(catalogue: list) -> None:
    """The reference devices stay the ones the test suite is written against."""

    assert len(DEVICE_PRESETS) == 11
    assert len(catalogue) > len(DEVICE_PRESETS)
    assert all_presets()[: len(DEVICE_PRESETS)] == DEVICE_PRESETS


def test_a_generated_profile_agrees_with_its_preset() -> None:
    """The generated profile must report the hardware of its own preset.

    The model code is the one reading that the preset offers a *set*
    of, because a handset is sold under several codes and the code is
    in the build fingerprint and in the client hints.  A profile that
    carried one code for every unit of the family would hand a site a
    grouping key for free, so the profile has to carry one of the
    codes the preset declares -- and never a code the preset does not
    declare.
    """

    generator = ProfileGenerator()
    presets = list_presets()
    for index, preset in enumerate(presets):
        profile = generator.generate(preset.preset_id, master_seed=f"{index:064x}")
        assert profile.webgl.unmasked_renderer == preset.gpu.unmasked_renderer
        assert profile.android_build.model in (preset.model_variants or (preset.model,))
        assert profile.android_abi_soc.soc_model == preset.abi.soc_model


def test_the_core_count_of_every_preset_is_one_the_schema_accepts() -> None:
    """``hardwareConcurrency`` may only carry a value the schema allows.

    The core count is not the number of physical cores: a Snapdragon 8
    Gen 2 has five of them and reports eight, because its little cores
    count twice.  So the count cannot be derived from the cluster
    layout -- it has to be carried by the catalogue, and a typo in
    the catalogue is a profile that reports nine cores, which no
    Android handset has ever done.
    """

    allowed = {4, 6, 8, 12}
    for preset in list_presets():
        for cores in preset.cpu_options:
            assert cores in allowed, (
                f"{preset.preset_id} offers {cores} cores, "
                f"which is not a value the schema accepts"
            )


def test_two_presets_of_the_same_chipset_report_the_same_core_count() -> None:
    """The same chipset always reports the same number of cores.

    The Pixel 7 and the Pixel Fold both carry a Tensor G2, so they
    must both report eight cores.  A catalogue that gives the two
    models different counts is a catalogue with a typo in it, and the
    typo is invisible until a profile is generated from it.
    """

    by_soc: dict[str, set[int]] = {}
    for preset in list_presets():
        by_soc.setdefault(preset.abi.soc_model, set()).update(preset.cpu_options)

    for soc, counts in by_soc.items():
        assert len(counts) == 1, (
            f"{soc} is listed with several core counts: {sorted(counts)}"
        )
