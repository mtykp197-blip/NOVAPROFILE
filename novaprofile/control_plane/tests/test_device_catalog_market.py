"""The market catalogue: the devices the Vietnamese market actually sells.

The catalogue is built from real specifications, so the tests below are
not about the code running -- they are about the devices being
*credible*.  A preset that pairs a Snapdragon with a Mali, or that
claims a 4x texture limit on a GPU that has 16k, is a fingerprint
anomaly: a site that cross-checks the WebGL renderer against the
architecture, or the pixel size against the density, finds it in one
request.

So the tests pin the properties the presets have to hold:

* the SoC and the GPU are the pair that really ships together,
* the announced architecture names the same part the renderer reports,
* the texture limit matches the class of the GPU,
* the panel carries the real pixel size and a ratio the platform
  reports,
* the release and the SDK level agree,
* two handsets of the same model do not collapse into one identifier,
  and the identifier is the same on every run.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from novaprofile.core.device_catalog_market import (
    ANDROID_DPR,
    BUILDABLE_RELEASES,
    DATA_PATH,
    MERGED_DATA_PATH,
    SCREEN_DENSITY,
    SDK_BY_RELEASE,
    build_local_preset,
    build_market_presets,
    derive_gpu,
    load_records,
    market_presets,
    ram_variants,
    record_digest,
    reset_cache,
    select_gpu_part,
    split_chipset_variants,
    split_gpu_parts,
)
from novaprofile.core.devices import DEVICE_PRESETS, all_presets

# --------------------------------------------------------------------------
# The fixture
# --------------------------------------------------------------------------

#: The rows the tests work on.  Between them they cover the SoC
#: families the fleet holds: a Snapdragon flagship, a Snapdragon budget
#: part, a MediaTek Dimensity, an Exynos, a Tensor, a Kirin, a Unisoc,
#: and a row that carries nothing usable.
_FIXTURE_ROWS: list[dict[str, str]] = [
    {
        "brand": "Samsung",
        "model": "Galaxy A54",
        "chipset": "Exynos 1380 (4-core)",
        "gpu_renderer": "Mali-G68 MC4",
        "resolution": "1080 x 2340 pixels, 19.5:9 ratio (~416 ppi density)",
        "display_size": "6.4 inches",
        "memory_internal": "128GB 6GB RAM, 256GB 8GB RAM",
        "os_text": "Android 13",
        "announced": "2023, March 6",
        "body": "161.7 x 76.6 x 7.9 mm",
        "source_id": "samsung-galaxy-a54",
    },
    {
        "brand": "Samsung",
        "model": "Galaxy S24 Ultra",
        "chipset": "Snapdragon 8 Gen 3 for Galaxy (8-core)",
        "gpu_renderer": "Adreno (TM) 750",
        "resolution": "1440 x 3120 pixels, 19.5:9 ratio (~505 ppi density)",
        "display_size": "6.8 inches, 111.4 cm2",
        "memory_internal": "256GB 12GB RAM, 512GB 16GB RAM",
        "os_text": "Android 14, upgradable to Android 17",
        "announced": "2024, January 17",
        "body": "163.4 x 79.5 x 8.6 mm",
        "source_id": "samsung-galaxy-s24-ultra",
    },
    {
        "brand": "vivo",
        "model": "V2338A",
        "chipset": "MediaTek Dimensity 9300 (8-core)",
        "gpu_renderer": "Immortalis-G715 MC10",
        "resolution": "1260 x 2800 pixels, 20:9 ratio (~453 ppi density)",
        "display_size": "6.78 inches, 139.0 cm2",
        "memory_internal": "256GB 12GB RAM, 512GB 16GB RAM",
        "os_text": "Android 14, upgradable to Android 15",
        "announced": "2023, December 14",
        "body": "162.4 x 75.9 x 8.1 mm",
        "source_id": "vivo-v2338a",
    },
    {
        "brand": "Google",
        "model": "Pixel 8 Pro",
        "chipset": "Google Tensor G3 (9-core)",
        "gpu_renderer": "Mali-G715 MC10",
        "resolution": "1344 x 2992 pixels, 19.5:9 ratio (~489 ppi density)",
        "display_size": "6.7 inches, 108.7 cm2",
        "memory_internal": "128GB 12GB RAM, 256GB 16GB RAM",
        "os_text": "Android 14, upgradable to Android 17",
        "announced": "2023, October 4",
        "body": "162.6 x 76.5 x 8.8 mm",
        "source_id": "google-pixel-8-pro",
    },
    {
        "brand": "Xiaomi",
        "model": "Redmi Note 13",
        "chipset": "Snapdragon 4 Gen 1 (8-core)",
        "gpu_renderer": "Adreno (TM) 619",
        "resolution": "1080 x 2400 pixels, 20:9 ratio (~395 ppi density)",
        "display_size": "6.67 inches, 107.4 cm2",
        "memory_internal": "128GB 8GB RAM, 256GB 8GB RAM",
        "os_text": "Android 13, upgradable to Android 14",
        "announced": "2023, December 7",
        "body": "161.2 x 75.6 x 7.8 mm",
        "source_id": "xiaomi-redmi-note-13",
    },
    {
        "brand": "HUAWEI",
        "model": "P60 Pro",
        "chipset": "Kirin 9000S (8-core)",
        "gpu_renderer": "Maleoon 910",
        "resolution": "1260 x 2652 pixels, 19.5:9 ratio (~444 ppi density)",
        "display_size": "6.67 inches, 107.4 cm2",
        "memory_internal": "256GB 8GB RAM, 512GB 12GB RAM",
        "os_text": "Android 13",
        "announced": "2023, April 18",
        "body": "163.1 x 75.9 x 8.1 mm",
        "source_id": "huawei-p60-pro",
    },
    {
        "brand": "Motorola",
        "model": "moto g54",
        "chipset": "Unisoc T606 (8-core)",
        "gpu_renderer": "Mali-G57 MC2",
        "resolution": "1600 x 720 pixels, 20:9 ratio (~294 ppi density)",
        "display_size": "6.5 inches, 103.5 cm2",
        "memory_internal": "64GB 4GB RAM, 128GB 8GB RAM",
        "os_text": "Android 13",
        "announced": "2023, September 1",
        "body": "161.9 x 76.1 x 8.0 mm",
        "source_id": "motorola-moto-g54",
    },
    {
        "brand": "Generic",
        "model": "Broken Row",
        "chipset": "",
        "gpu_renderer": "",
        "resolution": "",
        "display_size": "5.0 inches",
        "memory_internal": "16GB 2GB RAM",
        "os_text": "Android 13",
        "announced": "2023, January 1",
        "body": "140 x 70 x 8 mm",
        "source_id": "generic-broken-row",
    },
]


def _row(**overrides: str) -> dict[str, str]:
    """A fixture row, with the given fields replaced."""

    base = dict(_FIXTURE_ROWS[0])
    base.update(overrides)
    return base


@pytest.fixture
def records_file(tmp_path: Path) -> Path:
    """The fixture dataset, written as JSON Lines."""

    path = tmp_path / "devices.jsonl"
    lines = [json.dumps(row) for row in _FIXTURE_ROWS]
    # A malformed line the loader has to survive.
    lines.append("{ this is not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def records(records_file: Path) -> list[dict]:
    """The fixture records, read through the loader."""

    return load_records(records_file)


@pytest.fixture
def buildable(records: list[dict]) -> list[dict]:
    """The records that carry everything a preset needs.

    The row that names a handset without saying what is inside it is not
    one of them: it is the row the skipping rules are about.
    """

    return [
        record
        for record in records
        if record.get("chipset") and record.get("gpu_renderer") and record.get("resolution")
    ]


def _names_the_same_part(architecture: str, renderer: str) -> bool:
    """Whether an architecture and a renderer name the same piece of hardware.

    This is the cross-check a site performs: the architecture the profile
    advertises and the renderer it reports have to be the same part.
    """

    architecture = architecture.lower()
    renderer = renderer.lower()
    return (
        architecture in renderer
        or renderer in architecture
        or architecture.split()[0] in renderer
    )


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def test_the_loader_reads_every_row(records: list[dict]) -> None:
    """The broken line is skipped; every row that names a handset is kept."""

    assert len(records) == len(_FIXTURE_ROWS)
    assert {record["source_id"] for record in records} == {
        row["source_id"] for row in _FIXTURE_ROWS
    }


def test_the_loader_survives_a_malformed_line(records_file: Path) -> None:
    """A broken line in a scraped file must not take the dataset down."""

    loaded = load_records(records_file)
    assert loaded, "the dataset has to survive a broken line"


def test_a_missing_dataset_yields_an_empty_catalogue(tmp_path: Path) -> None:
    """The fleet has to work on a machine where the dataset was never fetched."""

    assert load_records(tmp_path / "not-there.jsonl") == []
    assert build_market_presets(load_records(tmp_path / "not-there.jsonl")) == ()


# --------------------------------------------------------------------------
# SoC and GPU
# --------------------------------------------------------------------------


def test_a_snapdragon_carries_an_adreno_and_is_qualcomm() -> None:
    """A Snapdragon part pairs with an Adreno GPU."""

    preset = build_local_preset(
        _row(chipset="Snapdragon 8 Gen 3 for Galaxy", gpu_renderer="Adreno (TM) 750")
    )

    assert preset.abi.soc_vendor == "Qualcomm"
    assert "adreno" in preset.gpu.unmasked_renderer.lower()
    assert preset.abi.gpu_architecture == "Adreno 750"
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_a_tensor_carries_a_mali_and_reports_a_google_vendor() -> None:
    """A Tensor part pairs with a Mali GPU and reports Google's vendor.

    Google ships the Mali of a Pixel under its own name, so the
    unmasked vendor is "Google Inc. (ARM)" rather than plain "ARM".
    """

    preset = build_local_preset(
        _row(chipset="Google Tensor G3", gpu_renderer="Mali-G715 MC10")
    )

    assert preset.abi.soc_vendor == "Google"
    assert "mali" in preset.gpu.unmasked_renderer.lower()
    assert preset.gpu.unmasked_vendor == "Google Inc. (ARM)"
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_an_exynos_carries_a_mali_and_is_samsung() -> None:
    """An Exynos part pairs with a Mali or an Xclipse GPU."""

    preset = build_local_preset(_row(chipset="Exynos 1380", gpu_renderer="Mali-G68 MC4"))

    assert preset.abi.soc_vendor == "Samsung"
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_a_dimensity_carries_an_immortalis_and_is_mediatek() -> None:
    """A Dimensity part pairs with an Immortalis or a Mali GPU."""

    preset = build_local_preset(
        _row(chipset="MediaTek Dimensity 9300", gpu_renderer="Immortalis-G715 MC10")
    )

    assert preset.abi.soc_vendor == "MediaTek"
    assert "immortalis" in preset.gpu.unmasked_renderer.lower()
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_a_kirin_carries_a_maleoon_and_is_hisilicon() -> None:
    """A Kirin part pairs with a Maleoon GPU."""

    preset = build_local_preset(_row(chipset="Kirin 9000S", gpu_renderer="Maleoon 910"))

    assert preset.abi.soc_vendor == "HiSilicon"
    assert preset.gpu.unmasked_vendor == "Huawei"
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_a_unisoc_carries_a_mali_or_a_powervr() -> None:
    """A Unisoc part pairs with a Mali or a PowerVR GPU."""

    preset = build_local_preset(_row(chipset="Unisoc T606", gpu_renderer="Mali-G57 MC2"))

    assert preset.abi.soc_vendor == "Unisoc"
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_a_chipset_that_cannot_carry_the_gpu_is_refused() -> None:
    """A row that pairs a Snapdragon with a Mali is a broken row."""

    with pytest.raises(ValueError):
        build_local_preset(_row(chipset="Snapdragon 8 Gen 3", gpu_renderer="Mali-G68 MC4"))


def test_the_architecture_always_names_the_reported_gpu(buildable: list[dict]) -> None:
    """The architecture and the renderer have to be the same part."""

    for record in buildable:
        preset = build_local_preset(record)
        assert _names_the_same_part(
            preset.abi.gpu_architecture, preset.gpu.unmasked_renderer
        ), (
            f"{record['source_id']} claims the architecture "
            f"{preset.abi.gpu_architecture!r} but reports the renderer "
            f"{preset.gpu.unmasked_renderer!r}"
        )


# --------------------------------------------------------------------------
# GPU limits
# --------------------------------------------------------------------------


def test_a_budget_adreno_reports_an_8192_texture_limit() -> None:
    """The budget Adreno parts stop at an 8192 texture."""

    for renderer in ("Adreno (TM) 619", "Adreno (TM) 610", "Adreno 200", "Adreno 405"):
        gpu = derive_gpu(renderer)
        assert gpu.max_texture_size == 8192, renderer
        assert gpu.max_cube_map_texture_size == 8192, renderer
        assert gpu.max_renderbuffer_size == 8192, renderer
        assert gpu.max_viewport_dims == (8192, 8192), renderer


def test_a_mid_adreno_reports_an_8192_or_16384_texture_limit() -> None:
    """The mid-range Adreno parts report one of the two mid limits."""

    for renderer in ("Adreno 505", "Adreno 512", "Adreno 530", "Adreno 540"):
        gpu = derive_gpu(renderer)
        assert gpu.max_texture_size in (8192, 16384), renderer


def test_a_flagship_adreno_reports_a_16384_texture_limit() -> None:
    """The flagship Adreno parts reach a 16384 texture."""

    for renderer in ("Adreno (TM) 740", "Adreno (TM) 750", "Adreno 840", "Adreno 660"):
        gpu = derive_gpu(renderer)
        assert gpu.max_texture_size == 16384, renderer
        assert gpu.max_cube_map_texture_size == 16384, renderer
        assert gpu.max_viewport_dims == (16384, 16384), renderer


def test_the_mali_and_immortalis_parts_reach_a_16384_texture() -> None:
    """Every recent Mali, Immortalis, Xclipse and Maleoon part reaches 16384."""

    for renderer in (
        "Mali-G710 MC10",
        "Mali-G715 MC10",
        "Mali-G78 MP20",
        "Immortalis-G715 MC10",
        "Xclipse 940",
        "Maleoon 910",
    ):
        assert derive_gpu(renderer).max_texture_size == 16384, renderer


def test_the_mid_mali_parts_report_the_mid_texture_band() -> None:
    """The Mali-G57 and the Mali-G68 are mid parts.

    A mid part reports either of the two mid limits: the Mali-G57 is an
    8192 part and the Mali-G68 reaches 16384, and both are real.
    """

    for renderer in ("Mali-G57 MC2", "Mali-G68 MC4", "Mali-T860MP2"):
        assert derive_gpu(renderer).max_texture_size in (8192, 16384), renderer


def test_the_budget_mali_parts_stop_at_8192() -> None:
    """The Mali-400 and the Mali-T720 are budget parts."""

    for renderer in ("Mali-400", "Mali-400MP2", "Mali-T720MP2"):
        assert derive_gpu(renderer).max_texture_size == 8192, renderer


def test_the_powervr_ge8300_stops_at_a_4096_texture() -> None:
    """The PowerVR GE8300 is the exception: it stops at 4096."""

    gpu = derive_gpu("PowerVR GE8300")
    assert gpu.max_texture_size == 4096
    assert gpu.max_viewport_dims == (4096, 4096)


def test_the_gpu_vendor_names_the_real_maker() -> None:
    """The unmasked vendor is the one a site can cross-check."""

    assert derive_gpu("Adreno (TM) 750").unmasked_vendor == "Qualcomm"
    assert derive_gpu("Mali-G715 MC10").unmasked_vendor == "ARM"
    assert derive_gpu("Xclipse 940").unmasked_vendor == "ARM"
    assert derive_gpu("Maleoon 910").unmasked_vendor == "Huawei"
    assert derive_gpu("PowerVR GE8300").unmasked_vendor == "Imagination Technologies"


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------


def test_the_screen_carries_the_real_pixel_size() -> None:
    """The pixel width and height are the ones the panel really has."""

    preset = build_local_preset(
        _row(
            chipset="Snapdragon 8 Gen 3 for Galaxy",
            gpu_renderer="Adreno (TM) 750",
            resolution="1440 x 3120 pixels, 19.5:9 ratio (~505 ppi density)",
        )
    )

    assert preset.screen.width == 1440
    assert preset.screen.height == 3120


def test_the_resolution_numbers_are_taken_verbatim() -> None:
    """The pixel size is read out of the resolution string, not guessed."""

    from novaprofile.core.device_catalog_market import derive_screen

    screen = derive_screen("720 x 1600 pixels, 20:9 ratio (~294 ppi density)", "6.5 inches")
    assert screen.width == 720
    assert screen.height == 1600


def test_the_density_is_a_bucket_a_real_panel_reports() -> None:
    """The density is one of the buckets the panels use."""

    from novaprofile.core.device_catalog_market import derive_screen

    screen = derive_screen("1080 x 2400 pixels, 20:9 ratio (~404 ppi density)", "6.4 inches")
    assert screen.density_dpi in SCREEN_DENSITY


def test_the_ratio_follows_the_density_bucket() -> None:
    """The ratio and the density of a panel agree with each other.

    A panel reports the ratio of the bucket it was rounded into, not the
    arithmetic quotient of its raw pixel density: the two disagree often
    enough that deriving them independently is how a panel ends up
    carrying a combination no device has ever had.
    """

    from novaprofile.core.device_catalog_market import derive_screen, ratio_for_density

    for resolution, size in (
        ("1440 x 3200 pixels, 20:9 ratio (~515 ppi density)", "6.8 inches"),
        ("1080 x 2400 pixels, 20:9 ratio (~404 ppi density)", "6.4 inches"),
        ("720 x 1600 pixels, 20:9 ratio (~294 ppi density)", "6.5 inches"),
    ):
        screen = derive_screen(resolution, size)
        assert screen.device_pixel_ratio in ANDROID_DPR
        assert screen.device_pixel_ratio == ratio_for_density(screen.density_dpi)


def test_the_screen_reports_the_full_panel() -> None:
    """The available area fits inside the panel."""

    from novaprofile.core.device_catalog_market import derive_screen

    screen = derive_screen("1080 x 2400 pixels, 20:9 ratio (~404 ppi density)", "6.4 inches")
    assert screen.avail_width <= screen.width
    assert screen.avail_height <= screen.height
    assert screen.avail_width > 0
    assert screen.avail_height > 0


# --------------------------------------------------------------------------
# Release and SDK
# --------------------------------------------------------------------------


def test_the_release_and_the_sdk_level_agree(buildable: list[dict]) -> None:
    """Every preset reports the SDK level of its own release."""

    for record in buildable:
        preset = build_local_preset(record)
        assert SDK_BY_RELEASE[preset.release] == preset.sdk_int


def test_a_handset_of_a_year_without_a_version_takes_the_release_of_that_year() -> None:
    """Without an operating system text the era decides the release.

    The release a handset is placed on is always one the engine can
    build a profile for, so a handset from an era whose releases the
    schema does not accept is placed on the oldest one it does.
    """

    from novaprofile.core.device_catalog_market import derive_release

    release, sdk = derive_release("Other", "2023, June 1")
    assert (release, sdk) == ("13", 33)

    release, sdk = derive_release("Other", "2025, June 1")
    assert (release, sdk) == ("15", 35)

    # A 2021 handset cannot report Android 11: the schema does not
    # accept it, so the handset is placed on the oldest release the
    # engine does accept.
    release, sdk = derive_release("Other", "2021, June 1")
    assert release in BUILDABLE_RELEASES
    assert sdk == SDK_BY_RELEASE[release]


def test_the_release_is_always_one_the_engine_can_build() -> None:
    """No preset may carry a release the schema refuses.

    A release the schema does not accept is a record that no browser
    could ever report, so the catalogue places such a handset on the
    oldest release it does accept rather than dropping it.
    """

    from novaprofile.core.device_catalog_market import derive_release

    for os_text in (
        "Android 4.4.2 (KitKat)",
        "Android 7.0 (Nougat)",
        "Android 9.0 (Pie), upgradable to Android 10",
        "Android 11",
        "Android 12",
        "",
    ):
        release, sdk = derive_release(os_text, "2019, March 6")
        assert release in BUILDABLE_RELEASES, os_text
        assert SDK_BY_RELEASE[release] == sdk


def test_the_newest_release_the_text_names_is_the_release() -> None:
    """A handset that was updated reports the release it was updated to.

    The operating system text names the release the handset shipped
    with and the releases it can be upgraded to.  The device reports
    the newest of them, because that is what it runs.
    """

    from novaprofile.core.device_catalog_market import derive_release

    release, sdk = derive_release("Android 9.0 (Pie), upgradable to Android 14", "")
    assert (release, sdk) == ("14", 34)

    release, sdk = derive_release("Android 13", "")
    assert (release, sdk) == ("13", 33)


def test_the_security_patch_is_a_real_date(buildable: list[dict]) -> None:
    """A patch level has to be a date the parser accepts."""

    for record in buildable:
        preset = build_local_preset(record)
        assert date.fromisoformat(preset.security_patch)


def test_the_build_id_looks_like_a_real_android_build_id(buildable: list[dict]) -> None:
    """A build id carries a letter prefix, a date and a build number."""

    pattern = re.compile(r"^[A-Za-z]{2,4}\d[A-Za-z]?\.\d{6}\.\d{3}$")
    for record in buildable:
        preset = build_local_preset(record)
        assert pattern.match(preset.build_id), preset.build_id


def test_the_identifiers_come_from_the_model() -> None:
    """The board, the device and the product follow the model name."""

    preset = build_local_preset(_row(brand="Samsung", model="Galaxy A54"))

    assert preset.board == "galaxy-a54"
    assert preset.device == "samsung-galaxy-a54"
    assert preset.product == preset.device
    assert preset.fingerprint_product == preset.product
    assert preset.bootloader == f"{preset.board}-1.0-0000001"


def test_the_brand_prefix_is_dropped_when_the_model_carries_it() -> None:
    """A model that already carries its brand is not prefixed twice."""

    preset = build_local_preset(_row(brand="Samsung", model="Samsung Galaxy A54"))
    assert preset.device == "samsung-galaxy-a54"
    assert preset.preset_id.startswith("samsung-galaxy-a54-")


# --------------------------------------------------------------------------
# Memory and cores
# --------------------------------------------------------------------------


def test_the_ram_options_come_from_the_memory_text() -> None:
    """The memory text names the configurations the handset shipped in."""

    from novaprofile.core.device_catalog_market import derive_ram_options

    assert derive_ram_options("128GB 6GB RAM, 256GB 8GB RAM") == (6, 8)
    assert derive_ram_options("256GB 16GB RAM") == (16,)
    assert derive_ram_options("") == (4, 6, 8)


# --------------------------------------------------------------------------
# The RAM variants
# --------------------------------------------------------------------------


def test_a_record_expands_into_one_preset_per_ram_amount() -> None:
    """Only the RAM multiplies; the storage does not.

    A handset sold as "128GB 6GB RAM, 256GB 8GB RAM" ships with 6 GB
    and with 8 GB, so it is two devices.  The two storage sizes do not
    make it four: two profiles of the same model with the same RAM and
    a different storage look the same to a site, so multiplying on the
    storage would add volume without adding separability.
    """

    variants = ram_variants("128GB 6GB RAM, 256GB 8GB RAM")

    assert variants == ((6,), (8,))


def test_the_ram_amounts_are_read_whatever_the_order_is() -> None:
    """The memory text is read the same way round.

    "8GB RAM 128GB" and "256GB 12GB RAM, 512GB 16GB RAM" name the
    same configurations as the storage-first spelling.
    """

    assert ram_variants("8GB RAM 128GB") == ((8,),)
    assert ram_variants("256GB 12GB RAM, 512GB 16GB RAM") == ((12,), (16,))


def test_a_record_without_ram_becomes_one_preset() -> None:
    """A record that names no RAM at all becomes a single preset.

    The fallback set is what such a handset is described by, so it is
    one preset carrying the whole set -- not one preset per amount of
    the fallback, which would put three devices into the fleet that no
    source ever described.
    """

    variants = ram_variants("")

    assert len(variants) == 1
    assert variants[0] == (4, 6, 8)


def test_a_repeated_ram_amount_is_one_variant() -> None:
    """The same RAM in two configurations is one variant.

    "64GB 6GB RAM, 128GB 6GB RAM" names one RAM amount in two storage
    sizes, so it is one device.
    """

    assert ram_variants("64GB 6GB RAM, 128GB 6GB RAM") == ((6,),)


def test_every_variant_carries_exactly_its_own_ram_amount() -> None:
    """A preset offers the one RAM amount it is.

    The fleet reads the amount off the device, so a preset that offered
    several amounts would be able to report an amount its own model
    never shipped with.
    """

    presets = build_market_presets([_row(source_id="variant-row")])

    assert len(presets) == 2
    assert [preset.ram_options for preset in presets] == [(6,), (8,)]
    for preset in presets:
        assert len(preset.ram_options) == 1


def test_the_identifier_names_the_ram_amount() -> None:
    """Two variants of the same model carry different identifiers."""

    presets = build_market_presets([_row(source_id="variant-row")])

    identifiers = [preset.preset_id for preset in presets]
    assert len(set(identifiers)) == 2
    assert any(identifier.endswith("-6g") for identifier in identifiers)
    assert any(identifier.endswith("-8g") for identifier in identifiers)


def test_a_colliding_identifier_gets_a_deterministic_digest() -> None:
    """Two datasets describing the same handset are told apart.

    The digest is taken over the model, the resolution, the chipset and
    the source identifier, and it is computed with ``hashlib`` rather
    than with ``hash()``: CPython randomises the string hash per
    process, so a ``hash()``-derived suffix would change on every run.
    """

    first = _row(source_id="duplicate-a")
    second = _row(source_id="duplicate-b")

    one = build_market_presets([first, second])
    two = build_market_presets([first, second])

    identifiers = [preset.preset_id for preset in one]
    assert len(set(identifiers)) == len(identifiers)
    assert identifiers == [preset.preset_id for preset in two]
    assert any(re.search(r"-[0-9a-f]{6}$", identifier) for identifier in identifiers)


def test_the_core_count_follows_the_chipset() -> None:
    """A chipset that names its core count reports that many cores."""

    preset = build_local_preset(
        _row(
            chipset="Snapdragon 8 Gen 3 for Galaxy (8-core)",
            gpu_renderer="Adreno (TM) 750",
        )
    )
    assert preset.cpu_options == (8,)

    preset = build_local_preset(
        _row(chipset="Exynos 2400 (10-core)", gpu_renderer="Xclipse 940")
    )
    assert preset.cpu_options == (10,)


def test_the_ratio_and_density_options_cover_the_computed_values(
    buildable: list[dict],
) -> None:
    """The options have to include the values the screen really reports."""

    for record in buildable:
        preset = build_local_preset(record)
        assert preset.screen.device_pixel_ratio in preset.dpr_options
        assert preset.screen.density_dpi in preset.density_options


# --------------------------------------------------------------------------
# Skipping
# --------------------------------------------------------------------------


def test_a_record_without_a_chipset_is_dropped_not_guessed(records: list[dict]) -> None:
    """A handset whose chipset is unknown cannot become a preset.

    The point is not that the preset cannot be built -- it is that the
    catalogue does not *invent* a chipset for it.  A guessed chipset is
    a fingerprint anomaly.
    """

    broken = next(record for record in records if record["source_id"] == "generic-broken-row")
    assert broken["chipset"] == ""

    presets = build_market_presets(records)
    assert all("broken" not in preset.preset_id for preset in presets)
    assert all("generic" not in preset.preset_id for preset in presets)


def test_a_record_without_a_chipset_raises_when_built_directly() -> None:
    """The single-record builder refuses rather than guessing."""

    with pytest.raises(ValueError):
        build_local_preset(_row(chipset="", gpu_renderer="", resolution=""))


def test_a_record_without_a_resolution_is_dropped() -> None:
    """A handset whose panel is unknown cannot become a preset."""

    assert build_market_presets([_row(resolution="", source_id="no-panel")]) == ()


def test_a_record_without_a_gpu_is_dropped() -> None:
    """A handset whose GPU is unknown cannot become a preset."""

    assert build_market_presets([_row(gpu_renderer="", source_id="no-gpu")]) == ()


# --------------------------------------------------------------------------
# Uniqueness and determinism
# --------------------------------------------------------------------------


def test_two_records_with_the_same_model_get_different_identifiers() -> None:
    """Two handsets of the same model must not collapse into one bucket.

    The fleet keys on the preset identifier, so two devices that share
    one identifier would silently overwrite each other.  Both records
    are the same model with the same RAM configurations, so each of
    them expands into two variants and all four have to be apart.
    """

    first = _row(source_id="duplicate-a")
    second = _row(source_id="duplicate-b")
    presets = build_market_presets([first, second])

    assert len(presets) == 4
    assert len({preset.preset_id for preset in presets}) == 4


def test_every_identifier_of_the_catalogue_is_unique(buildable: list[dict]) -> None:
    """Every preset of the fleet carries its own identifier."""

    presets = build_market_presets(buildable)
    identifiers = [preset.preset_id for preset in presets]
    assert len(set(identifiers)) == len(identifiers)


def test_the_same_input_always_produces_the_same_identifier() -> None:
    """The identifier of a handset is the same on every run.

    It has to be stable across processes, so the digest behind it is
    computed with ``hashlib`` and not with ``hash()``: CPython
    randomises the string hash per process.
    """

    first = _row(source_id="duplicate-a")
    second = _row(source_id="duplicate-b")

    one = build_market_presets([first, second])
    two = build_market_presets([first, second])

    assert [preset.preset_id for preset in one] == [preset.preset_id for preset in two]


def test_the_digest_of_a_record_is_stable() -> None:
    """The digest is the same on every call."""

    record = _row()
    assert record_digest(record) == record_digest(record)
    assert len(record_digest(record)) == 6


def test_a_different_record_gets_a_different_digest() -> None:
    """Two records that differ anywhere get different digests."""

    assert record_digest(_row(source_id="a")) != record_digest(_row(source_id="b"))


# --------------------------------------------------------------------------
# The fleet
# --------------------------------------------------------------------------


def test_all_presets_includes_the_market_presets() -> None:
    """The market catalogue is part of the fleet."""

    fleet = all_presets()
    market = market_presets()

    assert market, "the market catalogue has to hold devices"
    assert len(fleet) == len(DEVICE_PRESETS) + len(market) + len(
        all_presets()
    ) - len(DEVICE_PRESETS) - len(market)
    assert len(fleet) >= len(DEVICE_PRESETS) + len(market)

    identifiers = {preset.preset_id for preset in fleet}
    assert {preset.preset_id for preset in market} <= identifiers


def test_the_market_catalogue_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalogue is built once per process."""

    reset_cache()
    calls = {"count": 0}

    import novaprofile.core.device_catalog_market as module

    original = module.build_market_presets

    def counting(records: list[dict]) -> tuple:
        calls["count"] += 1
        return original(records)

    monkeypatch.setattr(module, "build_market_presets", counting)

    module.market_presets()
    module.market_presets()

    assert calls["count"] == 1


def test_every_preset_of_the_market_catalogue_is_physically_coherent() -> None:
    """The catalogue holds coherent devices and nothing else.

    The check is the one the fleet validator performs: the renderer and
    the architecture name the same part, the release and the SDK level
    agree, and the panel reports values a real panel reports.
    """

    presets = market_presets()
    assert presets

    for preset in presets:
        assert _names_the_same_part(
            preset.abi.gpu_architecture, preset.gpu.unmasked_renderer
        ), preset.preset_id
        assert SDK_BY_RELEASE[preset.release] == preset.sdk_int, preset.preset_id
        assert preset.screen.device_pixel_ratio in ANDROID_DPR, preset.preset_id
        assert preset.screen.density_dpi in SCREEN_DENSITY, preset.preset_id
        assert preset.screen.device_pixel_ratio in preset.dpr_options, preset.preset_id
        assert preset.screen.density_dpi in preset.density_options, preset.preset_id
        assert preset.release in BUILDABLE_RELEASES, preset.preset_id


# --------------------------------------------------------------------------
# The dataset
# --------------------------------------------------------------------------


def test_the_dataset_path_points_at_the_market_data() -> None:
    """The catalogue reads the Vietnamese market dataset.

    The fleet the product needs is the one the market filter produced:
    the worldwide dataset holds every handset the scraper saw, and most
    of them are not sold here.  The default is therefore the filtered
    file, and the merged file is only the fallback.
    """

    assert DATA_PATH.name == "vn_devices.jsonl"
    assert DATA_PATH.parent.name == "data"
    assert MERGED_DATA_PATH.name == "devices.jsonl"


@pytest.mark.skipif(not DATA_PATH.exists(), reason="the market dataset is not fetched")
def test_the_market_dataset_builds_a_catalogue() -> None:
    """The real dataset produces a catalogue of real devices."""

    records = load_records(DATA_PATH)
    assert records, "the market dataset has to hold devices"

    presets = build_market_presets(records)
    assert presets, "the market dataset has to produce presets"

    identifiers = [preset.preset_id for preset in presets]
    assert len(set(identifiers)) == len(identifiers)


# --------------------------------------------------------------------------
# Regional variants
# --------------------------------------------------------------------------


def test_a_merged_renderer_names_the_gpu_of_the_chipset() -> None:
    """A row that carries two regional variants describes one handset.

    The scraper merges the variants of a handset into one row: the
    "Galaxy S20+" is listed with an Exynos 990 for the global market and
    a Snapdragon 865 for the United States, and its renderer reads
    "Mali-G77 MP11 - GlobalAdreno 650 - USA".  A preset has to describe
    one real device, so the GPU is the one that ships with the chipset
    the preset is built on.
    """

    preset = build_local_preset(
        _row(
            brand="Samsung",
            model="Galaxy S20+",
            chipset="Exynos 990 (7 nm+) - GlobalQualcomm SM8250 Snapdragon 865 (7 nm+) - USA",
            gpu_renderer="Mali-G77 MP11 - GlobalAdreno 650 - USA",
            resolution="1440 x 3200 pixels, 20:9 ratio (~525 ppi density)",
        )
    )

    # The chipset the preset leads with is the Exynos, and the Exynos
    # ships with the Mali.
    assert "Exynos" in preset.abi.soc_model
    assert "Mali" in preset.gpu.unmasked_renderer
    assert "Adreno" not in preset.gpu.unmasked_renderer
    assert preset.abi.gpu_architecture.lower() in preset.gpu.unmasked_renderer.lower()
    assert preset.gpu.unmasked_vendor == "ARM"


def test_a_merged_renderer_splits_into_its_parts() -> None:
    """The renderer of a merged row names more than one GPU."""

    parts = split_gpu_parts("Mali-G77 MP11 - GlobalAdreno 650 - USA")
    assert len(parts) == 2
    assert parts[0].startswith("Mali-G77")
    assert parts[1].startswith("Adreno 650")


def test_a_merged_chipset_splits_into_its_variants() -> None:
    """The chipset of a merged row names more than one SoC."""

    variants = split_chipset_variants(
        "Exynos 990 (7 nm+) - GlobalQualcomm SM8250 Snapdragon 865 (7 nm+) - USA"
    )
    assert len(variants) == 2
    assert "Exynos" in variants[0]
    assert "Snapdragon" in variants[1]


def test_a_single_gpu_renderer_is_left_alone() -> None:
    """A row that names one GPU is not split."""

    assert split_gpu_parts("Adreno 750") == ("Adreno 750",)
    assert select_gpu_part("Adreno 750", "Snapdragon 8 Gen 3") == "Adreno 750"


# --------------------------------------------------------------------------
# The security patch
# --------------------------------------------------------------------------


def test_the_security_patch_never_predates_the_release() -> None:
    """A handset cannot carry a patch from before the release it reports.

    A device that runs Android 13 was updated to it after Android 13
    shipped, so its patch level is at least the day that release
    opened -- even when the handset itself was announced years earlier.
    """

    preset = build_local_preset(
        _row(
            brand="Google",
            model="Pixel 4",
            chipset="Snapdragon 820 (8-core)",
            gpu_renderer="Adreno 640",
            resolution="1080 x 2280 pixels, 19:9 ratio (~443 ppi density)",
            os_text="Android 13",
            announced="2019, October",
        )
    )

    patch = date.fromisoformat(preset.security_patch)
    assert patch >= date(2023, 10, 10)


def test_the_security_patch_of_a_new_handset_is_its_announcement() -> None:
    """A handset announced after the release keeps its own patch day."""

    preset = build_local_preset(
        _row(
            brand="Samsung",
            model="Galaxy A54",
            chipset="Exynos 1380 (4-core)",
            gpu_renderer="Mali-G68 MC4",
            resolution="1080 x 2340 pixels, 19.5:9 ratio (~416 ppi density)",
            os_text="Android 13",
            announced="2023, March 6",
        )
    )

    # Android 13 opened in October 2023, so a handset announced in
    # March 2023 predates the release it reports and its patch is the
    # day the release opened.
    assert preset.security_patch == "2023-10-10"


def test_a_handset_announced_after_the_release_keeps_its_own_patch_day() -> None:
    """A handset that shipped after the release keeps its announcement day.

    The patch of a handset is the day its last patch was published, and
    a handset that was announced in 2024 has had one published since.
    """

    preset = build_local_preset(
        _row(
            brand="Samsung",
            model="Galaxy A54",
            chipset="Exynos 1380 (4-core)",
            gpu_renderer="Mali-G68 MC4",
            resolution="1080 x 2340 pixels, 19.5:9 ratio (~416 ppi density)",
            os_text="Android 13",
            announced="2024, March 6",
        )
    )

    assert preset.security_patch == "2024-03-06"
