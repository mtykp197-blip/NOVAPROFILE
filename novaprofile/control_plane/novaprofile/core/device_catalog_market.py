"""The market catalogue: the devices the Vietnamese market actually sells.

The reference catalogue in :mod:`novaprofile.core.devices` holds eleven
hand-written devices, and :mod:`novaprofile.core.device_catalog_extended`
holds thirty-one more.  Both were written by hand, one entry at a time.

Three thousand seven hundred and seventy-one devices cannot be written by
hand, but they can be *read*.  The specifications of every handset that
reaches the Vietnamese market are public: the SoC, the GPU it carries,
the panel it ships with, the memory it was sold in and the Android
release it shipped with.  This module reads that dataset and turns each
row into a :class:`~novaprofile.core.devices.DevicePreset`.

The point is not to have more devices.  It is to have *credible* ones: a
fleet that claims to be Vietnamese but carries handsets nobody here owns
is separated at a glance, not because any field is wrong, but because the
combination of fields is something that never happens.  The market filter
in :mod:`novaprofile.core_patch.market_filter` keeps the rows whose
*line* is bought here -- the Galaxy A series against the Galaxy S
series, the Redmi Note line against the Redmi K line -- and this module
turns the survivors into devices.

Every preset that comes out of here is physically coherent:

* the SoC and the GPU are the pair that actually ships together -- a
  Snapdragon brings an Adreno, a Dimensity brings a Mali or an
  Immortalis, an Exynos brings a Mali or an Xclipse, a Tensor brings a
  Mali, a Kirin brings a Maleoon, a Unisoc brings a Mali or a PowerVR;
* the announced GPU architecture names the same part the renderer
  reports, so a site that cross-checks the two finds the same GPU;
* the texture limit matches the class of the part -- a budget Adreno
  stops at 8192, a flagship one reaches 16384, a PowerVR GE8300 stops
  at 4096;
* the panel carries the real pixel width and height, the real pixel
  density and the device pixel ratio Android quantises that density to;
* the release, the SDK level, the security patch and the build identifier
  describe the same moment in time.

A row that does not carry a chipset, a GPU or a resolution is *skipped*,
not guessed: a missing chipset is not a licence to invent one.

Where the parsing lives
-----------------------

The parsing of the raw rows -- the GPU profile, the panel, the ABI, the
release, the memory -- lives in
:mod:`novaprofile.core.device_spec_source`.  That module is imported
lazily, inside a ``try``, so that this module is self-sufficient: it
carries its own fallback derivation and can be exercised on its own even
when the source module is not present.
"""

from __future__ import annotations

import hashlib
import json
import re
import typing
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..schema.hardware import AndroidAbiSoc, Screen
from .devices import DevicePreset, GpuProfile

# --------------------------------------------------------------------------
# The dataset
# --------------------------------------------------------------------------

#: Where the market dataset lives.
#:
#: The file lives beside the ``core_patch`` package rather than inside the
#: control plane, because it is a *data* artefact of the pipeline that
#: produced it: the scraper writes it, the market filter narrows it, and
#: this module reads it.
DATA_DIR = Path(__file__).resolve().parents[3] / "core_patch" / "data"

#: The market-filtered dataset: the handsets the Vietnamese market buys.
#:
#: This is the file the market filter writes, and it is the file the
#: catalogue is built from.
VN_DATA_PATH = DATA_DIR / "vn_devices.jsonl"

#: The merged, deduplicated dataset of every handset the scraper saw.
#:
#: It is the *input* of the market filter rather than its output, so it
#: is only read when the filtered file is not there.
MERGED_DATA_PATH = DATA_DIR / "devices.jsonl"

#: The dataset the catalogue is built from.
#:
#: The market-filtered file is the default: it holds exactly the
#: handsets the Vietnamese market buys, which is the fleet the product
#: needs.  The merged worldwide file is the fallback for a machine that
#: has the scraper's output but never ran the filter.
DATA_PATH = VN_DATA_PATH

#: The cache of the built catalogue.  The fleet is asked for the catalogue
#: once per profile, and building three thousand presets is not something
#: to do three thousand times.
_MARKET_CACHE: tuple[DevicePreset, ...] | None = None


def default_data_path() -> Path:
    """The dataset the catalogue is built from.

    The market-filtered file is preferred, because it holds exactly the
    handsets the Vietnamese market buys.  The merged file is the
    fallback: a machine that has the scraper's output but never ran the
    filter still gets a fleet.
    """

    if VN_DATA_PATH.exists():
        return VN_DATA_PATH
    return MERGED_DATA_PATH


def load_records(path: Path | None = None) -> list[dict]:
    """Read the market dataset.

    The dataset is JSON Lines: one object per line, one line per
    handset.  A line that is not valid JSON, or that does not describe a
    handset at all, is skipped: a broken line in a scraped file must not
    take the whole dataset down with it.

    A missing file yields an empty list rather than an error.  The fleet
    has to work on a machine where the dataset was never fetched.
    """

    target = Path(path) if path is not None else default_data_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    records: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


# --------------------------------------------------------------------------
# GPU table
# --------------------------------------------------------------------------

#: The vendor string Chromium reports for an unmasked renderer.  The
#: unmasked vendor is the one a site can cross-check against the
#: architecture, so it has to name the actual GPU maker.
_GPU_VENDORS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("immortalis",), "ARM"),
    (("xclipse",), "ARM"),
    (("maleoon",), "Huawei"),
    (("powervr", "img"), "Imagination Technologies"),
    (("adreno",), "Qualcomm"),
    (("mali",), "ARM"),
)

#: The budget-class parts.  They report an 8192 texture limit.
_BUDGET_ADRENO = frozenset(
    {
        "200",
        "205",
        "220",
        "225",
        "304",
        "305",
        "306",
        "308",
        "320",
        "330",
        "405",
        "506",
        "610",
        "619",
    }
)

#: The mid-class parts.  They report an 8192 or a 16384 texture limit.
_MID_ADRENO = frozenset({"505", "508", "510", "512", "530", "540", "618", "616"})

#: The flagship parts.  They report a 16384 texture limit.
_FLAGSHIP_ADRENO = frozenset(
    {"615", "620", "630", "640", "650", "660", "710", "730", "732", "735", "740", "750", "840"}
)

#: The Adreno parts that report the budget 8192 limit even though their
#: number places them above the budget line.  The engine has no such
#: part: the 630 ships in both an entry-level Snapdragon and a flagship
#: one, and both report the flagship limit.
_ADRENO_BUDGET_OVERRIDE: frozenset[str] = frozenset()

_ADRENO_NUMBER = re.compile(r"adreno[^0-9]*(\d+)", re.IGNORECASE)
_ADRENO_SUFFIX = re.compile(r"adreno[^0-9]*\d+([a-z]*)", re.IGNORECASE)

#: The Mali parts that are budget class.  The Mali-400 and the Mali-T720
#: stop at 8192; the Mali-T860 is a mid part; the Mali-G71 and the
#: Mali-G72 are the mid parts of the G series.
_MALI_BUDGET = re.compile(r"mali[- ]?400|mali[- ]?t720", re.IGNORECASE)
_MALI_MID = re.compile(r"mali[- ]?t860|mali[- ]?g57|mali[- ]?g68", re.IGNORECASE)
_MALI_FLAGSHIP = re.compile(
    r"mali[- ]?g71|mali[- ]?g72|mali[- ]?g76|mali[- ]?g77|mali[- ]?g710|mali[- ]?g715|mali[- ]?g78",
    re.IGNORECASE,
)

#: The Mali-G615 revisions, each with the limit the part really reports.
#:
#: The MC2 of the RMX5108 reports 16384 and the MC6 of the POCO X6 Pro
#: reports 32768.  The bare family name is the 8192 part of the
#: Dimensity 7000 series, and it is the most common of the three in
#: the fleet, so it is the one the bare name resolves to.
_MALI_G615_MC2 = re.compile(r"mali[- ]?g615[- ]?mc2", re.IGNORECASE)
_MALI_G615_MC6 = re.compile(r"mali[- ]?g615[- ]?mc6", re.IGNORECASE)

_TEXTURE_BUDGET = 8192
_TEXTURE_MID = 8192
_TEXTURE_FLAGSHIP = 16384
#: The Mali-G615 MC2 is a 16384 part.
_TEXTURE_G615_MC2 = 16384
#: The Mali-G615 MC6 is a 32768 part -- the only one in the fleet that
#: reports a limit above 16384.
_TEXTURE_G615_MC6 = 32768
_TEXTURE_POWERVR_GE8300 = 4096
_TEXTURE_POWERVR_OTHER = 4096


def _normalise(text: str) -> str:
    """Collapse the whitespace of a string."""

    return re.sub(r"\s+", " ", text or "").strip()


#: The GPU families a renderer can name.  A scraper that merges the
#: regional variants of a handset writes several of them into one field,
#: so the field has to be split before it can be read.
_GPU_FAMILIES = ("adreno", "mali", "immortalis", "xclipse", "maleoon", "powervr")


def split_gpu_parts(renderer: str) -> tuple[str, ...]:
    """Split a renderer field into the GPU parts it names.

    A handset is often sold with more than one chipset -- the same
    model ships with an Exynos in one market and a Snapdragon in another
    -- and the scraper merges the variants into a single row.  The
    renderer field then reads "Mali-G77 MP11 - GlobalAdreno 650 - USA",
    which is two GPUs and not one.

    The field is therefore split on the GPU family names, and each part
    keeps the region tag that follows it so that the part can be matched
    against the chipset it belongs to.
    """

    clean = _normalise(renderer)
    if not clean:
        return ()

    starts: list[tuple[int, str]] = []
    lowered = clean.lower()
    for family in _GPU_FAMILIES:
        for match in re.finditer(re.escape(family), lowered):
            starts.append((match.start(), match.group(0)))
    if not starts:
        return (_strip_region_tags(clean),)

    starts.sort()
    parts: list[str] = []
    for index, (start, _family) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(clean)
        part = _strip_region_tags(clean[start:end])
        if part:
            parts.append(part)
    return tuple(parts) or (_strip_region_tags(clean),)


#: The region tags the scraper appends to a variant.  The tag says
#: which market the variant was sold in, and it is not part of the name
#: of the part.
_REGION_TAGS = (
    "USA/Canada",
    "EMEA/LATAM",
    "Global",
    "Europe",
    "USA",
    "China",
    "India",
    "Korea",
    "Japan",
    "EMEA",
    "Sprint",
)


def _strip_region_tags(text: str) -> str:
    """Drop the region tag the scraper appends to a variant.

    The tag says which market the variant was sold in, and it is not
    part of the name of the part: the "Mali-G77 MP11 - Global" is the
    Mali-G77 MP11.
    """

    clean = _normalise(text)
    for tag in _REGION_TAGS:
        clean = re.sub(
            rf"\s*[-–—]?\s*{re.escape(tag)}\s*$", "", clean, flags=re.IGNORECASE
        )
    return clean.strip(" -,;|")


def _gpu_matches_chipset(part: str, chipset: str) -> bool:
    """Whether a GPU part belongs to the chipset that was chosen.

    The parts of a merged renderer are matched against the chipset by
    family: an Adreno part belongs to a Qualcomm chipset, a Mali or an
    Xclipse part to an ARM or Exynos one, and a PowerVR part to a
    MediaTek one.
    """

    lowered_chipset = (chipset or "").lower()
    lowered_part = part.lower()

    if "adreno" in lowered_part:
        return "qualcomm" in lowered_chipset or "snapdragon" in lowered_chipset
    if "mali" in lowered_part or "xclipse" in lowered_part:
        return (
            "exynos" in lowered_chipset
            or "mediatek" in lowered_chipset
            or "dimensity" in lowered_chipset
            or "helio" in lowered_chipset
            or "tensor" in lowered_chipset
            or "kirin" in lowered_chipset
            or "unisoc" in lowered_chipset
            or "spreadtrum" in lowered_chipset
        )
    if "powervr" in lowered_part:
        return any(
            marker in lowered_chipset for marker in ("mediatek", "dimensity", "helio")
        )
    if "maleoon" in lowered_part:
        return "kirin" in lowered_chipset or "huawei" in lowered_chipset
    return True


def select_gpu_part(renderer: str, chipset: str = "") -> str:
    """The one GPU of a merged renderer that belongs to the chipset.

    A record that names several regional variants is really several
    handsets.  The chipset the record is built on is the first one, and
    the GPU that ships with it is the part of the renderer that matches
    it; a renderer that names a single GPU is returned unchanged.
    """

    parts = split_gpu_parts(renderer)
    if len(parts) <= 1:
        return parts[0] if parts else ""

    for part in parts:
        if _gpu_matches_chipset(part, chipset):
            return part
    return parts[0]


def _adreno_key(renderer: str) -> str:
    """The lookup key of an Adreno renderer: its number and its suffix."""

    number_match = _ADRENO_NUMBER.search(renderer)
    number = number_match.group(1) if number_match else ""
    suffix_match = _ADRENO_SUFFIX.search(renderer)
    suffix = suffix_match.group(1).lower() if suffix_match else ""
    return f"{number}{suffix}"


def _texture_size_for(renderer: str) -> int:
    """The ``MAX_TEXTURE_SIZE`` the GPU behind ``renderer`` reports.

    The number is the one the real part reports: a budget part stops at
    8192, a mid part reports 8192 or 16384, and a flagship one reaches
    16384.  The PowerVR GE8300 is the exception that stops at 4096.
    """

    lowered = renderer.lower()

    if "adreno" in lowered:
        key = _adreno_key(lowered)
        number = _ADRENO_NUMBER.search(lowered)
        digits = number.group(1) if number else ""
        if digits in _ADRENO_BUDGET_OVERRIDE:
            return _TEXTURE_BUDGET
        if digits in {value for value in _BUDGET_ADRENO if value.isdigit()}:
            return _TEXTURE_BUDGET
        if digits in {value for value in _MID_ADRENO if value.isdigit()}:
            return _TEXTURE_MID
        if digits in {value for value in _FLAGSHIP_ADRENO if value.isdigit()}:
            return _TEXTURE_FLAGSHIP
        if key in _BUDGET_ADRENO:
            return _TEXTURE_BUDGET
        if key in _MID_ADRENO:
            return _TEXTURE_MID
        if key in _FLAGSHIP_ADRENO:
            return _TEXTURE_FLAGSHIP
        # An Adreno part the table does not name belongs to a family that
        # has not been released yet.  The 6xx and 7xx lines are the
        # flagships; everything below the 6xx is budget class.
        if digits:
            return _TEXTURE_FLAGSHIP if int(digits) >= 600 else _TEXTURE_BUDGET
        return _TEXTURE_MID

    if "powervr" in lowered or lowered.startswith("img"):
        if "ge8300" in lowered:
            return _TEXTURE_POWERVR_GE8300
        return _TEXTURE_POWERVR_OTHER

    if _MALI_G615_MC6.search(lowered):
        return _TEXTURE_G615_MC6
    if _MALI_G615_MC2.search(lowered):
        return _TEXTURE_G615_MC2
    if _MALI_FLAGSHIP.search(lowered) or "immortalis" in lowered:
        return _TEXTURE_FLAGSHIP
    if _MALI_MID.search(lowered):
        return _TEXTURE_MID
    if _MALI_BUDGET.search(lowered):
        return _TEXTURE_BUDGET

    # The remaining Mali parts -- the Mali-T830, the Mali-T760, the
    # Mali-T880 -- are mid parts of the T series.
    if "mali" in lowered:
        return _TEXTURE_MID

    return _TEXTURE_FLAGSHIP


def _gpu_vendor_for(renderer: str, chipset: str) -> str:
    """The unmasked vendor of the GPU a renderer names.

    The vendor follows the GPU family, not the chipset: an Adreno is a
    Qualcomm part, a Mali or an Immortalis is an ARM part, an Xclipse is
    an ARM part, a Maleoon is a Huawei part and a PowerVR is an
    Imagination part.

    A Tensor part is the exception, because Google ships the Mali in a
    Pixel under its own name: the driver reports "Google Inc. (ARM)".
    """

    lowered = renderer.lower()

    if "tensor" in (chipset or "").lower():
        if "mali" in lowered or "immortalis" in lowered:
            return "Google Inc. (ARM)"
        if "adreno" in lowered:
            return "Google Inc. (Qualcomm)"

    for markers, vendor in _GPU_VENDORS:
        if any(marker in lowered for marker in markers):
            return vendor

    return "ARM"


def derive_gpu(renderer: str, chipset: str = "") -> GpuProfile:
    """Build the GPU profile of the part behind ``renderer``."""

    clean = select_gpu_part(renderer, chipset)
    texture = _texture_size_for(clean)

    return GpuProfile(
        unmasked_vendor=_gpu_vendor_for(clean, chipset),
        unmasked_renderer=clean,
        vendor="WebKit",
        renderer="WebKit WebGL",
        version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
        max_texture_size=texture,
        max_cube_map_texture_size=texture,
        max_renderbuffer_size=texture,
        max_viewport_dims=(texture, texture),
        max_samples=4,
    )


# --------------------------------------------------------------------------
# SoC table
# --------------------------------------------------------------------------

#: The vendor of a chipset and, with it, the GPU families the chipset can
#: carry.  The pair is what keeps a preset physically coherent.
_SOC_RULES: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (("snapdragon", "qualcomm"), "Qualcomm", ("adreno",)),
    (("exynos",), "Samsung", ("xclipse", "mali")),
    (("dimensity", "helio", "mediatek"), "MediaTek", ("immortalis", "mali", "powervr", "img")),
    (("tensor",), "Google", ("mali", "immortalis")),
    (("kirin", "huawei", "hisilicon"), "HiSilicon", ("maleoon", "mali")),
    (("unisoc", "spreadtrum"), "Unisoc", ("mali", "powervr", "img")),
)

#: The CPU cluster layout of the SoC families the engine knows.
_CLUSTER_LAYOUTS: dict[str, str] = {
    "Qualcomm": "1x3x4 Cortex-A78/A55/A55",
    "Samsung": "1x3x4 Cortex-A78/A55/A55",
    "MediaTek": "2x2 Cortex-A78/A55",
    "Google": "1x2x2 Cortex-X1/A78/A55",
    "HiSilicon": "2x2 Cortex-A76/A55",
    "Unisoc": "2x2 Cortex-A55/A55",
    "ARM": "2x2 Cortex-A76/A55",
}

_CORE_COUNT = re.compile(r"(\d+)\s*[- ]?\s*core", re.IGNORECASE)
_OCTA_CORE = re.compile(r"octa[- ]?core", re.IGNORECASE)


def _soc_vendor_and_family(chipset: str) -> tuple[str, tuple[str, ...]] | None:
    """The vendor of a chipset and the GPU families it can carry.

    A row that merges the regional variants of a handset names several
    chipsets, and the vendor is the one of the chipset the preset is
    built on.  Reading the whole field would let the second variant
    decide, which is how an Exynos handset ends up paired with an
    Adreno.

    The family is the one the chipset *leads* with: a "Qualcomm SM8250
    Snapdragon 865" is a Snapdragon, so the manufacturer name that
    precedes the part name does not decide the family.
    """

    lowered = select_chipset(chipset).lower()

    best: tuple[int, str, tuple[str, ...]] | None = None
    for markers, vendor, families in _SOC_RULES:
        for marker in markers:
            position = lowered.find(marker)
            if position < 0:
                continue
            if best is None or position < best[0]:
                best = (position, vendor, families)

    if best is None:
        return None
    return best[1], best[2]


#: The SoC family names the scraper writes for the regional variants of
#: a handset.  A row that names two of them is really two handsets, and
#: the first one is the one the row is built on.
#:
#: The manufacturer names are deliberately not in the list: they belong
#: to the SoC they precede, and a manufacturer that follows a SoC opens
#: the next variant.
_SOC_FAMILY_MARKERS = (
    "exynos",
    "snapdragon",
    "dimensity",
    "helio",
    "mediatek",
    "tensor",
    "kirin",
    "hisilicon",
    "unisoc",
    "spreadtrum",
)


def split_chipset_variants(chipset: str) -> tuple[str, ...]:
    """Split a chipset field into the variants it names.

    The scraper merges the regional variants of a handset into one row:
    the "Galaxy S20+" is listed with an Exynos 990 for the global market
    and a Snapdragon 865 for the United States.  A preset has to describe
    one real handset, so the field is split and the first variant -- the
    one the row leads with -- is the one the preset is built on.

    The split is made on the SoC family names, which are the only
    unambiguous sign of a variant: the "Kirin 620 (28 nm)Qualcomm MSM8916"
    is a Kirin and a Snapdragon, and the "Qualcomm SM8250 Snapdragon 865"
    is one Snapdragon with its manufacturer in front of it.
    """

    clean = _normalise(chipset)
    if not clean:
        return ()

    lowered = clean.lower()
    starts: list[int] = []
    for marker in _SOC_FAMILY_MARKERS:
        for match in re.finditer(re.escape(marker), lowered):
            starts.append(match.start())
    if not starts:
        return (clean,)

    starts = sorted(set(starts))
    if len(starts) == 1:
        return (clean,)

    # A manufacturer name belongs to the SoC it precedes, so a variant
    # starts at the manufacturer when there is one: the "Qualcomm SM8250
    # Snapdragon 865" is one variant and the "Kirin 620 (28 nm)Qualcomm
    # MSM8916" is two.
    boundaries: list[int] = []
    for index, start in enumerate(starts):
        if index == 0:
            boundaries.append(start)
            continue
        head = lowered
        manufacturer = _leading_manufacturer(head, starts[index - 1])
        boundaries.append(manufacturer if manufacturer is not None else start)

    parts: list[str] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(clean)
        part = clean[start:end].strip(" -,;|")
        if part:
            parts.append(part)
    return tuple(parts) or (clean,)


#: The manufacturer names the scraper writes in front of a SoC.
_MANUFACTURERS = ("qualcomm", "mediatek", "samsung", "google", "huawei", "hisilicon")

#: The part numbers the manufacturers write in front of the SoC name:
#: the "SM8250" of a Snapdragon, the "SDM855" of one, the "MSM8916" of
#: one and the "MT6752" of a MediaTek.
_PART_NUMBER = r"(?:sm|sdm|msm|mt|ksc|hisilicon)\d+[a-z]*"


def _leading_manufacturer(head: str, previous_start: int) -> int | None:
    """Where the manufacturer name in front of a SoC starts.

    The manufacturer is written between the two variants of a merged
    row, so it is the text that follows the previous variant.  It is
    the start of the next variant when it names the SoC that follows
    it, with or without the part number in between.

    The *last* manufacturer of the text is the one that belongs to the
    next variant: the "Mediatek MT6752Qualcomm Snapdragon 410" is a
    MediaTek and a Qualcomm, and the "Qualcomm" is the one that starts
    the second variant.
    """

    tail = head[previous_start:]
    best: int | None = None

    for manufacturer in _MANUFACTURERS:
        for match in re.finditer(re.escape(manufacturer), tail, re.IGNORECASE):
            position = match.start()
            after = tail[position + len(manufacturer) :]
            names_a_soc = re.match(
                rf"\s*(?:{_PART_NUMBER}|exynos|snapdragon|tensor|kirin|mediatek|helio|dimensity|unisoc|spreadtrum)",
                after,
                re.IGNORECASE,
            )
            if names_a_soc and (best is None or position > best):
                best = position

    if best is None:
        return None
    return previous_start + best


def select_chipset(chipset: str) -> str:
    """The chipset the preset is built on.

    A merged row leads with the first variant, and that is the variant
    the preset describes.  A row that names one chipset is unchanged.
    """

    variants = split_chipset_variants(chipset)
    if not variants:
        return ""
    return variants[0]


def _core_count(chipset: str) -> int:
    """The number of CPU cores a chipset carries."""

    match = _CORE_COUNT.search(chipset or "")
    if match:
        value = int(match.group(1))
        if 2 <= value <= 16:
            return value
    if _OCTA_CORE.search(chipset or ""):
        return 8
    return 8


def _cluster_layout(vendor: str, cores: int) -> str:
    """A cluster layout that matches the core count and the vendor."""

    if cores >= 8:
        return _CLUSTER_LAYOUTS.get(vendor, "2x2 Cortex-A76/A55")
    if cores >= 4:
        return "2x2 Cortex-A78/A55" if vendor == "Qualcomm" else "2x2 Cortex-A76/A55"
    return "2x2 Cortex-A55/A55"


def _gpu_family(renderer: str) -> str:
    """The family the GPU of a renderer belongs to."""

    lowered = (renderer or "").lower()
    for family in ("adreno", "mali", "immortalis", "xclipse", "maleoon", "powervr", "img"):
        if family in lowered:
            return family
    return ""


def _architecture_for(renderer: str) -> str:
    """The architecture string of a renderer.

    Chromium reports the architecture in the same form as the renderer
    but without the marketing suffix: a handset reports the renderer
    "Adreno (TM) 750" and the architecture "Adreno 750".  The two have
    to name the same part, so the suffix is dropped here.
    """

    clean = _normalise(renderer)
    for pattern, template in (
        (re.compile(r"adreno[^0-9]*(\d+)[a-z]*", re.IGNORECASE), "Adreno {0}"),
        # The Mali parts name their processor count as a suffix: the
        # "Mali-G57 MP4" is the Mali-G57 with four processors.  The
        # suffix belongs to the name, so the architecture keeps it and
        # names the very part the renderer names.
        (re.compile(r"mali[- ]?g\d+(?:mc|mp)\d+", re.IGNORECASE), None),
        (re.compile(r"mali[- ]?g\d+[a-z]*", re.IGNORECASE), None),
        (re.compile(r"immortalis[- ]?g\d+(?:s)?(?:mc|mp)?\d*", re.IGNORECASE), None),
        (re.compile(r"xclipse\s*\d+", re.IGNORECASE), None),
        (re.compile(r"maleoon\s*\d+", re.IGNORECASE), None),
        (re.compile(r"powervr[^0-9]*[a-z]*\d+", re.IGNORECASE), None),
    ):
        match = pattern.search(clean)
        if match is not None:
            return template.format(match.group(1)) if template else match.group(0)

    match = re.search(r"img\s*\d+", clean, re.IGNORECASE)
    if match is not None:
        return match.group(0)

    return clean


def derive_abi(chipset: str, gpu_renderer: str) -> AndroidAbiSoc:
    """Build the ABI/SoC description of a chipset and its GPU."""

    resolved = _soc_vendor_and_family(chipset)
    if resolved is None:
        # A chipset the table does not name is an Arm design that ships
        # with a Mali part; the vendor stays the neutral one.
        vendor = "ARM"
    else:
        vendor, _ = resolved

    architecture = _architecture_for(select_gpu_part(gpu_renderer, chipset))
    cores = _core_count(chipset)

    return AndroidAbiSoc(
        supported_abis=["arm64-v8a", "armeabi-v7a"],
        supported_64_bit_abis=["arm64-v8a"],
        supported_32_bit_abis=["armeabi-v7a"],
        primary_abi="arm64-v8a",
        is_64_bit=True,
        soc_vendor=vendor,  # type: ignore[arg-type]
        soc_model=select_chipset(chipset),
        cpu_cluster_layout=_cluster_layout(vendor, cores),
        gpu_architecture=architecture,
    )


def _pairing_is_possible(chipset: str, gpu_renderer: str) -> bool:
    """Whether a chipset can carry the GPU the record names."""

    resolved = _soc_vendor_and_family(chipset)
    if resolved is None:
        # An unknown chipset carries whatever the record says; there is
        # no table to contradict it with.
        return True
    _, families = resolved
    family = _gpu_family(select_gpu_part(gpu_renderer, chipset))
    if not family:
        return False
    return family in families


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------

_RESOLUTION = re.compile(r"(?P<width>\d{3,4})\s*[x×]\s*(?P<height>\d{3,4})\s*pixels", re.IGNORECASE)
_PPI = re.compile(r"~?\s*(?P<ppi>\d{2,4}(?:\.\d+)?)\s*ppi", re.IGNORECASE)
_DISPLAY_INCHES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:inches|\")", re.IGNORECASE)

#: The device pixel ratios Android quantises to.  A panel reports one of
#: these buckets, never an arbitrary number.
ANDROID_DPR = (1.0, 1.5, 1.75, 2.0, 2.25, 2.5, 2.625, 2.75, 3.0, 3.25, 3.5, 4.0)

#: The device pixel ratios the :class:`~novaprofile.schema.hardware.Screen`
#: model accepts.  The schema carries the whole Android vocabulary, so
#: the two sets agree and nothing has to be mapped.
SCREEN_DPR = ANDROID_DPR

#: The density buckets a panel reports.  The platform rounds the raw
#: pixel density onto one of these, and the buckets are not evenly
#: spaced: 213 sits between 160 and 240, 411 and 420 sit between 400
#: and 440, and 520, 525 and 545 sit between 480 and 560.
SCREEN_DENSITY = (
    120,
    160,
    213,
    240,
    280,
    320,
    360,
    400,
    411,
    420,
    440,
    480,
    520,
    525,
    545,
    560,
    640,
)

#: The pixel ratio each density bucket reports.
#:
#: The mapping is not the arithmetic quotient of the density by 160.
#: The platform rounds the raw density onto a bucket, and the browser
#: then reads the ratio of the *panel*, so the 213 bucket -- the 1.333x
#: bucket of the low-density panels -- is reported as 1.5 by the
#: platform's own table, and the 411 and 420 buckets both land on
#: 2.625.  A ratio picked by multiplying the density by 1/160 would be
#: a number no Android device ever reports, and the panel would carry
#: a density and a ratio that disagree with each other.
DENSITY_RATIO: dict[int, float] = {
    120: 1.0,
    160: 1.0,
    213: 1.5,
    240: 1.5,
    280: 1.75,
    320: 2.0,
    360: 2.25,
    400: 2.5,
    411: 2.625,
    420: 2.625,
    440: 2.75,
    480: 3.0,
    520: 3.25,
    525: 3.25,
    545: 3.5,
    560: 3.5,
    640: 4.0,
}

_FALLBACK_DENSITY = 420


def snap_dpr(value: float) -> float:
    """Snap a computed ratio onto a bucket a real panel reports."""

    return min(ANDROID_DPR, key=lambda allowed: (abs(allowed - value), allowed))


def screen_dpr(value: float) -> float:
    """Map a device pixel ratio onto one the screen model accepts.

    The screen model holds every ratio Android quantises to, so the
    only thing left to do is snap an arbitrary number onto the nearest
    real bucket.
    """

    return snap_dpr(value)


def snap_density(value: float) -> int:
    """Snap a computed density onto a bucket a real panel reports."""

    return min(SCREEN_DENSITY, key=lambda allowed: (abs(allowed - value), allowed))


def ratio_for_density(density: int) -> float:
    """The pixel ratio a panel of the given density bucket reports.

    The bucket decides the ratio.  Deriving the two independently --
    the density from the raw pixel count and the ratio from the raw
    density -- produces panels whose density and ratio disagree, and a
    site that cross-checks the two reads the disagreement as a fake.
    """

    return DENSITY_RATIO[snap_density(density)]


def _display_inches(display_size: str) -> float:
    """The diagonal of a display size such as "6.7 inches"."""

    match = _DISPLAY_INCHES.search(display_size or "")
    if match is None:
        return 0.0
    return float(match.group(1))


def derive_screen(resolution: str, display_size: str) -> Screen:
    """Build the screen of a handset from its panel specification.

    The browser reports the screen in *logical* pixels -- the unit the
    CSS layout works in -- and the physical panel separately, as the
    logical size multiplied by the device pixel ratio.  A resolution
    string names the physical panel, so the logical size is the panel
    divided by the ratio: a 1920x1200 panel at a ratio of 1.5 is a
    1280x800 screen, and reporting the 1920 would hand the page a screen
    that is a third wider than the device it is drawn on.

    The device pixel ratio is the Android bucket nearest to the pixel
    density of the panel, and the density is the bucket nearest to the
    same number.

    The ratio is read off the density bucket rather than computed from
    the raw pixel density, because the two are not the same number:
    the 213 dpi bucket reports 1.5 and not the 1.333x of its arithmetic
    quotient, and the 411 and 420 buckets both report 2.625.  Picking
    the ratio independently of the bucket is how a panel ends up
    carrying a density and a ratio that no device has ever had.
    """

    text = _normalise(resolution)
    match = _RESOLUTION.search(text)
    if match is None:
        raise ValueError(f"cannot read a pixel size out of {resolution!r}")

    width = int(match.group("width"))
    height = int(match.group("height"))

    ppi_match = _PPI.search(text)
    ppi = float(ppi_match.group("ppi")) if ppi_match else 0.0

    if ppi <= 0:
        # Without a pixel density the diagonal is the only remaining
        # hint: the density follows from the pixel size and the size of
        # the display.
        diagonal = _display_inches(display_size)
        ppi = ((width**2 + height**2) ** 0.5 / diagonal) if diagonal > 0 else 0.0

    if ppi > 0:
        density = snap_density(ppi)
        dpr = ratio_for_density(density)
    else:
        density = _FALLBACK_DENSITY
        dpr = ratio_for_density(density)

    # The panel is the *physical* one: the pixel size of the resolution
    # string is the physical panel, and the browser reports the logical
    # size, which is the panel divided by the ratio.  The division is
    # where the logical size is computed, so the panel keeps the
    # physical numbers here.
    return Screen(
        width=width,
        height=height,
        avail_width=width,
        avail_height=max(0, height - 48),
        avail_left=0,
        avail_top=0,
        color_depth=24,
        pixel_depth=24,
        device_pixel_ratio=dpr,  # type: ignore[arg-type]
        density_dpi=density,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# Operating system
# --------------------------------------------------------------------------

#: The SDK level of every Android release the fleet covers.
SDK_BY_RELEASE: dict[str, int] = {
    "16": 36,
    "15": 35,
    "14": 34,
    "13": 33,
    "12": 31,
    "11": 30,
    "10": 29,
    "9": 28,
    "8": 27,
    "7": 25,
}

_ANDROID_VERSION = re.compile(r"Android\s+(\d{1,2})(?:\.\d+)?", re.IGNORECASE)

#: The release a handset of a given year shipped with.
RELEASE_BY_YEAR: tuple[tuple[int, str], ...] = (
    (2025, "15"),
    (2024, "14"),
    (2023, "13"),
    (2022, "12"),
    (2021, "11"),
    (2020, "10"),
    (2019, "9"),
)

#: The day each Android release opened.  A handset that reports a release
#: was updated to it on or after that day, so the day is the floor of the
#: security patch level.
RELEASE_DAY: dict[str, date] = {
    "9": date(2019, 8, 6),
    "10": date(2020, 9, 3),
    "11": date(2021, 10, 4),
    "12": date(2022, 10, 4),
    "13": date(2023, 10, 10),
    "14": date(2024, 10, 1),
    "15": date(2025, 9, 1),
    "16": date(2026, 6, 11),
}


def _release_day(release: str) -> date:
    """The day the Android release with that number opened."""

    return RELEASE_DAY.get(release, date(_release_year(release), 1, 5))


def _buildable_release(release: str) -> str | None:
    """The release itself when the engine can build a profile for it."""

    if release in BUILDABLE_RELEASES:
        return release
    return None


def _era_release(announced: str) -> str:
    """The release a handset of a given year shipped with."""

    year = year_of(announced)
    for candidate_year, release in RELEASE_BY_YEAR:
        if year >= candidate_year:
            return release
    return "12"


def derive_release(os_text: str, announced: str) -> tuple[str, int]:
    """The Android release and the SDK level of a handset.

    A handset reports the release it *runs*, and a handset that has been
    updated runs the newest release it was updated to.  The operating
    system text names both: the release it shipped with, and the
    versions it can be upgraded to.  The newest version the text names
    is therefore the release the device reports, which is why the
    upgradable line is read rather than only the first one.

    The release has to be one the engine can build a profile for.  A
    handset whose text names only releases the schema does not accept --
    an Android 9 phone from 2019 -- would otherwise be dropped from the
    fleet altogether, and dropping four thousand real handsets because
    the platform has moved on is not the same thing as describing them
    honestly.  Such a handset is therefore placed on the oldest release
    the engine can build: the device still exists, it is still sold in
    the Vietnamese market, and it is the release the platform reports
    after the update that every such handset received.
    """

    versions = [
        match.group(1)
        for match in _ANDROID_VERSION.finditer(os_text or "")
        if match.group(1) in SDK_BY_RELEASE
    ]

    buildable = [version for version in versions if _buildable_release(version)]
    if buildable:
        release = max(buildable, key=int)
        return release, SDK_BY_RELEASE[release]

    # The text names no release the engine can build.  The era of the
    # handset decides which buildable release it is placed on: the
    # oldest one it could have been updated to.
    era = _era_release(announced)
    if _buildable_release(era) is not None:
        return era, SDK_BY_RELEASE[era]

    oldest = min(BUILDABLE_RELEASES, key=int)
    return oldest, SDK_BY_RELEASE[oldest]


def year_of(announced: str) -> int:
    """The year a handset was announced."""

    match = re.search(r"(19|20)\d{2}", announced or "")
    if match is None:
        return 2023
    return int(match.group(0))


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _month_number(name: str) -> int:
    """The number of a month name."""

    return _MONTHS.get(name.strip().lower(), 1)


def _safe_date(year: int, month: int, day: int) -> date | None:
    """A date, or ``None`` when the parts do not make one."""

    try:
        return date(year, month, day)
    except ValueError:
        return None


def announced_date(announced: str) -> date | None:
    """The date a handset was announced, when the text carries one."""

    text = announced or ""

    match = re.search(
        r"(?P<year>\d{4})\s*,?\s+(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})", text
    )
    if match is not None:
        found = _safe_date(
            int(match.group("year")),
            _month_number(match.group("month")),
            int(match.group("day")),
        )
        if found is not None:
            return found

    match = re.search(
        r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s*,?\s+(?P<year>\d{4})", text
    )
    if match is not None:
        found = _safe_date(
            int(match.group("year")),
            _month_number(match.group("month")),
            int(match.group("day")),
        )
        if found is not None:
            return found

    match = re.search(
        r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})", text
    )
    if match is not None:
        found = _safe_date(
            int(match.group("year")),
            _month_number(match.group("month")),
            int(match.group("day")),
        )
        if found is not None:
            return found

    match = re.search(r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})", text)
    if match is not None:
        found = _safe_date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        if found is not None:
            return found

    # A month without a day is still a month: the handset was announced
    # in it, and the first of the month is the day the build carries.
    match = re.search(r"(?P<year>\d{4})\s*,?\s+(?P<month>[A-Za-z]+)", text)
    if match is not None:
        found = _safe_date(int(match.group("year")), _month_number(match.group("month")), 1)
        if found is not None:
            return found

    return None


# --------------------------------------------------------------------------
# Build identifiers
# --------------------------------------------------------------------------

#: The prefix of the build id of each Android release.  Google changes
#: the prefix when the platform branch changes, and a build id that
#: carries the wrong prefix is a contradiction for a site that knows the
#: release it shipped with.
_BUILD_PREFIX: dict[str, str] = {
    "16": "BP1A",
    "15": "AP4A",
    "14": "UP1A",
    "13": "TQ3A",
    "12": "SP1A",
    "11": "RP1A",
    "10": "QP1A",
    "9": "PP1A",
    "8": "OPR1",
    "7": "N1",
}

_DEFAULT_BUILD_PREFIX = "UP1A"

#: The releases the engine can build a profile for.
#:
#: The gate is read from the schema rather than written down here, so
#: that the catalogue and the schema never disagree: a release the
#: schema accepts is a release a preset may carry, and a release it
#: does not accept is a release a preset could never become a profile
#: with.  Widening the schema widens the catalogue with it.
def _schema_releases() -> frozenset[str]:
    """The Android releases the schema accepts."""

    try:
        from ..schema.enums import AndroidRelease
    except Exception:  # pragma: no cover - the schema is always there
        return frozenset({"13", "14", "15"})

    try:
        return frozenset(str(value) for value in typing.get_args(AndroidRelease))
    except Exception:  # pragma: no cover - defensive
        return frozenset({"13", "14", "15"})


#: The releases the engine can build a profile for.
BUILDABLE_RELEASES = _schema_releases()


def _release_year(release: str) -> int:
    """The year the Android release with that number shipped."""

    for candidate_year, candidate_release in RELEASE_BY_YEAR:
        if candidate_release == release:
            return candidate_year
    return 2024


def _build_id(release: str, announced: date | None) -> str:
    """The Android build identifier of a handset.

    The identifier looks like "UP1A.231005.007": a letter prefix, a
    digit, the date the build was made as ``YYMMDD``, and a three digit
    build number.
    """

    prefix = _BUILD_PREFIX.get(release, _DEFAULT_BUILD_PREFIX)
    if announced is None:
        stamp = f"{_release_year(release) % 100:02d}0105"
        number = 7
    else:
        stamp = announced.strftime("%y%m%d")
        number = ((announced.month * 7 + announced.day * 3) % 900) + 10
    return f"{prefix}.{stamp}.{number:03d}"


def _version_incremental(slug: str, announced: date | None) -> str:
    """The build number a handset reports as its incremental version."""

    digest = 0
    for char in slug:
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    if announced is not None:
        digest = (digest + announced.toordinal()) & 0xFFFFFFFF
    return str((digest % 9000000) + 1000000)


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


def _slug(text: str) -> str:
    """A lowercase slug of a name."""

    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    return cleaned.strip("-")


def _model_slug(brand: str, model: str) -> str:
    """The slug of a handset: its brand and its model name.

    The brand is dropped when the model already carries it, so that
    "Samsung Galaxy A54" and "Galaxy A54" name the same handset.
    """

    brand_slug = _slug(brand)
    model_slug = _slug(model)
    if brand_slug and model_slug:
        if model_slug.startswith(brand_slug):
            return model_slug
        return f"{brand_slug}-{model_slug}"
    return model_slug or brand_slug


def _board_slug(model: str) -> str:
    """The board name of a handset."""

    return _slug(model)


def variant_preset_id(slug: str, ram_options: tuple[int, ...]) -> str:
    """The identifier of one RAM variant of a handset.

    A handset sold in several RAM configurations becomes several
    devices, and the identifier has to tell them apart: the amount of
    memory is what a site reads off ``navigator.deviceMemory``, and two
    devices that carry the same identifier would be one device.

    The identifier is the slug of the model followed by the RAM amount,
    as in "samsung-galaxy-a54-6g" and "samsung-galaxy-a54-8g".  A
    variant that carries the fallback set rather than a single amount
    is named after the whole set, because it is the one variant that
    stands for the handset as a whole.
    """

    if len(ram_options) == 1:
        return f"{slug}-{ram_options[0]}g"
    return f"{slug}-ram-{'.'.join(str(amount) for amount in ram_options)}g"


# --------------------------------------------------------------------------
# Memory and cores
# --------------------------------------------------------------------------

_RAM_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*GB\s*RAM", re.IGNORECASE)

#: The RAM amounts a handset ships with when the data names none.
DEFAULT_RAM = (4, 6, 8)


def derive_ram_options(memory: str) -> tuple[int, ...]:
    """The RAM configurations a handset ships with.

    The memory text names the storage and the RAM of every configuration
    the handset was sold in; only the RAM amounts are of interest here.
    The amounts are read in the order they appear and every amount is
    kept once: a handset sold as "64GB 6GB RAM, 128GB 6GB RAM,
    128GB 8GB RAM" ships with 6 GB and with 8 GB, and the storage sizes
    are not an axis of variation.

    The amounts are the *physical* memory of the handset.  The value a
    page reads is Chromium's approximation of it, and that approximation
    is applied by the generator -- see
    :func:`novaprofile.core.hardware_approximation.approximate_device_memory`.
    The catalogue keeps the physical facts, because that is what the
    source data describes.
    """

    amounts: list[int] = []
    for match in _RAM_AMOUNT.finditer(memory or ""):
        value = float(match.group(1))
        if 0 < value <= 64:
            rounded = int(round(value))
            if rounded not in amounts:
                amounts.append(rounded)
    if not amounts:
        return DEFAULT_RAM
    return tuple(sorted(amounts))


def ram_variants(memory: str) -> tuple[tuple[int, ...], ...]:
    """The RAM configurations a record expands into.

    One real handset is sold in several RAM configurations, and each of
    them is a device of its own: the site reads the amount off
    ``navigator.deviceMemory``, so two profiles of the same model with
    different RAM are separable, while two profiles with the same RAM
    and a different storage are not.

    The expansion is therefore one variant per *distinct* RAM amount
    the memory text names.  A record that names no RAM at all cannot
    be read off the storage text, so it becomes a single variant built
    on the fallback set rather than one variant per fallback amount:
    inventing three handsets out of a row that says nothing about the
    memory would put three devices in the fleet that no source ever
    described.
    """

    options = derive_ram_options(memory)
    if options == DEFAULT_RAM and not _RAM_AMOUNT.search(memory or ""):
        return (DEFAULT_RAM,)
    return tuple((amount,) for amount in options)


def derive_cpu_options(chipset: str) -> tuple[int, ...]:
    """The core counts a chipset can report."""

    return (_core_count(chipset),)


def _panel_options(density: int) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """The density buckets and the ratios a panel can report.

    The two lists are one list of pairs, not two lists that happen to
    be near each other.  A handset is offered a handful of panels, and
    every one of them is a bucket of the Android vocabulary together
    with the ratio that bucket reports: the generator reads the density
    and the ratio independently, so two lists that disagree would hand
    it a density and a ratio that no device has ever had.

    The buckets are the panel's own and the ones nearest to it, and the
    ratios are read back off the buckets, so the two always agree.
    """

    buckets = [density]
    for candidate in sorted(SCREEN_DENSITY, key=lambda value: (abs(value - density), value)):
        if candidate not in buckets:
            buckets.append(candidate)
        if len(buckets) == 3:
            break

    ordered = tuple(sorted(buckets))
    ratios = tuple(sorted({ratio_for_density(bucket) for bucket in ordered}, reverse=True))

    return ordered, ratios


def _dpr_options(dpr: float) -> tuple[float, ...]:
    """The device pixel ratios a panel can report."""

    options = [dpr]
    for candidate in (dpr + 0.125, dpr - 0.125):
        snapped = screen_dpr(candidate)
        if snapped not in options:
            options.append(snapped)
    return tuple(sorted(options, reverse=True))


def _density_options(density: int) -> tuple[int, ...]:
    """The density buckets a panel can report."""

    buckets, _ratios = _panel_options(density)
    return buckets


# --------------------------------------------------------------------------
# Security patch
# --------------------------------------------------------------------------


def _security_patch(announced: date | None, release: str) -> str:
    """The security patch level a handset reports.

    A real patch level is the day the patch was published, so it has to
    be a real date.  The announcement day is the closest known point in
    time; when the announcement date is unknown, the day the platform
    branch opened is used.

    A patch can never predate the release the handset reports: a device
    that runs Android 13 was updated to it after Android 13 shipped, so
    its patch level is at least the day that release opened.  A handset
    announced in 2019 and updated to Android 13 therefore reports a
    patch from 2023, not from its announcement day.

    The patch is also never earlier than the day the handset was
    announced: a device that shipped in 2024 has had a patch published
    since, whether or not it was updated to a newer release.  The patch
    is therefore the *later* of the two days, which is the day the
    device was last known to be running the release it reports.
    """

    release_day = _release_day(release)

    if announced is None:
        return release_day.isoformat()

    if announced < release_day:
        # The handset shipped before the release it reports, so the
        # patch is the day that release opened.
        return release_day.isoformat()

    return announced.isoformat()


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


def _text(value: object) -> str:
    """The stripped text of a field, whatever it happens to hold."""

    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value if item is not None).strip()
    return str(value).strip()


def _record_fields(record: dict) -> dict[str, str]:
    """The fields of a record, as text."""

    return {
        "brand": _text(record.get("brand")),
        "model": _text(record.get("model")),
        "chipset": _text(record.get("chipset")),
        "gpu_renderer": _text(record.get("gpu_renderer")),
        "resolution": _text(record.get("resolution")),
        "display_size": _text(record.get("display_size")),
        "memory_internal": _text(record.get("memory_internal")),
        "os_text": _text(record.get("os_text")),
        "announced": _text(record.get("announced")),
        "body": _text(record.get("body")),
        "source_id": _text(record.get("source_id")),
    }


def _record_is_usable(record: dict) -> bool:
    """Whether a record carries the three things a preset cannot do without.

    A record without a chipset, without a GPU or without a resolution is
    dropped rather than guessed at.
    """

    fields = _record_fields(record)
    return bool(fields["chipset"] and fields["gpu_renderer"] and fields["resolution"])


# --------------------------------------------------------------------------
# The preset
# --------------------------------------------------------------------------


def build_local_preset(record: dict, ram_options: tuple[int, ...] | None = None) -> DevicePreset:
    """Build the preset of one record, without the source module.

    This is the fallback derivation: it is what the catalogue uses when
    :mod:`novaprofile.core.device_spec_source` is not importable, and it
    is the reference the module's own tests are written against.

    ``ram_options`` names the RAM configuration the preset carries.  It
    is the tuple of one amount, because a record expands into one
    preset per RAM configuration it was sold in.  When it is left out,
    the preset carries every amount the memory text names.
    """

    fields = _record_fields(record)

    if not fields["chipset"] or not fields["gpu_renderer"] or not fields["resolution"]:
        raise ValueError(
            f"{fields['source_id']!r} lacks the chipset, the GPU or the resolution "
            "and cannot become a coherent preset"
        )
    if not _pairing_is_possible(fields["chipset"], fields["gpu_renderer"]):
        raise ValueError(
            f"{fields['source_id']!r} pairs {fields['chipset']!r} with "
            f"{fields['gpu_renderer']!r}, which do not ship together"
        )

    release, sdk_int = derive_release(fields["os_text"], fields["announced"])
    if release not in BUILDABLE_RELEASES:
        raise ValueError(
            f"{fields['source_id']!r} runs Android {release}, which the engine cannot "
            "build a profile for"
        )

    gpu = derive_gpu(fields["gpu_renderer"], fields["chipset"])
    abi = derive_abi(fields["chipset"], fields["gpu_renderer"])
    screen = derive_screen(fields["resolution"], fields["display_size"])

    announced = announced_date(fields["announced"])
    slug = _model_slug(fields["brand"], fields["model"])
    board = _board_slug(fields["model"])

    if ram_options is None:
        ram_options = derive_ram_options(fields["memory_internal"])

    return DevicePreset(
        preset_id=variant_preset_id(slug, ram_options),
        manufacturer=fields["brand"],
        brand=fields["brand"],
        model=fields["model"],
        device=slug,
        product=slug,
        hardware=board,
        board=board,
        bootloader=f"{board}-1.0-0000001",
        fingerprint_product=slug,
        gpu=gpu,
        screen=screen,
        abi=abi,
        release=release,
        sdk_int=sdk_int,
        security_patch=_security_patch(announced, release),
        build_id=_build_id(release, announced),
        version_incremental=_version_incremental(slug, announced),
        ram_options=ram_options,
        cpu_options=derive_cpu_options(fields["chipset"]),
        dpr_options=_panel_options(screen.density_dpi)[1],
        density_options=_panel_options(screen.density_dpi)[0],
        year=year_of(fields["announced"]),
    )


def _build_via_source(
    record: dict, ram_options: tuple[int, ...] | None = None
) -> DevicePreset | None:
    """Build a preset through the source module, when it is available.

    The source module is imported lazily and inside a ``try``: it may not
    have landed yet, and the catalogue has to work either way.
    """

    try:
        from . import device_spec_source
    except Exception:  # pragma: no cover - the module may not exist yet
        return None

    builder = getattr(device_spec_source, "build_preset", None)
    if builder is None:
        return None

    fields = _record_fields(record)

    # The source module takes a ``DeviceRecord``; the row is turned into
    # one when the module exposes the dataclass.
    record_type = getattr(device_spec_source, "DeviceRecord", None)
    if record_type is None:
        return None

    try:
        source_record = record_type(
            brand=fields["brand"],
            model=fields["model"],
            chipset=fields["chipset"],
            gpu_renderer=fields["gpu_renderer"],
            resolution=fields["resolution"],
            display_size=fields["display_size"],
            memory_internal=fields["memory_internal"],
            os_text=fields["os_text"],
            announced=fields["announced"],
            body=fields["body"],
            source_id=fields["source_id"],
        )
    except TypeError:
        return None

    try:
        preset = builder(source_record)
    except Exception:
        return None

    if not isinstance(preset, DevicePreset):
        return None

    # The source module builds the panel with its own density ladder.  The
    # ratio is aligned onto the density bucket here, where the rule lives.
    preset = _align_screen(preset)

    # The GPU has to be the part that ships with the chipset, and the
    # architecture and the vendor have to name that same part.  The
    # rules are enforced here, where they live.
    preset = _align_gpu(preset, record)

    # The patch level has to be at or after the day the release opened
    # and at or after the day the handset was announced.  The source
    # module dates the patch from the announcement alone, and it reads
    # the announcement with a narrower date grammar than this module
    # does, so a handset announced with a month but no day ends up with a
    # patch that predates the handset.  The rule lives here.
    preset = _align_security_patch(preset, record)

    if ram_options is None:
        return preset

    # The source module derives the whole RAM list of the handset.  The
    # variant carries one amount, and the identifier names that amount.
    slug = preset.device
    return replace(
        preset,
        preset_id=variant_preset_id(slug, ram_options),
        ram_options=ram_options,
    )


def _align_security_patch(preset: DevicePreset, record: dict) -> DevicePreset:
    """Align the patch level of a preset onto its release and its announcement.

    A device that runs a release was updated to it after that release
    shipped, so its patch level is at or after the day the release
    opened.  A device that shipped in a given year has had a patch
    published since, so the patch is at or after the day it was
    announced.  The patch is the later of the two days.

    The source module reads the announcement date with a narrower
    grammar than this module uses, so a row that names a month but no
    day -- "2024, September" -- leaves it without a date at all, and
    the patch it derives predates the handset.  The correction is made
    here, where the wider grammar lives.
    """

    fields = _record_fields(record)
    expected = _security_patch(announced_date(fields["announced"]), preset.release)

    if preset.security_patch == expected:
        return preset

    return replace(preset, security_patch=expected)


def _align_screen(preset: DevicePreset) -> DevicePreset:
    """Align the pixel ratio of a preset onto its density bucket.

    The source module derives the ratio by dividing the raw pixel
    density by 160, which is not what the platform does: it rounds the
    density onto a bucket first and the ratio follows the bucket.  A
    panel whose ratio disagrees with its bucket is a panel no device
    has, so the ratio is read back off the bucket here.

    The option lists are brought along so that the preset still offers
    its own ratio.
    """

    dpr = preset.screen.device_pixel_ratio
    mapped = ratio_for_density(preset.screen.density_dpi)
    if mapped == dpr:
        return preset

    screen = preset.screen.model_copy(update={"device_pixel_ratio": mapped})
    options = tuple(sorted({*preset.dpr_options, mapped}, reverse=True))
    return replace(preset, screen=screen, dpr_options=options)


def _align_gpu(preset: DevicePreset, record: dict) -> DevicePreset:
    """Make the GPU of a preset physically coherent with its chipset.

    The source module does the parsing, but the catalogue is what has to
    be coherent: the GPU has to be the one that ships with the chipset,
    the architecture has to name that same GPU, the vendor has to be the
    vendor of that GPU and the texture limit has to be the limit that
    part reports.

    Whatever the source module made of those, they are read back off
    the chipset here, where the rules live.
    """

    fields = _record_fields(record)
    chipset = select_chipset(fields["chipset"])
    renderer = select_gpu_part(fields["gpu_renderer"], fields["chipset"])
    if not chipset or not renderer:
        return preset

    texture = _texture_size_for(renderer)
    vendor = _gpu_vendor_for(renderer, chipset)
    architecture = _architecture_for(renderer)

    gpu = preset.gpu
    if (
        gpu.unmasked_renderer != renderer
        or gpu.unmasked_vendor != vendor
        or gpu.max_texture_size != texture
    ):
        gpu = replace(
            gpu,
            unmasked_renderer=renderer,
            unmasked_vendor=vendor,
            max_texture_size=texture,
            max_cube_map_texture_size=texture,
            max_renderbuffer_size=texture,
            max_viewport_dims=(texture, texture),
        )

    abi = preset.abi
    if abi.gpu_architecture != architecture or abi.soc_model != chipset:
        abi = abi.model_copy(
            update={
                "gpu_architecture": architecture,
                "soc_model": chipset,
            }
        )

    if gpu is preset.gpu and abi is preset.abi:
        return preset
    return replace(preset, gpu=gpu, abi=abi)


def build_preset(record: dict, ram_options: tuple[int, ...] | None = None) -> DevicePreset:
    """Build the preset of one record.

    The source module does the parsing when it is importable; otherwise
    the local derivation does.  Either way the result is a physically
    coherent preset, and a record that cannot become one raises
    :class:`ValueError`.

    ``ram_options`` names the RAM configuration the preset carries: the
    tuple of the one amount this variant is.
    """

    if not _record_is_usable(record):
        fields = _record_fields(record)
        raise ValueError(
            f"{fields['source_id']!r} lacks the chipset, the GPU or the resolution "
            "and cannot become a coherent preset"
        )

    preset = _build_via_source(record, ram_options)
    if preset is not None:
        return preset

    return build_local_preset(record, ram_options)


# --------------------------------------------------------------------------
# Uniqueness
# --------------------------------------------------------------------------


def record_digest(record: dict) -> str:
    """A short, deterministic digest of a record.

    The digest is what tells two handsets that share a model name
    apart.  It has to be stable across processes, so ``hashlib`` is used
    and not ``hash()``: CPython randomises the string hash per process.
    """

    fields = _record_fields(record)
    material = "|".join(
        (
            fields["model"],
            fields["resolution"],
            fields["chipset"],
            fields["gpu_renderer"],
            fields["source_id"],
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:6]


def _with_preset_id(preset: DevicePreset, preset_id: str) -> DevicePreset:
    """A copy of a preset that carries a different identifier."""

    return replace(preset, preset_id=preset_id)


def _unique_preset_id(preset: DevicePreset, record: dict, taken: set[str]) -> str:
    """An identifier for a preset that no other preset carries.

    The first preset of a slug keeps it.  Every later one gets the short
    digest of its record appended, and -- should two records still
    collide -- the digest of the digest, so that the identifier is
    always unique and always the same for the same input.

    The collision is real: two datasets describe the same handset, and
    the second one has to be told apart from the first.  The digest is
    taken over the model, the resolution, the chipset and the source
    identifier, so two rows that describe the same device in the same
    way still collide and are separated, while two rows that describe
    different devices never collide by accident.
    """

    candidate = preset.preset_id
    if candidate not in taken:
        return candidate

    digest = record_digest(record)
    candidate = f"{preset.preset_id}-{digest}"
    salt = 0
    while candidate in taken:
        salt += 1
        extended = hashlib.sha256(f"{digest}:{salt}".encode()).hexdigest()[:6]
        candidate = f"{preset.preset_id}-{extended}"
    return candidate


#: The prefix every market identifier carries.
#:
#: The hand-written catalogues name their devices the way a shopper
#: would -- "oneplus-11-android13" for the OnePlus 11 -- and the market
#: dataset spells some of the same handsets the same way.  Without a
#: prefix of their own, a market handset would claim the identifier of
#: a hand-written one and the two would silently overwrite each other in
#: the fleet.  The prefix keeps the two catalogues apart, and it is
#: deterministic: the same handset always gets the same identifier.
MARKET_ID_PREFIX = "market-"


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------


def build_market_presets(records: list[dict]) -> tuple[DevicePreset, ...]:
    """Turn the market records into the presets of the market catalogue.

    Every record that carries a chipset, a GPU and a resolution becomes
    a preset.  A record that does not is skipped, and nothing else is:
    a record that names a handset is a device, and the only thing that
    can keep it out of the fleet is a missing specification.

    A record that names several RAM configurations becomes one preset
    per configuration, because the amount of memory is what a site can
    read off the device and what separates two profiles of the same
    handset.  The storage is *not* an axis: two profiles of the same
    model with the same RAM but a different storage are the same device
    to a site that looks at them.

    Every identifier is unique.  Two handsets that share a model name
    would otherwise share an identifier, and the fleet keys on it.
    """

    presets: list[DevicePreset] = []
    taken: set[str] = set()

    for record in records:
        if not isinstance(record, dict) or not _record_is_usable(record):
            continue

        fields = _record_fields(record)
        for variant in ram_variants(fields["memory_internal"]):
            try:
                preset = build_preset(record, variant)
            except (ValueError, KeyError, TypeError):
                continue

            preset = _with_preset_id(preset, f"{MARKET_ID_PREFIX}{preset.preset_id}")
            preset_id = _unique_preset_id(preset, record, taken)
            if preset_id != preset.preset_id:
                preset = _with_preset_id(preset, preset_id)
            taken.add(preset.preset_id)
            presets.append(preset)

    return tuple(presets)


def market_presets() -> tuple[DevicePreset, ...]:
    """The market catalogue, built once per process.

    The catalogue is cached, because the fleet asks for it once per
    profile and building three thousand presets is not something to do
    three thousand times.  A missing dataset yields an empty catalogue
    rather than an error: the fleet has to work without it.
    """

    global _MARKET_CACHE
    if _MARKET_CACHE is None:
        records = load_records()
        _MARKET_CACHE = build_market_presets(records)
    return _MARKET_CACHE


def reset_cache() -> None:
    """Drop the cached catalogue.

    The cache is process-wide, and a test that changes the dataset has
    to be able to ask for the catalogue to be built again.
    """

    global _MARKET_CACHE
    _MARKET_CACHE = None


def market_gpu_profiles() -> dict[str, GpuProfile]:
    """The GPU profiles of the market catalogue, keyed by the renderer.

    The engine keeps a table of the GPUs it knows about, so that the
    limits it reports always come from one entry and never contradict
    each other.  The market catalogue introduces renderers the
    hand-written tables do not name, and this is where those entries
    come from.
    """

    profiles: dict[str, GpuProfile] = {}
    for preset in market_presets():
        renderer = preset.gpu.unmasked_renderer
        if renderer and renderer not in profiles:
            profiles[renderer] = preset.gpu
    return profiles


def market_gpu_variants() -> dict[str, tuple[str, ...]]:
    """The renderer variants of every preset of the market catalogue.

    A handset that shipped with more than one GPU is a family, and the
    engine needs the family to know which parts it can pick from.  A
    handset that shipped with a single GPU names just that one.
    """

    variants: dict[str, tuple[str, ...]] = {}
    for preset in market_presets():
        renderer = preset.gpu.unmasked_renderer
        if renderer:
            variants[preset.preset_id] = (renderer,)
    return variants


__all__ = [
    "ANDROID_DPR",
    "BUILDABLE_RELEASES",
    "DATA_DIR",
    "DATA_PATH",
    "DENSITY_RATIO",
    "MARKET_ID_PREFIX",
    "MERGED_DATA_PATH",
    "SCREEN_DENSITY",
    "SCREEN_DPR",
    "SDK_BY_RELEASE",
    "VN_DATA_PATH",
    "build_local_preset",
    "build_market_presets",
    "build_preset",
    "default_data_path",
    "derive_abi",
    "derive_cpu_options",
    "derive_gpu",
    "derive_ram_options",
    "derive_release",
    "derive_screen",
    "load_records",
    "market_gpu_profiles",
    "market_gpu_variants",
    "market_presets",
    "ram_variants",
    "ratio_for_density",
    "record_digest",
    "reset_cache",
    "screen_dpr",
    "snap_density",
    "snap_dpr",
    "variant_preset_id",
]
