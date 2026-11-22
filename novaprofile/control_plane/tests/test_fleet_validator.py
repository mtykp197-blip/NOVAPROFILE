"""The fleet validator.

The fleet grows from a hand-written catalogue to tens of thousands of
generated devices, so the checker that guards it has to be able to fail
a generator that produces physically impossible combinations.

These tests do two things:

* they pin the *ground truth* -- the 42 known-good presets must produce
  zero errors and zero warnings, and
* they break one field at a time with :func:`dataclasses.replace` and
  assert that the right axis is flagged, so every rule is proven to
  bite.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from novaprofile.core.device_catalog_extended import EXTENDED_PRESETS
from novaprofile.core.devices import (
    DEVICE_PRESETS,
    DevicePreset,
    GpuProfile,
    all_presets,
)
from novaprofile.core.fleet_validator import (
    _CLASS_TEXTURES,
    FleetIssue,
    FleetReport,
    _gpu_classes,
    validate_fleet,
    validate_preset,
)

#: The reference fleet: eleven hand-written devices plus the extended
#: catalogue.  These are known-good and must never be flagged.
KNOWN_GOOD: tuple[DevicePreset, ...] = DEVICE_PRESETS + EXTENDED_PRESETS

#: A preset that is convenient to break: a Snapdragon with an Adreno
#: renderer, a 3.0 ratio on a 480 dpi panel and a modern release.
BASE: DevicePreset = next(
    preset for preset in KNOWN_GOOD if preset.preset_id == "galaxy-s24-ultra-android14"
)


def _break(model: Any, **changes: Any) -> Any:
    """A copy of a model with some fields overwritten.

    The preset and the GPU profile are frozen dataclasses, while the
    screen and the ABI description are frozen pydantic models whose
    validators would reject the very values a broken generator produces.
    Both have to be breakable, so both kinds are handled here.
    """
    if hasattr(model, "model_copy"):
        return model.model_copy(update=changes)
    return replace(model, **changes)


def _axes(issues: list[FleetIssue]) -> set[str]:
    """The axes the issues touch."""
    return {issue.axis for issue in issues}


def _fields(issues: list[FleetIssue]) -> set[str]:
    """The preset fields the issues touch."""
    return {issue.field for issue in issues}


def _error_issues(issues: list[FleetIssue]) -> list[FleetIssue]:
    return [issue for issue in issues if issue.severity == "error"]


# --------------------------------------------------------------------------
# The ground truth
# --------------------------------------------------------------------------


def test_the_reference_fleet_has_no_errors() -> None:
    """The 42 known-good presets must produce zero errors.

    This is the assertion that keeps the validator honest: if it flags
    a real device, the rule is wrong and the rule has to change.
    """
    report = validate_fleet(KNOWN_GOOD)

    assert report.checked == len(KNOWN_GOOD)
    assert len(KNOWN_GOOD) >= 42
    assert report.errors == (), report.summary()


def test_the_reference_fleet_has_no_warnings() -> None:
    """The reference fleet is coherent enough to raise no soft findings either."""
    report = validate_fleet(KNOWN_GOOD)

    assert report.warnings == (), report.summary()


def test_every_reference_preset_validates_on_its_own() -> None:
    """``validate_preset`` agrees with ``validate_fleet``."""
    for preset in KNOWN_GOOD:
        assert validate_preset(preset) == [], preset.preset_id


def test_the_report_says_so() -> None:
    """A clean report is marked coherent and says how much it checked."""
    report = validate_fleet(KNOWN_GOOD)

    assert report.ok is True
    assert "coherent" in report.summary()
    assert str(len(KNOWN_GOOD)) in report.summary()


# --------------------------------------------------------------------------
# The GPU class table covers the whole fleet
# --------------------------------------------------------------------------


def test_every_renderer_the_fleet_ships_belongs_to_a_known_class() -> None:
    """No renderer of the shipped fleet is left without a class.

    The class table has to name every GPU the fleet actually uses.  A
    renderer that falls into no class only produces a warning, but the
    warning says that the *table* is incomplete, not that the preset is
    wrong, so it is noise.
    """
    unknown = {
        preset.gpu.unmasked_renderer
        for preset in all_presets()
        if not _gpu_classes(preset.gpu.unmasked_renderer)
    }

    assert unknown == set()


def test_every_renderer_of_the_fleet_reports_a_texture_limit_of_its_class() -> None:
    """The class of a renderer has to admit the limit the fleet reports.

    The class is what makes the texture rule meaningful, so a renderer
    whose shipped limit falls outside its own class band would make the
    rule meaningless for that renderer.
    """
    for preset in all_presets():
        classes = _gpu_classes(preset.gpu.unmasked_renderer)
        assert classes, preset.preset_id
        allowed = {
            limit for name in classes for limit in _CLASS_TEXTURES[name]
        }
        assert preset.gpu.max_texture_size in allowed, preset.preset_id


@pytest.mark.parametrize(
    ("renderer", "expected"),
    [
        # The Mali-G52 spans two classes: the MC2 is the budget core of
        # the Helio G80/G85/G88 and the later revisions are the 8192
        # parts.
        ("Mali-G52 MC2", "budget"),
        ("Mali-G52 2EEMC2", "mid"),
        ("Mali-G52 MP6", "mid"),
        ("Mali-G52", "budget"),
        # The Mali-G71, G72, G76 and G77 are 16384 parts throughout.
        ("Mali-G71 MP2", "flagship"),
        ("Mali-G72 MP3", "flagship"),
        ("Mali-G72 MP18", "flagship"),
        ("Mali-G76 MC4", "mid"),
        ("Mali-G76 MP12", "flagship"),
        ("Mali-G77 MC9", "flagship"),
        # The mainstream Adreno parts.
        ("Adreno 618", "budget"),
        ("Adreno 616", "budget"),
        ("Adreno 612", "mid"),
        ("Adreno 613", "mid"),
        ("Adreno 644", "flagship"),
        ("Adreno 710", "flagship"),
        ("Adreno 720", "flagship"),
        # The Imagination and the IMG cores are the 4096 parts.
        ("PowerVR SGX540", "budget"),
        ("PowerVR G6200", "budget"),
        ("PowerVR GM9446", "budget"),
        ("IMG8322", "budget"),
        ("IMG BXM-8-256", "budget"),
        # The Apple cores.
        ("Apple GPU (4-core graphics)", "flagship"),
    ],
)
def test_a_revision_lands_in_the_class_of_its_specification(
    renderer: str, expected: str
) -> None:
    """A revision is classified by its specification, not by its family."""
    classes = _gpu_classes(renderer)

    assert expected in classes, renderer


def test_a_vendor_prefix_does_not_hide_the_core() -> None:
    """The vendor word in front of a core does not change its class.

    The GSMArena specification sheets name the vendor in front of the
    core -- "ARM Mali-G76 MP16", "Qualcomm Adreno (TM) 618".
    """
    for prefixed, bare in (
        ("ARM Mali-G76 MP16", "Mali-G76 MP16"),
        ("Qualcomm Adreno (TM) 618", "Adreno 618"),
        ("Arm Mali-G610 MC3", "Mali-G610 MC3"),
    ):
        assert _gpu_classes(prefixed) == _gpu_classes(bare), prefixed


def test_a_regional_suffix_does_not_hide_the_core() -> None:
    """The regional suffix of a record does not change its class."""
    assert _gpu_classes("Mali-G76 MP12 - EMEA/LATAM") == _gpu_classes("Mali-G76 MP12")
    assert _gpu_classes("Mali-G72 MP18 - EMEA") == _gpu_classes("Mali-G72 MP18")


# --------------------------------------------------------------------------
# Rule 1: the texture limit has to match the GPU class
# --------------------------------------------------------------------------


def test_a_flagship_gpu_may_not_report_a_budget_texture_limit() -> None:
    """An Adreno 750 is a flagship part and reports 16384."""
    broken = replace(BASE, gpu=replace(BASE.gpu, max_texture_size=4096))

    issues = validate_preset(broken)

    assert "gpu" in _axes(issues)
    assert "gpu.max_texture_size" in _fields(issues)
    assert _error_issues(issues)


def test_a_budget_gpu_may_not_report_a_flagship_texture_limit() -> None:
    """A budget part reports 4096 or 8192, never 16384."""
    budget_gpu = GpuProfile(
        unmasked_vendor="Qualcomm",
        unmasked_renderer="Adreno (TM) 610",
        vendor="WebKit",
        renderer="WebKit WebGL",
        version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
        max_texture_size=16384,
    )
    broken = replace(BASE, gpu=budget_gpu)

    issues = validate_preset(broken)

    assert "gpu.max_texture_size" in _fields(issues)
    assert _error_issues(issues)


def test_the_texture_limit_of_a_mid_gpu_is_accepted() -> None:
    """A mid part may report either 8192 or 16384."""
    for limit in (8192, 16384):
        mid_gpu = GpuProfile(
            unmasked_vendor="Qualcomm",
            unmasked_renderer="Adreno (TM) 540",
            vendor="WebKit",
            renderer="WebKit WebGL",
            version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
            max_texture_size=limit,
        )
        broken = replace(
            BASE,
            gpu=mid_gpu,
            abi=_break(BASE.abi, gpu_architecture="Adreno 540"),
        )

        assert validate_preset(broken) == [], limit


def test_a_gpu_that_ships_in_two_classes_may_report_either_limit() -> None:
    """The Adreno 630 ships in an entry-level and in a flagship SoC.

    The renderer string does not carry the SoC, so the limit has to fall
    inside the union of the two bands.
    """
    for limit in (8192, 16384):
        ambiguous = GpuProfile(
            unmasked_vendor="Qualcomm",
            unmasked_renderer="Adreno (TM) 630",
            vendor="WebKit",
            renderer="WebKit WebGL",
            version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
            max_texture_size=limit,
        )
        broken = replace(
            BASE,
            gpu=ambiguous,
            abi=_break(BASE.abi, gpu_architecture="Adreno 630"),
        )

        assert validate_preset(broken) == [], limit


def test_a_gpu_that_ships_in_two_classes_may_not_report_a_budget_limit() -> None:
    """Neither variant of the Adreno 630 reports a 4096 texture limit."""
    ambiguous = GpuProfile(
        unmasked_vendor="Qualcomm",
        unmasked_renderer="Adreno (TM) 630",
        vendor="WebKit",
        renderer="WebKit WebGL",
        version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
        max_texture_size=4096,
    )
    broken = replace(
        BASE,
        gpu=ambiguous,
        abi=_break(BASE.abi, gpu_architecture="Adreno 630"),
    )

    issues = validate_preset(broken)

    assert "gpu.max_texture_size" in _fields(issues)
    assert _error_issues(issues)


# --------------------------------------------------------------------------
# Rule 2: the renderer vendor has to match the family
# --------------------------------------------------------------------------


def test_an_adreno_renderer_may_not_claim_arm() -> None:
    """An Adreno is a Qualcomm part."""
    broken = replace(BASE, gpu=replace(BASE.gpu, unmasked_vendor="ARM"))

    issues = validate_preset(broken)

    assert "gpu.unmasked_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_a_mali_renderer_may_not_claim_qualcomm() -> None:
    """A Mali is an ARM part."""
    mali = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "pixel-7-android14"
    )
    broken = replace(mali, gpu=_break(mali.gpu, unmasked_vendor="Qualcomm"))

    issues = validate_preset(broken)

    assert "gpu.unmasked_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_a_maleoon_renderer_may_not_claim_arm() -> None:
    """Maleoon is Huawei's own core, not ARM's."""
    huawei = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "huawei-p60-pro-android13"
    )
    broken = replace(huawei, gpu=_break(huawei.gpu, unmasked_vendor="ARM"))

    issues = validate_preset(broken)

    assert "gpu.unmasked_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_the_tensor_gpu_keeps_its_google_vendor() -> None:
    """A Tensor's Mali reports "Google Inc. (ARM)" and must not be flagged.

    This is the case the rules explicitly have to leave alone: the
    vendor is Google's, the family is ARM's.
    """
    pixel = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "pixel-8-pro-android14"
    )

    assert pixel.gpu.unmasked_vendor == "Google Inc. (ARM)"
    assert validate_preset(pixel) == []


def test_the_tensor_adreno_keeps_its_google_vendor() -> None:
    """A Tensor's Adreno reports "Google Inc. (Qualcomm)"."""
    tensor_gpu = GpuProfile(
        unmasked_vendor="Google Inc. (Qualcomm)",
        unmasked_renderer="Adreno (TM) 642L",
        vendor="WebKit",
        renderer="WebKit WebGL",
        version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
        max_texture_size=8192,
    )
    pixel = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "pixel-8-pro-android14"
    )
    broken = replace(
        pixel,
        gpu=tensor_gpu,
        abi=_break(pixel.abi, gpu_architecture="Adreno 642L"),
    )

    assert validate_preset(broken) == []


# --------------------------------------------------------------------------
# Rule 3: the architecture has to name the same GPU
# --------------------------------------------------------------------------


def test_the_architecture_may_not_name_another_gpu_family() -> None:
    """An Adreno renderer with a Mali architecture is a different GPU."""
    broken = replace(BASE, abi=_break(BASE.abi, gpu_architecture="Mali-G78 MP20"))

    issues = validate_preset(broken)

    assert "abi.gpu_architecture" in _fields(issues)
    assert _error_issues(issues)


def test_the_architecture_may_name_another_model_of_the_same_family() -> None:
    """A different model of the same family is not an inconsistency.

    The catalogue ships an Adreno 660 whose architecture string says
    "Adreno 642"; the rule has to accept that.
    """
    y36 = next(preset for preset in KNOWN_GOOD if preset.preset_id == "vivo-y36-android13")

    assert y36.gpu.unmasked_renderer != y36.abi.gpu_architecture
    assert validate_preset(y36) == []


def test_an_immortalis_renderer_may_not_carry_an_adreno_architecture() -> None:
    """Immortalis is MediaTek's rebrand of the Mali architecture."""
    vivo = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "vivo-x90-android14"
    )
    broken = replace(vivo, abi=_break(vivo.abi, gpu_architecture="Adreno 740"))

    issues = validate_preset(broken)

    assert "abi.gpu_architecture" in _fields(issues)
    assert _error_issues(issues)


# --------------------------------------------------------------------------
# Rule 4: the SoC vendor has to follow from the SoC
# --------------------------------------------------------------------------


def test_a_snapdragon_soc_may_not_claim_samsung() -> None:
    """A Snapdragon is a Qualcomm part."""
    broken = replace(BASE, abi=_break(BASE.abi, soc_vendor="Samsung"))

    issues = validate_preset(broken)

    assert "abi.soc_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_a_tensor_soc_may_not_claim_qualcomm() -> None:
    """A Tensor is Google's own part."""
    pixel = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "pixel-8-pro-android14"
    )
    broken = replace(pixel, abi=_break(pixel.abi, soc_vendor="Qualcomm"))

    issues = validate_preset(broken)

    assert "abi.soc_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_an_exynos_soc_may_not_claim_qualcomm() -> None:
    """An Exynos is a Samsung part."""
    samsung = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "galaxy-a54-android13"
    )
    broken = replace(samsung, abi=_break(samsung.abi, soc_vendor="Qualcomm"))

    issues = validate_preset(broken)

    assert "abi.soc_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_a_dimensity_soc_may_not_claim_qualcomm() -> None:
    """A Dimensity is a MediaTek part."""
    vivo = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "vivo-x90-android14"
    )
    broken = replace(vivo, abi=_break(vivo.abi, soc_vendor="Qualcomm"))

    issues = validate_preset(broken)

    assert "abi.soc_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_a_kirin_soc_may_not_claim_qualcomm() -> None:
    """A Kirin is a HiSilicon part."""
    huawei = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "huawei-p60-pro-android13"
    )
    broken = replace(huawei, abi=_break(huawei.abi, soc_vendor="Qualcomm"))

    issues = validate_preset(broken)

    assert "abi.soc_vendor" in _fields(issues)
    assert _error_issues(issues)


def test_a_tensor_soc_with_a_google_gpu_vendor_is_accepted() -> None:
    """A Tensor that reports "Google Inc. (ARM)" is correct, not a mismatch."""
    pixel = next(
        preset for preset in KNOWN_GOOD if preset.preset_id == "pixel-8-pro-android14"
    )

    assert pixel.abi.soc_vendor == "Google"
    assert pixel.gpu.unmasked_vendor == "Google Inc. (ARM)"
    assert validate_preset(pixel) == []


# --------------------------------------------------------------------------
# Rule 5: the screen has to be plausible
# --------------------------------------------------------------------------


def test_the_screen_width_may_not_be_zero() -> None:
    """A panel cannot have no pixels."""
    broken = replace(BASE, screen=_break(BASE.screen, width=0))

    issues = validate_preset(broken)

    assert "screen" in _axes(issues)
    assert "screen.width" in _fields(issues)
    assert _error_issues(issues)


def test_the_screen_height_may_not_be_negative() -> None:
    """A panel cannot have a negative height."""
    broken = replace(BASE, screen=_break(BASE.screen, height=-100))

    issues = validate_preset(broken)

    assert "screen.height" in _fields(issues)
    assert _error_issues(issues)


def test_the_pixel_ratio_may_not_leave_the_plausible_range() -> None:
    """A ratio above 5.0 is not a panel any Android device reports."""
    broken = replace(BASE, screen=_break(BASE.screen, device_pixel_ratio=9.0))

    issues = validate_preset(broken)

    assert "screen.device_pixel_ratio" in _fields(issues)
    assert _error_issues(issues)


def test_the_pixel_ratio_may_not_be_below_one() -> None:
    """A ratio below 1.0 is not a panel any Android device reports."""
    broken = replace(BASE, screen=_break(BASE.screen, device_pixel_ratio=0.5))

    issues = validate_preset(broken)

    assert "screen.device_pixel_ratio" in _fields(issues)
    assert _error_issues(issues)


def test_the_density_may_not_leave_the_plausible_range() -> None:
    """A density above 800 dpi is not a panel any Android device reports."""
    broken = replace(BASE, screen=_break(BASE.screen, density_dpi=2000))

    issues = validate_preset(broken)

    assert "screen.density_dpi" in _fields(issues)
    assert _error_issues(issues)


def test_the_density_may_not_be_below_the_floor() -> None:
    """A density below 120 dpi is not a panel any Android device reports."""
    broken = replace(BASE, screen=_break(BASE.screen, density_dpi=80))

    issues = validate_preset(broken)

    assert "screen.density_dpi" in _fields(issues)
    assert _error_issues(issues)


def test_the_available_width_may_not_exceed_the_width() -> None:
    """The available area is a part of the screen, never more."""
    broken = replace(BASE, screen=_break(BASE.screen, avail_width=BASE.screen.width + 1))

    issues = validate_preset(broken)

    assert "screen.avail_width" in _fields(issues)
    assert _error_issues(issues)


def test_the_available_height_may_not_exceed_the_height() -> None:
    """The available area is a part of the screen, never more."""
    broken = replace(
        BASE, screen=_break(BASE.screen, avail_height=BASE.screen.height + 1)
    )

    issues = validate_preset(broken)

    assert "screen.avail_height" in _fields(issues)
    assert _error_issues(issues)


# --------------------------------------------------------------------------
# Rule 6: the pixel ratio has to agree with the density
# --------------------------------------------------------------------------


def test_a_pixel_ratio_that_contradicts_the_density_is_a_warning() -> None:
    """The density and the ratio disagree softly.

    The rule is a soft one, so it must not fail the fleet on its own.
    The option lists are widened here so that the pixel ratio rule is
    the only one that speaks.
    """
    broken = replace(
        BASE,
        screen=_break(BASE.screen, device_pixel_ratio=1.5, density_dpi=480),
        dpr_options=(1.5, 2.625, 3.0),
    )

    issues = validate_preset(broken)

    assert "screen.device_pixel_ratio" in _fields(issues)
    assert not _error_issues(issues)
    assert all(issue.severity == "warning" for issue in issues)


def test_the_warning_does_not_make_the_report_fail() -> None:
    """A soft finding keeps the report healthy."""
    broken = replace(
        BASE,
        screen=_break(BASE.screen, device_pixel_ratio=1.5, density_dpi=480),
        dpr_options=(1.5, 2.625, 3.0),
    )
    report = validate_fleet([broken])

    assert report.ok is True
    assert report.errors == ()
    assert len(report.warnings) == 1


# --------------------------------------------------------------------------
# Rule 7: the release and the SDK level have to agree
# --------------------------------------------------------------------------


def test_the_sdk_level_may_not_contradict_the_release() -> None:
    """Android 14 is SDK 34."""
    broken = replace(BASE, sdk_int=33)

    issues = validate_preset(broken)

    assert "release" in _axes(issues)
    assert "release.sdk_int" in _fields(issues)
    assert _error_issues(issues)


@pytest.mark.parametrize(
    ("release", "sdk_int"),
    [("9", 28), ("10", 29), ("11", 30), ("12", 31), ("13", 33), ("14", 34), ("15", 35), ("16", 36)],
)
def test_every_known_release_maps_to_its_sdk_level(release: str, sdk_int: int) -> None:
    """The release table covers every release the platform ships."""
    broken = replace(BASE, release=release, sdk_int=sdk_int)

    assert validate_preset(broken) == [], release


# --------------------------------------------------------------------------
# Rule 8: the security patch has to be a real, past date
# --------------------------------------------------------------------------


def test_the_security_patch_may_not_be_in_the_future() -> None:
    """A device cannot ship a patch from the future."""
    broken = replace(BASE, security_patch="2099-01-01")

    issues = validate_preset(broken)

    assert "release.security_patch" in _fields(issues)
    assert _error_issues(issues)


def test_the_security_patch_may_not_be_malformed() -> None:
    """The patch is a date, not a free-form string."""
    broken = replace(BASE, security_patch="2024/06/05")

    issues = validate_preset(broken)

    assert "release.security_patch" in _fields(issues)
    assert _error_issues(issues)


def test_the_security_patch_may_not_be_an_impossible_date() -> None:
    """The 30th of February does not exist."""
    broken = replace(BASE, security_patch="2023-02-30")

    issues = validate_preset(broken)

    assert "release.security_patch" in _fields(issues)
    assert _error_issues(issues)


# --------------------------------------------------------------------------
# Rule 9: the build id has to look like a build id
# --------------------------------------------------------------------------


def test_the_build_id_may_not_be_free_form() -> None:
    """A build id has a fixed shape."""
    broken = replace(BASE, build_id="not-a-build-id")

    issues = validate_preset(broken)

    assert "release.build_id" in _fields(issues)
    assert _error_issues(issues)


def test_the_build_id_may_not_have_a_wrong_date_part() -> None:
    """The date part of a build id has six digits."""
    broken = replace(BASE, build_id="UP1A.2310.007")

    issues = validate_preset(broken)

    assert "release.build_id" in _fields(issues)
    assert _error_issues(issues)


def test_the_vendor_specific_build_ids_are_accepted() -> None:
    """Motorola and Huawei ship build ids of their own shape."""
    for preset_id in ("motorola-edge-40-android13", "huawei-p60-pro-android13"):
        preset = next(p for p in KNOWN_GOOD if p.preset_id == preset_id)
        assert validate_preset(preset) == [], preset_id


# --------------------------------------------------------------------------
# Rule 10: the identity fields have to be present
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "preset_id",
        "manufacturer",
        "brand",
        "model",
        "device",
        "product",
        "hardware",
        "board",
        "bootloader",
        "fingerprint_product",
        "build_id",
        "version_incremental",
    ],
)
def test_an_empty_identity_field_is_an_error(field: str) -> None:
    """Every identity string has to carry a value."""
    broken = replace(BASE, **{field: ""})

    issues = validate_preset(broken)

    assert "identity" in _axes(issues)
    assert f"identity.{field}" in _fields(issues)
    assert _error_issues(issues)


def test_a_blank_identity_field_is_an_error() -> None:
    """Whitespace is not a value."""
    broken = replace(BASE, model="   ")

    issues = validate_preset(broken)

    assert "identity.model" in _fields(issues)
    assert _error_issues(issues)


# --------------------------------------------------------------------------
# Rule 11: the option lists have to be plausible and cover the panel
# --------------------------------------------------------------------------


def test_the_ram_options_may_not_be_empty() -> None:
    """A device ships with memory."""
    broken = replace(BASE, ram_options=())

    issues = validate_preset(broken)

    assert "memory.ram_options" in _fields(issues)
    assert _error_issues(issues)


def test_an_out_of_range_ram_option_is_a_warning() -> None:
    """128 GB of RAM is not a phone."""
    broken = replace(BASE, ram_options=(8, 128))

    issues = validate_preset(broken)

    assert "memory.ram_options" in _fields(issues)
    assert not _error_issues(issues)


def test_the_cpu_options_may_not_be_empty() -> None:
    """A device ships with cores."""
    broken = replace(BASE, cpu_options=())

    issues = validate_preset(broken)

    assert "memory.cpu_options" in _fields(issues)
    assert _error_issues(issues)


def test_an_out_of_range_cpu_option_is_a_warning() -> None:
    """64 cores is not a phone."""
    broken = replace(BASE, cpu_options=(8, 64))

    issues = validate_preset(broken)

    assert "memory.cpu_options" in _fields(issues)
    assert not _error_issues(issues)


def test_the_pixel_ratio_options_have_to_cover_the_panel() -> None:
    """A preset that does not offer its own ratio cannot produce that panel."""
    broken = replace(BASE, dpr_options=(1.5, 2.0))

    issues = validate_preset(broken)

    assert "memory.dpr_options" in _fields(issues)
    assert _error_issues(issues)


def test_the_pixel_ratio_options_may_not_be_empty() -> None:
    """A preset has to offer some pixel ratio."""
    broken = replace(BASE, dpr_options=())

    issues = validate_preset(broken)

    assert "memory.dpr_options" in _fields(issues)
    assert _error_issues(issues)


def test_the_density_options_have_to_cover_the_panel() -> None:
    """A preset that does not offer its own density cannot produce that panel."""
    broken = replace(BASE, density_options=(160, 240))

    issues = validate_preset(broken)

    assert "memory.density_options" in _fields(issues)
    assert _error_issues(issues)


def test_the_density_options_may_not_be_empty() -> None:
    """A preset has to offer some density."""
    broken = replace(BASE, density_options=())

    issues = validate_preset(broken)

    assert "memory.density_options" in _fields(issues)
    assert _error_issues(issues)


# --------------------------------------------------------------------------
# Rule 12: the preset ids have to be unique
# --------------------------------------------------------------------------


def test_a_duplicate_preset_id_is_an_error() -> None:
    """The fleet builder keys on the preset id."""
    report = validate_fleet([BASE, BASE])

    assert report.checked == 2
    assert not report.ok
    assert "identity.preset_id" in _fields(list(report.errors))


def test_the_duplicate_is_reported_on_the_second_occurrence() -> None:
    """The first occurrence stays clean, the second one is flagged."""
    report = validate_fleet([BASE, replace(BASE, model="Something Else")])

    duplicates = [
        issue for issue in report.errors if issue.field == "identity.preset_id"
    ]
    assert len(duplicates) == 1


def test_distinct_preset_ids_do_not_collide() -> None:
    """Two different devices with different ids are fine."""
    other = replace(BASE, preset_id="galaxy-s24-ultra-android15")

    report = validate_fleet([BASE, other])

    assert report.ok is True


# --------------------------------------------------------------------------
# The report object
# --------------------------------------------------------------------------


def test_the_report_separates_errors_from_warnings() -> None:
    """``errors`` and ``warnings`` partition the issues."""
    broken = replace(BASE, sdk_int=33)
    report = validate_fleet([broken])

    assert report.errors
    assert report.warnings == ()
    assert len(report.errors) + len(report.warnings) == len(report.issues)


def test_the_report_serialises() -> None:
    """The report has a JSON-friendly view."""
    report = validate_fleet(
        [BASE, replace(BASE, preset_id="dupe"), replace(BASE, preset_id="dupe")]
    )

    payload = report.to_dict()

    assert payload["checked"] == 3
    assert payload["ok"] is False
    assert payload["error_count"] == len(report.errors)
    assert payload["warning_count"] == len(report.warnings)
    assert len(payload["issues"]) == len(report.issues)
    assert set(payload["issues"][0]) == {
        "preset_id",
        "axis",
        "field",
        "message",
        "severity",
    }


def test_the_summary_names_the_first_error() -> None:
    """The summary points at the first thing to fix."""
    report = validate_fleet([replace(BASE, sdk_int=99)])

    summary = report.summary()

    assert "INCOHERENT" in summary
    assert BASE.preset_id in summary
    assert "sdk_int" in summary


def test_an_empty_fleet_is_coherent() -> None:
    """A fleet of no devices raises nothing."""
    report = validate_fleet([])

    assert report.checked == 0
    assert report.ok is True
    assert report.issues == ()


def test_the_issue_renders_readably() -> None:
    """An issue says which preset, axis and field it is about."""
    issue = FleetIssue(
        preset_id="some-device",
        axis="gpu",
        field="gpu.max_texture_size",
        message="the texture limit does not match the GPU class",
        severity="error",
    )

    assert "some-device" in str(issue)
    assert "gpu.max_texture_size" in str(issue)


def test_the_report_type_is_exported() -> None:
    """The public names the control plane imports are present."""
    assert FleetReport(checked=0, issues=()).ok is True
    assert FleetReport(checked=0, issues=()).summary()
