"""The device specification source.

This module is what turns the scraped GSMArena specification rows into
Android device presets.  The tests below do not check that the module
*runs* -- they check that the presets it produces are physically
coherent, because a preset that is not coherent is a fingerprint
anomaly: a site that cross-checks the WebGL renderer against the
architecture, or the pixel size against the density, finds it in one
request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novaprofile.core.device_spec_source import (
    DeviceRecord,
    build_preset,
    build_presets,
    derive_abi,
    derive_gpu,
    derive_ram_options,
    derive_release,
    derive_screen,
    load_device_records,
)

# --------------------------------------------------------------------------
# The fixture
# --------------------------------------------------------------------------

#: The rows the tests work on.  Between them they cover the SoC families
#: the fleet holds: a Snapdragon flagship, a Snapdragon budget part, a
#: MediaTek Dimensity, an Exynos, a Tensor, and a row that carries
#: nothing usable.
_FIXTURE_ROWS: list[dict[str, str]] = [
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
        "brand": "Samsung",
        "model": "Galaxy S24",
        "chipset": "Exynos 2400 (10-core)",
        "gpu_renderer": "Xclipse 940",
        "resolution": "1080 x 2340 pixels, 19.5:9 ratio (~416 ppi density)",
        "display_size": "6.2 inches, 92.8 cm2",
        "memory_internal": "256GB 8GB RAM, 512GB 12GB RAM",
        "os_text": "Android 14, upgradable to Android 16",
        "announced": "2024, January 17",
        "body": "147.0 x 70.9 x 8.4 mm",
        "source_id": "samsung-galaxy-s24",
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
        "brand": "Generic",
        "model": "Broken Row",
        "chipset": "",
        "gpu_renderer": "",
        "resolution": "",
        "display_size": "5.0 inches",
        "memory_internal": "16GB 2GB RAM",
        "os_text": "Android 10",
        "announced": "2020, January 1",
        "body": "140 x 70 x 8 mm",
        "source_id": "generic-broken-row",
    },
]


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
def records(records_file: Path) -> list[DeviceRecord]:
    """The fixture records, read through the loader."""

    return load_device_records(records_file)


@pytest.fixture
def buildable(records: list[DeviceRecord]) -> list[DeviceRecord]:
    """The records that carry everything a preset needs.

    The row that names a handset without saying what is inside it is not
    one of them: it is the row the skipping rules are about.
    """

    return [
        record
        for record in records
        if record.chipset and record.gpu_renderer and record.resolution
    ]


def _record(**overrides: str) -> DeviceRecord:
    """A record built from the fixture, with the given fields replaced."""

    base = dict(_FIXTURE_ROWS[0])
    base.update(overrides)
    return DeviceRecord(
        brand=base["brand"],
        model=base["model"],
        chipset=base["chipset"],
        gpu_renderer=base["gpu_renderer"],
        resolution=base["resolution"],
        display_size=base["display_size"],
        memory_internal=base["memory_internal"],
        os_text=base["os_text"],
        announced=base["announced"],
        body=base["body"],
        source_id=base["source_id"],
    )


def _names_the_same_part(architecture: str, renderer: str) -> bool:
    """Whether an architecture and a renderer name the same piece of hardware.

    This is the cross-check the consistency resolver performs: the
    architecture the profile advertises and the renderer it reports have
    to be the same part.
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


def test_the_loader_reads_every_row(records: list[DeviceRecord]) -> None:
    """The broken line is skipped; every row that names a handset is kept."""

    assert len(records) == 6
    assert {record.source_id for record in records} == {
        "samsung-galaxy-s24-ultra",
        "xiaomi-redmi-note-13",
        "vivo-v2338a",
        "samsung-galaxy-s24",
        "google-pixel-8-pro",
        "generic-broken-row",
    }


def test_the_loader_survives_a_malformed_line(records_file: Path) -> None:
    """A broken line in a scraped file must not take the dataset down."""

    loaded = load_device_records(records_file)
    assert loaded, "the dataset has to survive a broken line"


# --------------------------------------------------------------------------
# SoC and GPU
# --------------------------------------------------------------------------


def test_a_snapdragon_carries_an_adreno_and_is_qualcomm(records: list[DeviceRecord]) -> None:
    """A Snapdragon part pairs with an Adreno GPU."""

    flagship = next(r for r in records if r.source_id == "samsung-galaxy-s24-ultra")
    preset = build_preset(flagship)

    assert preset.abi.soc_vendor == "Qualcomm"
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)
    assert "adreno" in preset.gpu.unmasked_renderer.lower()
    assert preset.abi.gpu_architecture == "Adreno 750"


def test_a_dimensity_carries_an_immortalis_and_is_mediatek(
    records: list[DeviceRecord],
) -> None:
    """A Dimensity part pairs with an Immortalis or a Mali GPU."""

    dimensity = next(r for r in records if r.source_id == "vivo-v2338a")
    preset = build_preset(dimensity)

    assert preset.abi.soc_vendor == "MediaTek"
    assert "immortalis" in preset.gpu.unmasked_renderer.lower()
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_an_exynos_carries_an_xclipse_and_is_samsung(records: list[DeviceRecord]) -> None:
    """An Exynos part pairs with an Xclipse GPU."""

    exynos = next(r for r in records if r.source_id == "samsung-galaxy-s24")
    preset = build_preset(exynos)

    assert preset.abi.soc_vendor == "Samsung"
    assert "xclipse" in preset.gpu.unmasked_renderer.lower()
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_a_tensor_carries_a_mali_and_is_google(records: list[DeviceRecord]) -> None:
    """A Tensor part pairs with a Mali GPU."""

    tensor = next(r for r in records if r.source_id == "google-pixel-8-pro")
    preset = build_preset(tensor)

    assert preset.abi.soc_vendor == "Google"
    assert "mali" in preset.gpu.unmasked_renderer.lower()
    assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer)


def test_the_soc_vendor_is_always_one_of_the_allowed_values(
    buildable: list[DeviceRecord],
) -> None:
    """The ABI model only accepts seven vendors; nothing else may reach it."""

    allowed = {"ARM", "Qualcomm", "MediaTek", "Samsung", "Google", "Unisoc", "HiSilicon"}
    for record in buildable:
        preset = build_preset(record)
        assert preset.abi.soc_vendor in allowed


def test_the_architecture_always_names_the_reported_gpu(
    buildable: list[DeviceRecord],
) -> None:
    """The architecture and the renderer have to be the same part.

    This is the cross-check a site performs: it reads the WebGL
    renderer and compares it against the architecture the profile
    advertises.
    """
    for record in buildable:
        preset = build_preset(record)
        assert _names_the_same_part(preset.abi.gpu_architecture, preset.gpu.unmasked_renderer), (
            f"{record.source_id} claims the architecture "
            f"{preset.abi.gpu_architecture!r} but reports the renderer "
            f"{preset.gpu.unmasked_renderer!r}"
        )


def test_the_abi_lists_partition_the_supported_ones() -> None:
    """The ABI lists have to partition the supported list exactly.

    The resolver rejects a profile whose 64-bit and 32-bit lists do not
    add up to the supported ones, and a primary ABI that is not among
    them.
    """

    abi = derive_abi("Snapdragon 8 Gen 3 for Galaxy", "Adreno (TM) 750")

    assert abi.primary_abi in abi.supported_abis
    partition = set(abi.supported_64_bit_abis) | set(abi.supported_32_bit_abis)
    assert partition == set(abi.supported_abis)
    assert abi.is_64_bit == (abi.primary_abi in abi.supported_64_bit_abis)
    assert abi.soc_model == "Snapdragon 8 Gen 3 for Galaxy"
    assert abi.cpu_cluster_layout


def test_the_abi_names_the_vendor_of_the_chipset() -> None:
    """The vendor comes from the chipset, not from the renderer."""

    assert derive_abi("Exynos 2400", "Xclipse 940").soc_vendor == "Samsung"
    assert derive_abi("Dimensity 9300", "Immortalis-G715 MC10").soc_vendor == "MediaTek"
    assert derive_abi("Tensor G3", "Mali-G715 MC10").soc_vendor == "Google"
    assert derive_abi("Kirin 9000S", "Maleoon 910").soc_vendor == "HiSilicon"
    assert derive_abi("Snapdragon 8 Gen 2", "Adreno (TM) 740").soc_vendor == "Qualcomm"


# --------------------------------------------------------------------------
# GPU limits
# --------------------------------------------------------------------------


def test_a_budget_adreno_reports_an_8192_texture_limit() -> None:
    """The budget Adreno parts stop at an 8192 texture."""

    gpu = derive_gpu("Adreno (TM) 619")
    assert gpu.max_texture_size == 8192
    assert gpu.max_cube_map_texture_size == 8192
    assert gpu.max_renderbuffer_size == 8192
    assert gpu.max_viewport_dims == (8192, 8192)
    assert gpu.max_samples == 4


def test_a_mid_adreno_reports_an_8192_texture_limit() -> None:
    """The mid-range Adreno parts stop at an 8192 texture."""

    for renderer in ("Adreno (TM) 730", "Adreno (TM) 732", "Adreno (TM) 735"):
        gpu = derive_gpu(renderer)
        assert gpu.max_texture_size == 8192, renderer


def test_a_flagship_adreno_reports_a_16384_texture_limit() -> None:
    """The flagship Adreno parts reach a 16384 texture."""

    for renderer in ("Adreno (TM) 740", "Adreno (TM) 750", "Adreno (TM) 840"):
        gpu = derive_gpu(renderer)
        assert gpu.max_texture_size == 16384, renderer
        assert gpu.max_cube_map_texture_size == 16384
        assert gpu.max_renderbuffer_size == 16384
        assert gpu.max_viewport_dims == (16384, 16384)


def test_the_mali_and_immortalis_parts_reach_a_16384_texture() -> None:
    """Every Mali, Immortalis, Xclipse and Maleoon part reaches 16384."""

    for renderer in (
        "Mali-G57 MC2",
        "Mali-G610 MC6",
        "Mali-G615 MC2",
        "Mali-G68 MC4",
        "Mali-G710 MC10",
        "Mali-G715 MC10",
        "Immortalis-G715 MC10",
        "Xclipse 920",
        "Xclipse 940",
        "Maleoon 910",
    ):
        gpu = derive_gpu(renderer)
        assert gpu.max_texture_size == 16384, renderer


def test_the_powervr_part_stops_at_a_4096_texture() -> None:
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


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------


def test_the_screen_carries_the_real_pixel_size(records: list[DeviceRecord]) -> None:
    """The pixel width and height are the ones the panel really has."""

    flagship = next(r for r in records if r.source_id == "samsung-galaxy-s24-ultra")
    preset = build_preset(flagship)

    assert preset.screen.width == 1440
    assert preset.screen.height == 3120

    budget = next(r for r in records if r.source_id == "xiaomi-redmi-note-13")
    budget_preset = build_preset(budget)
    assert budget_preset.screen.width == 1080
    assert budget_preset.screen.height == 2400


def test_the_resolution_numbers_are_taken_verbatim() -> None:
    """The pixel size is read out of the resolution string, not guessed."""

    screen = derive_screen("720 x 1600 pixels, 20:9 ratio (~294 ppi density)", "6.5 inches")
    assert screen.width == 720
    assert screen.height == 1600


def test_the_ratio_follows_the_pixel_density() -> None:
    """The device pixel ratio is the one the density bucket reports.

    The ratio is not the pixel density over 160: the platform rounds
    the raw density onto a bucket and the ratio follows that bucket.
    A 515 ppi panel is a 520 dpi bucket, which reports 3.25 and not the
    3.22 the division would give.
    """

    dense = derive_screen("1440 x 3200 pixels, 20:9 ratio (~515 ppi density)", "6.8 inches")
    assert dense.device_pixel_ratio == 3.25

    middle = derive_screen("1080 x 2400 pixels, 20:9 ratio (~404 ppi density)", "6.4 inches")
    assert middle.device_pixel_ratio == 2.5

    coarse = derive_screen("720 x 1600 pixels, 20:9 ratio (~294 ppi density)", "6.5 inches")
    assert coarse.device_pixel_ratio == 1.75
    assert coarse.density_dpi == 280


def test_the_ratio_and_the_density_always_agree() -> None:
    """The pair a panel reports is one the platform actually reports.

    The buckets are not evenly spaced, so the two numbers are not one
    number wearing two names: the 213 bucket reports 1.5 and not the
    1.333x of its arithmetic quotient, and the 411 and the 420 buckets
    both report 2.625.
    """

    for ppi, density, ratio in (
        ("~120 ppi density", 120, 1.0),
        ("~160 ppi density", 160, 1.0),
        ("~213 ppi density", 213, 1.5),
        ("~240 ppi density", 240, 1.5),
        ("~280 ppi density", 280, 1.75),
        ("~320 ppi density", 320, 2.0),
        ("~360 ppi density", 360, 2.25),
        ("~400 ppi density", 400, 2.5),
        ("~411 ppi density", 411, 2.625),
        ("~420 ppi density", 420, 2.625),
        ("~440 ppi density", 440, 2.75),
        ("~480 ppi density", 480, 3.0),
        ("~520 ppi density", 520, 3.25),
        ("~525 ppi density", 525, 3.25),
        ("~545 ppi density", 545, 3.5),
        ("~560 ppi density", 560, 3.5),
        ("~640 ppi density", 640, 4.0),
    ):
        screen = derive_screen(f"1080 x 2400 pixels, 20:9 ratio ({ppi})", "6.4 inches")
        assert screen.density_dpi == density, ppi
        assert screen.device_pixel_ratio == ratio, ppi


def test_the_density_is_a_bucket_a_real_panel_reports() -> None:
    """The density is rounded onto the buckets the panels use."""

    screen = derive_screen("1080 x 2400 pixels, 20:9 ratio (~404 ppi density)", "6.4 inches")
    assert screen.density_dpi == 400


def test_the_screen_reports_the_full_panel() -> None:
    """The available area fits inside the panel."""

    screen = derive_screen("1080 x 2400 pixels, 20:9 ratio (~404 ppi density)", "6.4 inches")
    assert screen.avail_width <= screen.width
    assert screen.avail_height <= screen.height
    assert screen.avail_width > 0
    assert screen.avail_height > 0


# --------------------------------------------------------------------------
# Release and SDK
# --------------------------------------------------------------------------


def test_android_14_is_sdk_34() -> None:
    """The release and the SDK level have to agree."""

    release, sdk = derive_release("Android 14, upgradable to Android 17", "2024, January 17")
    assert release == "14"
    assert sdk == 34


def test_the_first_android_version_is_the_one_that_shipped() -> None:
    """The versions the handset can be upgraded to are not what it reports."""

    release, sdk = derive_release("Android 13, upgradable to Android 17", "2023, June 1")
    assert release == "13"
    assert sdk == 33


def test_the_whole_sdk_table_is_honoured() -> None:
    """Every release the fleet covers has its SDK level."""

    for release, sdk in (
        ("16", 36),
        ("15", 35),
        ("14", 34),
        ("13", 33),
        ("12", 31),
        ("11", 30),
        ("10", 29),
        ("9", 28),
    ):
        got_release, got_sdk = derive_release(f"Android {release}", "2024, January 17")
        assert (got_release, got_sdk) == (release, sdk)


def test_a_release_without_a_version_follows_the_announcement_year() -> None:
    """Without an operating system text the era decides the release."""

    release, sdk = derive_release("Other", "2018, September 20")
    assert release == "9"
    assert sdk == 28


def test_the_preset_carries_the_matching_sdk_level(
    buildable: list[DeviceRecord],
) -> None:
    """Every preset reports the SDK level of its own release."""

    expected = {"12": 31, "13": 33, "14": 34, "15": 35, "16": 36}
    for record in buildable:
        preset = build_preset(record)
        assert expected[preset.release] == preset.sdk_int


# --------------------------------------------------------------------------
# The build identifiers
# --------------------------------------------------------------------------


def test_the_build_id_carries_the_announcement_date(records: list[DeviceRecord]) -> None:
    """The build id is dated the day the handset was announced."""

    flagship = next(r for r in records if r.source_id == "samsung-galaxy-s24-ultra")
    preset = build_preset(flagship)
    # The build id carries the day the handset was announced as YYMMDD.
    assert ".240117." in preset.build_id
    assert preset.build_id.split(".")[0].endswith("A")


def test_the_security_patch_is_a_real_date(buildable: list[DeviceRecord]) -> None:
    """A patch level has to be a date the parser accepts."""

    from datetime import date

    for record in buildable:
        preset = build_preset(record)
        assert date.fromisoformat(preset.security_patch)


def test_the_identifiers_are_derived_from_the_model(records: list[DeviceRecord]) -> None:
    """The board, the device and the product follow the model name."""

    flagship = next(r for r in records if r.source_id == "samsung-galaxy-s24-ultra")
    preset = build_preset(flagship)

    assert preset.device == "samsung-galaxy-s24-ultra"
    assert preset.product == preset.device
    assert preset.hardware == preset.board
    assert preset.fingerprint_product == preset.product
    assert preset.bootloader.startswith(preset.board)
    assert preset.bootloader.endswith("-1.0-0000001")


def test_the_identifiers_are_never_empty(buildable: list[DeviceRecord]) -> None:
    """An empty identifier is an empty fingerprint segment."""

    for record in buildable:
        preset = build_preset(record)
        for value in (
            preset.preset_id,
            preset.manufacturer,
            preset.brand,
            preset.model,
            preset.device,
            preset.product,
            preset.hardware,
            preset.board,
            preset.bootloader,
            preset.fingerprint_product,
            preset.build_id,
            preset.version_incremental,
            preset.security_patch,
        ):
            assert value
            assert isinstance(value, str)


def test_the_preset_id_is_a_lowercase_slug(buildable: list[DeviceRecord]) -> None:
    """The identifier has to survive being used in a URL and a file name."""

    for record in buildable:
        preset = build_preset(record)
        assert preset.preset_id == preset.preset_id.lower()
        assert " " not in preset.preset_id


def test_the_fingerprint_is_shaped_like_a_real_one(
    buildable: list[DeviceRecord],
) -> None:
    """The fingerprint the preset builds has to carry all four segments."""

    for record in buildable:
        preset = build_preset(record)
        fingerprint = preset.build_fingerprint()
        parts = fingerprint.split(":")
        assert len(parts) == 4
        assert "/" in parts[0]
        assert parts[1] == f"{preset.release}/{preset.build_id}.{preset.version_incremental}"
        assert parts[2].isdigit()


# --------------------------------------------------------------------------
# Memory and cores
# --------------------------------------------------------------------------


def test_the_ram_options_come_from_the_memory_text() -> None:
    """The memory text names the configurations the handset shipped in."""

    assert derive_ram_options("128GB 8GB RAM, 256GB 12GB RAM") == (8, 12)
    assert derive_ram_options("256GB 16GB RAM") == (16,)
    assert derive_ram_options("") == (4, 6, 8)


def test_the_ram_options_are_never_empty(buildable: list[DeviceRecord]) -> None:
    """A fleet of handsets that all report the same RAM is a bucket."""

    for record in buildable:
        preset = build_preset(record)
        assert preset.ram_options
        assert all(value > 0 for value in preset.ram_options)


def test_the_core_count_follows_the_chipset(records: list[DeviceRecord]) -> None:
    """A chipset that names its core count reports that many cores."""

    flagship = next(r for r in records if r.source_id == "samsung-galaxy-s24-ultra")
    assert build_preset(flagship).cpu_options == (8,)

    exynos = next(r for r in records if r.source_id == "samsung-galaxy-s24")
    assert build_preset(exynos).cpu_options == (10,)


def test_the_ratio_and_density_options_cover_the_computed_values(
    buildable: list[DeviceRecord],
) -> None:
    """The options have to include the values the screen really reports."""

    for record in buildable:
        preset = build_preset(record)
        assert preset.screen.device_pixel_ratio in preset.dpr_options
        assert preset.screen.density_dpi in preset.density_options


# --------------------------------------------------------------------------
# Skipping and uniqueness
# --------------------------------------------------------------------------


def test_a_record_without_a_chipset_is_skipped(records: list[DeviceRecord]) -> None:
    """A handset whose chipset is unknown cannot become a preset."""

    broken = next(r for r in records if r.source_id == "generic-broken-row")
    assert broken.chipset == ""

    presets = build_presets(records)
    assert all("broken" not in preset.preset_id for preset in presets)
    assert all("generic" not in preset.preset_id for preset in presets)


def test_a_record_without_a_chipset_raises_when_built_directly() -> None:
    """The single-record builder refuses rather than guessing."""

    with pytest.raises(ValueError):
        build_preset(_record(chipset="", gpu_renderer="", resolution=""))


def test_a_record_without_a_resolution_is_skipped() -> None:
    """A handset whose panel is unknown cannot become a preset."""

    presets = build_presets([_record(resolution="", source_id="no-panel")])
    assert presets == ()


def test_a_record_the_engine_cannot_build_is_skipped() -> None:
    """A handset on an Android release the engine cannot build is skipped.

    The Android build model accepts the releases 13, 14 and 15 only, so
    a preset that claims another one could never become a profile.
    """

    presets = build_presets([_record(os_text="Android 12", source_id="too-old")])
    assert presets == ()

    with pytest.raises(ValueError):
        build_preset(_record(os_text="Android 12", source_id="too-old"))


def test_a_record_without_a_gpu_is_skipped() -> None:
    """A handset whose GPU is unknown cannot become a preset."""

    presets = build_presets([_record(gpu_renderer="", source_id="no-gpu")])
    assert presets == ()


def test_two_handlets_with_the_same_model_get_different_identifiers() -> None:
    """Two rows of the same model must not collapse into one bucket."""

    first = _record(source_id="duplicate-a")
    second = _record(source_id="duplicate-b")
    presets = build_presets([first, second])

    assert len(presets) == 2
    assert presets[0].preset_id != presets[1].preset_id


def test_every_preset_becomes_a_valid_android_build(
    buildable: list[DeviceRecord],
) -> None:
    """The preset has to turn into the build the engine hands to a browser.

    The Android build model accepts the release, the SDK level and the
    security patch of the preset, and it checks the shape of the
    fingerprint.  A preset that does not survive that check is a device
    the engine could never report.
    """

    for record in buildable:
        preset = build_preset(record)
        build = preset.android_build()
        assert build.version_release == preset.release
        assert build.sdk_int == preset.sdk_int
        assert build.security_patch == preset.security_patch


def test_the_identifiers_of_the_whole_fleet_are_unique(
    records: list[DeviceRecord],
) -> None:
    """Every preset of the fleet has to carry its own identifier."""

    presets = build_presets(records)
    identifiers = [preset.preset_id for preset in presets]
    assert len(set(identifiers)) == len(identifiers)


def test_the_fleet_holds_every_usable_record(records: list[DeviceRecord]) -> None:
    """Nothing that can become a preset is dropped."""

    presets = build_presets(records)
    assert len(presets) == 5
