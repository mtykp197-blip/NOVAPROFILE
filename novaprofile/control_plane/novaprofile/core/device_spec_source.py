"""Turn real GSMArena device specifications into coherent Android presets.

The reference catalogue in :mod:`novaprofile.core.devices` was written by
hand: eleven devices, each one internally consistent.  A fleet of ten
thousand users cannot be built on eleven devices -- a vendor that groups
on the model, the board or the panel would put the whole fleet into
eleven buckets.

This module closes that gap.  It reads the real specifications of real
handsets and turns each of them into a :class:`~novaprofile.core.devices.DevicePreset`
whose parts agree with each other:

* the SoC decides which GPU the handset actually carries,
* the panel decides the pixel size, the ratio and the density,
* the operating system decides the release, the SDK level and the patch,
* the announcement date decides the build identifier.

The point of the module is not to produce *more* devices but to produce
*coherent* ones.  A preset that pairs a Snapdragon with a Mali, or that
claims a 4x texture limit on a GPU that has 16k, is a contradiction a
site can find by cross-checking two values it already reads.  Where the
specifications do not say what the hardware is, the record is skipped:
a missing chipset is not a licence to guess one.

The data comes in as JSON Lines, one object per line, as produced by the
scraper that walks the GSMArena specification pages.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..schema.hardware import AndroidAbiSoc, Screen
from .devices import DevicePreset, GpuProfile

# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceRecord:
    """One row of the scraped specification table.

    The fields mirror the GSMArena specification page: the chipset and the
    GPU the chipset carries, the panel and its size, the memory, the
    operating system the handset shipped with and the date it was
    announced.

    ``source_id`` identifies the row in the source dataset.  It is used
    to disambiguate two handsets that share a model name, so that the
    identifiers of the fleet stay unique.
    """

    brand: str
    model: str
    chipset: str
    gpu_renderer: str
    resolution: str
    display_size: str
    memory_internal: str
    os_text: str
    announced: str
    body: str
    source_id: str


# --------------------------------------------------------------------------
# GPU table
# --------------------------------------------------------------------------

#: The vendor string Chromium reports for an unmasked renderer.  The
#: unmasked vendor is the one a site can cross-check against the
#: architecture, so it has to name the actual GPU maker.
_UNMASKED_VENDORS: tuple[tuple[str, ...], str] = (
    (("adreno",), "Qualcomm"),
    (("mali",), "ARM"),
    (("immortalis",), "ARM"),
    (("xclipse",), "ARM"),
    (("maleoon",), "Huawei"),
    (("powervr",), "Imagination Technologies"),
)

#: The texture limit each GPU class reports.  The number is the value of
#: ``MAX_TEXTURE_SIZE`` on the real part; the cube map, the renderbuffer
#: and the viewport follow it.
#
#: * the budget Adreno parts stop at 8192,
#: * the mid-range Adreno parts stop at 8192 as well,
#: * the flagship Adreno parts reach 16384,
#: * every Mali, Immortalis, Xclipse and Maleoon part reaches 16384,
#: * PowerVR GE8300 is the exception and stops at 4096.
_TEXTURE_SIZE_BUDGET_ADRENO = 8192
_TEXTURE_SIZE_MID_ADRENO = 8192
_TEXTURE_SIZE_FLAGS_ADRENO = 16384
_TEXTURE_SIZE_ARM = 16384
_TEXTURE_SIZE_POWERVR = 4096

#: Adreno parts that are budget class.  They report an 8192 texture limit.
_BUDGET_ADRENO = frozenset({"619", "610", "642", "640", "610L", "642L"})

#: Adreno parts that are mid-range class.  They report an 8192 texture limit.
_MID_ADRENO = frozenset({"730", "732", "735", "730L", "732L"})

#: Adreno parts that are flagship class.  They report a 16384 texture limit.
_FLAGS_ADRENO = frozenset({"740", "750", "840", "830", "750L", "840L"})

#: The number of the GPU model inside a renderer string, e.g. "740" out
#: of "Adreno (TM) 740" and "G715" out of "Mali-G715 MC10".
_ADRENO_NUMBER = re.compile(r"adreno[^0-9]*(\d+)", re.IGNORECASE)
_ADRENO_SUFFIX = re.compile(r"adreno[^0-9]*\d+([a-z]*)", re.IGNORECASE)


def _normalise(text: str) -> str:
    """Collapse the whitespace of a string.

    The scraped strings carry the spacing of the source page, which is not
    always the spacing Chromium reports.
    """

    return re.sub(r"\s+", " ", text or "").strip()


def _texture_size_for(renderer: str) -> int:
    """The ``MAX_TEXTURE_SIZE`` the GPU behind ``renderer`` reports."""

    lowered = renderer.lower()

    if "adreno" in lowered:
        match = _ADRENO_NUMBER.search(lowered)
        number = match.group(1) if match else ""
        suffix_match = _ADRENO_SUFFIX.search(lowered)
        suffix = suffix_match.group(1).lower() if suffix_match else ""
        key = f"{number}{suffix}"
        if key in _BUDGET_ADRENO:
            return _TEXTURE_SIZE_BUDGET_ADRENO
        if key in _MID_ADRENO:
            return _TEXTURE_SIZE_MID_ADRENO
        if key in _FLAGS_ADRENO:
            return _TEXTURE_SIZE_FLAGS_ADRENO
        # An Adreno part the table does not name is a part from a family
        # that has not been released yet.  The 6xx and 7xx lines are the
        # flagships, everything below the 6xx is budget class.
        if number:
            if int(number) >= 600:
                return _TEXTURE_SIZE_FLAGS_ADRENO
            return _TEXTURE_SIZE_MID_ADRENO
        return _TEXTURE_SIZE_MID_ADRENO

    if "powervr" in lowered:
        return _TEXTURE_SIZE_POWERVR

    # Mali, Immortalis, Xclipse and Maleoon are all recent Arm designs
    # with a 16384 texture limit.
    return _TEXTURE_SIZE_ARM


def derive_gpu(renderer: str) -> GpuProfile:
    """Build the GPU profile of the part behind ``renderer``.

    The renderer string is the one the source data carries; the limits
    are the ones the part really reports.  The vendor and the renderer
    that Chromium reports are the masked ones, exactly as the reference
    catalogue reports them, so that a page cannot tell a generated profile
    from a real one by looking at the vendor string alone.
    """

    clean = _normalise(renderer)
    lowered = clean.lower()

    unmasked_vendor = "Qualcomm"
    for markers, vendor in _UNMASKED_VENDORS:
        if any(marker in lowered for marker in markers):
            unmasked_vendor = vendor
            break

    texture = _texture_size_for(clean)
    return GpuProfile(
        unmasked_vendor=unmasked_vendor,
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
#: carry.  The pair is what keeps a preset physically coherent: a
#: Snapdragon does not hold a Mali and a Dimensity does not hold an
#: Adreno.
_SOC_RULES: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (
        ("snapdragon", "qualcomm"),
        "Qualcomm",
        ("adreno",),
    ),
    (
        ("exynos",),
        "Samsung",
        ("xclipse", "mali"),
    ),
    (
        ("dimensity", "helio", "mediatek"),
        "MediaTek",
        ("immortalis", "mali"),
    ),
    (
        ("tensor",),
        "Google",
        ("mali", "immortalis"),
    ),
    (
        ("kirin", "huawei", "hisilicon"),
        "HiSilicon",
        ("maleoon", "mali"),
    ),
    (
        ("unisoc", "spreadtrum"),
        "Unisoc",
        ("mali", "powervr"),
    ),
    (
        ("mediatek",),
        "MediaTek",
        ("mali", "immortalis"),
    ),
)

#: The CPU cluster layout of the SoC families the engine knows.  The
#: layout describes how the cores are grouped, and it has to agree with
#: the core count the fleet reports.
_CLUSTER_LAYOUTS: dict[str, str] = {
    "Qualcomm": "1x3x4 Cortex-A78/A55/A55",
    "Samsung": "1x3x4 Cortex-A78/A55/A55",
    "MediaTek": "2x2 Cortex-A78/A55",
    "Google": "1x2x2 Cortex-X1/A78/A55",
    "HiSilicon": "2x2 Cortex-A76/A55",
    "Unisoc": "2x2 Cortex-A55/A55",
    "ARM": "2x2 Cortex-A76/A55",
}

#: The core count of the flagship families, used when the source data
#: does not state it.
_DEFAULT_CORES = 8


def _soc_vendor_and_family(chipset: str) -> tuple[str, tuple[str, ...]] | None:
    """The vendor of a chipset and the GPU families it can carry."""

    lowered = chipset.lower()
    for markers, vendor, families in _SOC_RULES:
        if any(marker in lowered for marker in markers):
            return vendor, families
    return None


def _cluster_layout(vendor: str, cores: int) -> str:
    """A cluster layout that matches the core count and the vendor."""

    if cores >= 8:
        return _CLUSTER_LAYOUTS.get(vendor, "2x2 Cortex-A76/A55")
    if cores >= 4:
        return "2x2 Cortex-A78/A55" if vendor == "Qualcomm" else "2x2 Cortex-A76/A55"
    return "2x2 Cortex-A55/A55"


def _architecture_for(renderer: str) -> str:
    """The architecture string of a renderer, stripped of its suffix.

    Chromium reports the architecture in the same form as the renderer,
    but without the marketing suffix: a handset reports the renderer
    "Adreno (TM) 750" and the architecture "Adreno 750".  The two have
    to name the same part, so the suffix is dropped here.
    """

    clean = _normalise(renderer)
    match = re.search(r"adreno[^0-9]*(\d+)[a-z]*", clean, re.IGNORECASE)
    if match:
        return f"Adreno {match.group(1)}"
    match = re.search(r"mali[- ]?g\d+", clean, re.IGNORECASE)
    if match:
        return match.group(0)
    match = re.search(r"immortalis[- ]?g\d+", clean, re.IGNORECASE)
    if match:
        return match.group(0)
    match = re.search(r"xclipse\s*\d+", clean, re.IGNORECASE)
    if match:
        return match.group(0)
    match = re.search(r"maleoon\s*\d+", clean, re.IGNORECASE)
    if match:
        return match.group(0)
    match = re.search(r"powervr[^0-9]*[a-z]+\d+", clean, re.IGNORECASE)
    if match:
        return match.group(0)
    return clean


def derive_abi(chipset: str, gpu_renderer: str) -> AndroidAbiSoc:
    """Build the ABI/SoC description of a chipset and its GPU.

    The SoC vendor comes from the chipset name and the GPU architecture
    from the renderer string: a Snapdragon is a Qualcomm part that holds
    an Adreno, a Dimensity is a MediaTek part that holds a Mali or an
    Immortalis, an Exynos is a Samsung part that holds an Xclipse, a
    Tensor is a Google part that holds a Mali and a Kirin is a HiSilicon
    part that holds a Maleoon.

    The architecture always describes the GPU that is really there, so
    that the cross-check a site performs -- the renderer against the
    architecture -- finds the same part on both sides.
    """

    resolved = _soc_vendor_and_family(chipset)
    if resolved is None:
        # A chipset the table does not name is an Arm design that ships
        # with a Mali part; the vendor stays the neutral one.
        vendor = "ARM"
        families = ("mali", "immortalis")
    else:
        vendor, families = resolved

    renderer = _normalise(gpu_renderer)
    architecture = _architecture_for(renderer)

    cores = _core_count(chipset)
    return AndroidAbiSoc(
        supported_abis=["arm64-v8a", "armeabi-v7a"],
        supported_64_bit_abis=["arm64-v8a"],
        supported_32_bit_abis=["armeabi-v7a"],
        primary_abi="arm64-v8a",
        is_64_bit=True,
        soc_vendor=vendor,  # type: ignore[arg-type]
        soc_model=chipset.strip(),
        cpu_cluster_layout=_cluster_layout(vendor, cores),
        gpu_architecture=architecture,
    )


def _gpu_family(renderer: str) -> str:
    """The family the GPU of a renderer belongs to."""

    lowered = renderer.lower()
    for family in ("adreno", "mali", "immortalis", "xclipse", "maleoon", "powervr"):
        if family in lowered:
            return family
    return ""


def _pairing_is_possible(chipset: str, gpu_renderer: str) -> bool:
    """Whether a chipset can carry the GPU the record names.

    A record that pairs a Snapdragon with a Mali is a broken row, not a
    device.  Emitting it would put a contradiction into the fleet, which
    is exactly what the whole module exists to prevent.
    """

    resolved = _soc_vendor_and_family(chipset)
    if resolved is None:
        return True
    _, families = resolved
    family = _gpu_family(gpu_renderer)
    if not family:
        return False
    return family in families


#: The number of CPU cores a chipset has, when the data says so.
_CORE_COUNT = re.compile(r"(\d+)\s*[- ]?\s*core", re.IGNORECASE)
_OCTA_CORE = re.compile(r"octa[- ]?core", re.IGNORECASE)


def _core_count(chipset: str) -> int:
    """The number of CPU cores a chipset carries."""

    match = _CORE_COUNT.search(chipset)
    if match:
        value = int(match.group(1))
        if 2 <= value <= 16:
            return value
    if _OCTA_CORE.search(chipset):
        return 8
    return _DEFAULT_CORES


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------

#: "1080 x 2400 pixels, 20:9 ratio (~404 ppi density)"
_RESOLUTION = re.compile(
    r"(?P<width>\d{3,4})\s*[x×]\s*(?P<height>\d{3,4})\s*pixels",
    re.IGNORECASE,
)
_PPI = re.compile(r"~?\s*(?P<ppi>\d{2,4}(?:\.\d+)?)\s*ppi", re.IGNORECASE)

#: The ratios the device pixel ratio may take.  A panel reports one of
#: these buckets, never an arbitrary number.
#:
#: The set is the whole Android quantisation vocabulary: the 1.0 and
#: the 1.5 ends carry the low-density tablet panels, and the 3.25 and
#: the 4.0 ends carry the high-density flagship panels.
_ALLOWED_DPR = (1.0, 1.5, 1.75, 2.0, 2.25, 2.5, 2.625, 2.75, 3.0, 3.25, 3.5, 4.0)

#: The density buckets a panel reports.
#:
#: These are the buckets the platform actually rounds the raw pixel
#: density onto, and they are not evenly spaced: 213 sits between 160
#: and 240, 411 and 420 sit between 400 and 440, and 520, 525 and 545
#: sit between 480 and 560.
_ALLOWED_DENSITY = (
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
#: The platform rounds the raw density onto a bucket and the browser
#: then reads the ratio of the *panel*, so the 213 bucket -- the 1.333x
#: bucket of the low-density panels -- is reported as 1.5 by the
#: platform's own table, and the 411 and 420 buckets both land on
#: 2.625.  A ratio picked by multiplying the density by 1/160 would be
#: a number no Android device ever reports, and the panel would carry
#: a density and a ratio that disagree with each other.
_DENSITY_RATIO: dict[int, float] = {
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

#: The standard Android density ladder.  A handset reports one of these
#: buckets as its density, and the device pixel ratio is the one the
#: bucket reports.
#:
#: The bucket is chosen by the manufacturer, and the bucket that a panel
#: of a given pixel density carries is the one nearest to it: a 294 ppi
#: panel is a 320 dpi, 2.0x panel, and a 404 ppi panel is a 400 dpi,
#: 2.5x one.  Deriving the ratio from the raw pixel density instead
#: would report 1.75x for a panel that every real handset of that size
#: reports as 2.0x.
_DENSITY_LADDER = _ALLOWED_DENSITY

#: The standard Android density ladder.
#:
#: These are the buckets a manufacturer picks the density of a panel
#: from: the 120, 160, 213 and 240 buckets carry the low-density panels
#: and the 320, 480 and 640 buckets carry the high-density ones.  The
#: wider vocabulary of :data:`_ALLOWED_DENSITY` is what a panel
#: *reports* once it has been quantised, so the two are not the same
#: set.
_STANDARD_DENSITY = (120, 160, 213, 240, 320, 480, 640)

#: The density a panel without a pixel density falls back to.
_FALLBACK_DENSITY = 420


def _snap_dpr(value: float) -> float:
    """Snap a computed ratio onto a bucket a real panel reports."""

    return min(_ALLOWED_DPR, key=lambda allowed: (abs(allowed - value), allowed))


def _snap_density(value: float) -> int:
    """Snap a computed density onto a bucket a real panel reports."""

    return min(_ALLOWED_DENSITY, key=lambda allowed: (abs(allowed - value), allowed))


def _density_bucket(ppi: float) -> int:
    """The Android density bucket a panel of that pixel density carries.

    A manufacturer picks the bucket of a panel from the standard
    Android ladder -- 120, 160, 213, 240, 320, 480 and 640 -- and the
    bucket it picks is the one nearest to the panel's pixel density: a
    294 ppi panel is a 320 dpi, 2.0x panel, and a 404 ppi panel is a
    400 dpi, 2.5x one.

    The wider vocabulary of buckets -- the 280, the 360, the 411, the
    520 and the rest -- is what a panel *reports* once it has been
    quantised, so it is the vocabulary the ratio table is keyed by, but
    it is not the vocabulary a panel is built from.  Snapping onto the
    wider set would put a 294 ppi panel on the 280 bucket and report
    1.75x for a panel that every real handset of that size reports as
    2.0x.

    The panel is therefore snapped onto the *ladder* first, and the
    ratio that follows comes from the ladder bucket.  A panel that
    reports a bucket outside the ladder -- the 520 of a 515 ppi panel,
    for instance -- keeps that bucket, because the bucket a panel
    reports is the one the platform quantised it into and the fleet has
    to report the same one.
    """

    if ppi <= 0:
        return _FALLBACK_DENSITY

    # A panel's density is snapped onto the bucket the platform
    # quantises it into.  The bucket vocabulary is the full one --
    # the 280, the 360, the 411, the 520 and the rest -- because that
    # is what a real panel reports and what a real device therefore
    # reports to a site.  The narrower ladder is the set a panel is
    # *built* from; using it here would put a 404 ppi panel on the
    # 400 bucket and a 515 ppi panel on the 480 one, and both of those
    # are buckets the device never says it has.
    #
    # The nearest bucket wins, and a panel exactly between two of them
    # takes the denser one.
    return min(_ALLOWED_DENSITY, key=lambda bucket: (abs(bucket - ppi), -bucket))


def derive_screen(resolution: str, display_size: str) -> Screen:
    """Build the screen of a handset from its panel specification.

    The pixel width and the pixel height are the real ones: they are
    taken verbatim from the resolution string, because a handset that
    claims a different pixel size than its panel is a contradiction the
    site sees when it compares the reported screen with the reported
    viewport.

    The device pixel ratio and the density follow from the pixel density
    of the panel, and both are the buckets a real panel reports.
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
        # Without a pixel density the diagonal is the only remaining hint.
        # The pixel density of the panel is then computed from the pixel
        # size and the size of the display.
        diagonal_inches = _display_inches(display_size)
        ppi = (
            (width**2 + height**2) ** 0.5 / diagonal_inches if diagonal_inches > 0 else 0.0
        )

    if ppi > 0:
        density = _snap_density(_density_bucket(ppi))
        dpr = _DENSITY_RATIO[density]
    else:
        density = _FALLBACK_DENSITY
        dpr = _DENSITY_RATIO[density]

    return Screen(
        width=width,
        height=height,
        avail_width=width,
        avail_height=height - 48,
        avail_left=0,
        avail_top=0,
        color_depth=24,
        pixel_depth=24,
        device_pixel_ratio=dpr,  # type: ignore[arg-type]
        density_dpi=density,  # type: ignore[arg-type]
    )


def _display_inches(display_size: str) -> float:
    """The diagonal of a display size such as "6.7 inches"."""

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:inches|\")", display_size or "", re.IGNORECASE)
    if match is None:
        return 0.0
    return float(match.group(1))


# --------------------------------------------------------------------------
# Operating system
# --------------------------------------------------------------------------

#: The SDK level of every Android release the fleet covers.
_SDK_BY_RELEASE: dict[str, int] = {
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
_RELEASE_BY_YEAR: tuple[tuple[int, str], ...] = (
    (2024, "14"),
    (2023, "13"),
    (2022, "12"),
    (2021, "12"),
    (2020, "11"),
    (2019, "10"),
    (2018, "9"),
    (2017, "8"),
    (2016, "7"),
)


def derive_release(os_text: str, announced: str) -> tuple[str, int]:
    """The Android release and the SDK level of a handset.

    The first "Android N" in the operating system text is the release the
    handset shipped with; the text that follows it names the versions it
    can be upgraded to, which is not what the device reports.  When the
    text names no version at all, the release follows from the year the
    handset was announced.
    """

    match = _ANDROID_VERSION.search(os_text or "")
    if match is not None:
        release = match.group(1)
        sdk = _SDK_BY_RELEASE.get(release)
        if sdk is not None:
            return release, sdk

    year = _year_of(announced)
    for candidate_year, release in _RELEASE_BY_YEAR:
        if year >= candidate_year:
            return release, _SDK_BY_RELEASE[release]
    return "12", _SDK_BY_RELEASE["12"]


def _year_of(announced: str) -> int:
    """The year a handset was announced."""

    match = re.search(r"(19|20)\d{2}", announced or "")
    if match is None:
        return 2023
    return int(match.group(0))


def _announced_date(announced: str) -> date | None:
    """The date a handset was announced, when the text carries one.

    GSMArena writes the announcement as "2024, January 17", but the
    scraped rows carry every shape a date can take, so the common ones
    are all read.
    """

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

    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    """A date, or ``None`` when the parts do not make one."""

    try:
        return date(year, month, day)
    except ValueError:
        return None


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


#: The releases the engine can build a profile for.  The Android build
#: model accepts these and nothing else, so a preset that claims another
#: release could never become a profile: the device would be a record
#: that no browser could ever report.
_BUILDABLE_RELEASES = frozenset({"13", "14", "15"})


def _build_id(release: str, announced: date | None) -> str:
    """The Android build identifier of a handset.

    The identifier looks like "UP1A.231005.007": a letter prefix, a
    digit, the date the build was made as ``YYMMDD``, and a three digit
    build number.  The date comes from the day the handset was announced.
    """

    prefix = _BUILD_PREFIX.get(release, _DEFAULT_BUILD_PREFIX)
    if announced is None:
        # Without an announcement date the build is dated in the middle of
        # the year the platform branch opened, which is what a build of
        # that branch looks like.
        stamp = f"{_release_year(release) % 100:02d}0105"
    else:
        stamp = announced.strftime("%y%m%d")
    return f"{prefix}.{stamp}.{_build_number(announced):03d}"


def _release_year(release: str) -> int:
    """The year the Android release with that number shipped."""

    for candidate_year, candidate_release in _RELEASE_BY_YEAR:
        if candidate_release == release:
            return candidate_year
    return 2024


def _build_number(announced: date | None) -> int:
    """The three digit build number of a build."""

    if announced is None:
        return 7
    return ((announced.month * 7 + announced.day * 3) % 900) + 10


def _version_incremental(preset_slug: str, announced: date | None) -> str:
    """The build number a handset reports as its incremental version.

    The value is a plain counter that grows with every build, so it is
    derived from the handset itself and the day it was announced.  It has
    to be stable: the same handset always reports the same number.
    """

    digest = 0
    for char in preset_slug:
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    if announced is not None:
        digest = (digest + announced.toordinal()) & 0xFFFFFFFF
    return str((digest % 9000000) + 1000000)


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


def _slug(text: str) -> str:
    """A lowercase slug of a name.

    Every character that is not a letter or a digit becomes a dash, and
    runs of dashes collapse into one.
    """

    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    return cleaned.strip("-")


def _model_slug(record: DeviceRecord) -> str:
    """The slug of a handset: its brand and its model name.

    The brand and the model together identify the handset the way a
    shopper would name it, and they keep the identifiers of two handsets
    of the same brand apart.
    """

    brand = _slug(record.brand)
    model = _slug(record.model)
    if brand and model:
        if model.startswith(brand):
            return model
        return f"{brand}-{model}"
    return model or brand or _slug(record.source_id)


def _board_slug(record: DeviceRecord) -> str:
    """The board name of a handset.

    Real boards carry the codename of the handset rather than its model
    name, but the model name is what the scraped data holds, and it is
    the part of the identifier that has to stay unique.
    """

    model = _slug(record.model)
    return model or _slug(record.source_id)


# --------------------------------------------------------------------------
# Memory and cores
# --------------------------------------------------------------------------

#: "128GB 8GB RAM, 256GB 12GB RAM"
_RAM_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*GB\s+RAM", re.IGNORECASE)
_RAM_AMOUNT_ALT = re.compile(r"(\d+(?:\.\d+)?)\s*GB\s*RAM", re.IGNORECASE)

#: The RAM amounts a handset ships with when the data names none.
_DEFAULT_RAM = (4, 6, 8)


def derive_ram_options(memory: str) -> tuple[int, ...]:
    """The RAM configurations a handset ships with.

    The memory text names the storage and the RAM of every configuration
    the handset was sold in; only the RAM amounts are of interest here.
    """

    amounts: list[int] = []
    for pattern in (_RAM_AMOUNT, _RAM_AMOUNT_ALT):
        for match in pattern.finditer(memory or ""):
            value = float(match.group(1))
            if 0 < value <= 64:
                rounded = int(round(value))
                if rounded not in amounts:
                    amounts.append(rounded)
    if not amounts:
        return _DEFAULT_RAM
    return tuple(sorted(amounts))


def derive_cpu_options(chipset: str) -> tuple[int, ...]:
    """The core counts a chipset can report.

    A chipset that names its core count has exactly that many cores; the
    fleet reports one value, because the core count of a handset is a
    property of the chipset and not of the unit.
    """

    return (_core_count(chipset),)


def _panel_options(density: int) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """The density buckets and the ratios a panel can report.

    The two lists are one list of pairs, not two lists that happen to
    be near each other.  A handset is offered a handful of panels, and
    every one of them is a bucket of the Android vocabulary together
    with the ratio that bucket reports: the generator reads the density
    and the ratio independently, so two lists that disagree would hand
    it a density and a ratio that no device has ever had.
    """

    buckets = [density]
    for candidate in sorted(
        _ALLOWED_DENSITY, key=lambda value: (abs(value - density), value)
    ):
        if candidate not in buckets:
            buckets.append(candidate)
        if len(buckets) == 3:
            break

    ordered = tuple(sorted(buckets))
    ratios = tuple(sorted({_DENSITY_RATIO[bucket] for bucket in ordered}, reverse=True))

    return ordered, ratios


def _dpr_options(dpr: float) -> tuple[float, ...]:
    """The device pixel ratios a panel can report.

    The computed ratio is always among them: the panel does have that
    ratio, and the other one is the neighbouring bucket the same panel is
    reported with.
    """

    options = [dpr]
    for candidate in (dpr + 0.125, dpr - 0.125):
        snapped = _snap_dpr(round(candidate, 3))
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
    branch opened is used, which is the day the first build of that
    release shipped.
    """

    if announced is not None:
        return announced.isoformat()
    return date(_release_year(release), 1, 5).isoformat()


# --------------------------------------------------------------------------
# The record loader
# --------------------------------------------------------------------------

#: The fields a row has to carry to be a row at all.  The fields that
#: decide whether the row can become a preset are checked later, so that
#: a row with a missing chipset is skipped at the point where the
#: decision belongs.
_IDENTIFYING_FIELDS = ("brand", "model")


def load_device_records(path: Path) -> list[DeviceRecord]:
    """Read the scraped specification rows out of a JSON Lines file.

    One object per line, one line per handset.  A line that is not
    valid JSON, or that does not describe a handset at all, is skipped:
    a broken row in a scraped file must not take the whole dataset down
    with it.  A row that carries no chipset or no panel is kept, because
    the decision to skip it belongs to the preset builder.
    """

    records: list[DeviceRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            record = _record_from_payload(payload, number)
            if record is not None:
                records.append(record)
    return records


def _record_from_payload(payload: dict, number: int) -> DeviceRecord | None:
    """Build a record out of one decoded JSON object."""

    values = {key: _text(payload.get(key)) for key in _IDENTIFYING_FIELDS}
    if not any(values.values()):
        return None

    return DeviceRecord(
        brand=values["brand"],
        model=values["model"],
        chipset=_text(payload.get("chipset")),
        gpu_renderer=_text(payload.get("gpu_renderer")),
        resolution=_text(payload.get("resolution")),
        display_size=_text(payload.get("display_size")),
        memory_internal=_text(payload.get("memory_internal")),
        os_text=_text(payload.get("os_text")),
        announced=_text(payload.get("announced")),
        body=_text(payload.get("body")),
        source_id=_text(payload.get("source_id")) or f"row-{number}",
    )


def _text(value: object) -> str:
    """The stripped text of a field, whatever it happens to hold."""

    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value if item is not None).strip()
    return str(value).strip()


# --------------------------------------------------------------------------
# The preset
# --------------------------------------------------------------------------


def build_preset(record: DeviceRecord) -> DevicePreset:
    """Build the preset of one handset.

    Every part of the preset is taken from the record: the GPU from the
    renderer the chipset carries, the screen from the panel, the release
    from the operating system and the identifiers from the model name.

    A record that cannot become a coherent preset raises
    :class:`ValueError`: it lacks the chipset, the GPU or the panel, it
    pairs a chipset with a GPU the chipset does not carry, or it runs an
    Android release the engine cannot build a profile for.
    """

    if not record.chipset or not record.gpu_renderer or not record.resolution:
        raise ValueError(
            f"{record.source_id!r} lacks the chipset, the GPU or the resolution "
            "and cannot become a coherent preset"
        )
    if not _pairing_is_possible(record.chipset, record.gpu_renderer):
        raise ValueError(
            f"{record.source_id!r} pairs {record.chipset!r} with "
            f"{record.gpu_renderer!r}, which do not ship together"
        )

    release, sdk_int = derive_release(record.os_text, record.announced)
    if release not in _BUILDABLE_RELEASES:
        raise ValueError(
            f"{record.source_id!r} runs Android {release}, which the engine cannot "
            "build a profile for"
        )

    gpu = derive_gpu(record.gpu_renderer)
    abi = derive_abi(record.chipset, record.gpu_renderer)
    screen = derive_screen(record.resolution, record.display_size)

    announced = _announced_date(record.announced)
    slug = _model_slug(record)
    board = _board_slug(record)
    year = _year_of(record.announced)

    return DevicePreset(
        preset_id=f"{slug}-android{release}",
        manufacturer=record.brand,
        brand=record.brand,
        model=record.model,
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
        ram_options=derive_ram_options(record.memory_internal),
        cpu_options=derive_cpu_options(record.chipset),
        dpr_options=_panel_options(screen.density_dpi)[1],
        density_options=_panel_options(screen.density_dpi)[0],
        year=year,
    )


def build_presets(records: list[DeviceRecord]) -> tuple[DevicePreset, ...]:
    """Build the presets of a list of records.

    A record that cannot become a coherent preset is skipped rather
    than guessed at.  Two handsets that share a model name would also
    share a preset identifier, so the second one carries the identifier
    of its source row: the fleet has to be able to hold both.
    """

    presets: list[DevicePreset] = []
    seen: set[str] = set()
    for record in records:
        try:
            preset = build_preset(record)
        except (ValueError, KeyError):
            continue
        if preset.preset_id in seen:
            suffix = _slug(record.source_id)
            candidate = f"{preset.preset_id}-{suffix}" if suffix else ""
            if not candidate or candidate in seen:
                candidate = f"{preset.preset_id}-{len(presets)}"
            while candidate in seen:
                candidate = f"{candidate}-x"
            preset = _with_preset_id(preset, candidate)
        seen.add(preset.preset_id)
        presets.append(preset)
    return tuple(presets)


def _with_preset_id(preset: DevicePreset, preset_id: str) -> DevicePreset:
    """A copy of a preset that carries a different identifier."""

    values = {field: getattr(preset, field) for field in preset.__dataclass_fields__}
    values["preset_id"] = preset_id
    return DevicePreset(**values)


__all__ = [
    "DeviceRecord",
    "load_device_records",
    "derive_gpu",
    "derive_screen",
    "derive_abi",
    "derive_release",
    "derive_ram_options",
    "derive_cpu_options",
    "build_preset",
    "build_presets",
]
