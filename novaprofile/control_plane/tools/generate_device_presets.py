"""Expand the device preset catalog from the reference catalogue.

The hand-curated catalogue in ``novaprofile.core.devices`` holds eleven
handsets.  A fleet of any size needs more than that: the whole point of
an anti-detect product is that the operator can tell two of its
profiles apart, and a catalogue of eleven handsets runs out of distinct
device identities long before the operator runs out of accounts.

The reference catalogue in ``source tham khảo/device-catalog.json``
carries two thousand handsets from the Playwright device descriptors.
This script turns those into :class:`DevicePreset` entries.

What can be taken from the catalogue
------------------------------------
The catalogue is authoritative about the panel, the memory, the core
count and the graphics part, and those are the readings a site uses to
group a fleet.  They are copied as they are.

What has to be derived
----------------------
A build fingerprint names the device, the product, the hardware and the
board, and the catalogue carries none of those.  They are derived from
the model's own code, which is the one string the catalogue does carry
and which appears in the fingerprint of the real handset.

The rule is the one the platform itself uses: the device codename, the
product codename and the fingerprint product are the same string for
the great majority of handsets, and the board is the SoC vendor for
the handsets whose board is a Qualcomm or MediaTek reference design
and the device codename for the rest.

The bootloader version follows the pattern the manufacturers use --
``<codename>-<major>.<minor>-<build date>`` -- with the date derived
from the release the handset shipped with.

What is left alone
------------------
The graphics part is copied verbatim, including its vendor string,
because that string is what a site reads.  The WebGL limits are
derived from the part's class, because the limits are a property of
the silicon and not of the catalogue's row.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from novaprofile.core.devices import (  # noqa: E402
    AndroidAbiSoc,
    DevicePreset,
    GpuProfile,
    Screen,
)
from novaprofile.core.soc_cores import LAYOUT_BY_CORES, vendor_for_chipset  # noqa: E402

CATALOG = HERE.parent.parent.parent / "source tham khảo" / "device-catalog.json"
OUTPUT = HERE.parent / "novaprofile" / "core" / "generated_presets.py"

# --------------------------------------------------------------------------
# The Android release -> SDK level table.
# --------------------------------------------------------------------------
SDK_BY_RELEASE: dict[str, int] = {
    "10": 29,
    "11": 30,
    "12": 31,
    "13": 33,
    "14": 34,
    "15": 35,
    "16": 36,
}

#: The build-id prefixes the manufacturers use, per Android release.
#: The prefix is what a fingerprint carries, and it has to agree with
#: the release the profile claims.
BUILD_PREFIX_BY_RELEASE: dict[str, str] = {
    "12": "SP1A",
    "13": "TQ3A",
    "14": "UQ1A",
    "15": "AP1A",
    "16": "BP1A",
}

#: The security-patch dates the releases shipped with.  The patch date
#: is a property of the release train, and a fleet that reports a patch
#: date from before its own release is a fleet the platform's own
#: update servers can contradict.
PATCH_BY_RELEASE: dict[str, str] = {
    "12": "2022-10-05",
    "13": "2023-05-05",
    "14": "2024-01-05",
    "15": "2024-10-05",
    "16": "2025-07-05",
}

# --------------------------------------------------------------------------
# The graphics parts.
# --------------------------------------------------------------------------
#: The graphics parts the catalogue names, and the WebGL limits of their
#: class.  The limits are a property of the silicon: a part from the
#: high tier supports the 16k texture size, an entry-level part does
#: not, and a site that reads the limit against the renderer string can
#: tell a mismatched pair from a real one.
GPU_TABLE: dict[str, tuple[str, str, int]] = {
    "Adreno (TM) 840": ("Qualcomm", "Adreno 840", 16384),
    "Adreno (TM) 830": ("Qualcomm", "Adreno 830", 16384),
    "Adreno (TM) 760": ("Qualcomm", "Adreno 760", 16384),
    "Adreno (TM) 750": ("Qualcomm", "Adreno 750", 16384),
    "Adreno (TM) 742": ("Qualcomm", "Adreno 742", 16384),
    "Adreno (TM) 740": ("Qualcomm", "Adreno 740", 16384),
    "Adreno (TM) 732": ("Qualcomm", "Adreno 732", 16384),
    "Adreno (TM) 730": ("Qualcomm", "Adreno 730", 16384),
    "Adreno (TM) 720": ("Qualcomm", "Adreno 720", 16384),
    "Adreno (TM) 710": ("Qualcomm", "Adreno 710", 16384),
    "Adreno (TM) 642L": ("Qualcomm", "Adreno 642L", 8192),
    "Adreno (TM) 642": ("Qualcomm", "Adreno 642", 16384),
    "Adreno (TM) 640": ("Qualcomm", "Adreno 640", 16384),
    "Adreno (TM) 630": ("Qualcomm", "Adreno 630", 16384),
    "Adreno (TM) 619": ("Qualcomm", "Adreno 619", 16384),
    "Adreno (TM) 618": ("Qualcomm", "Adreno 618", 16384),
    "Adreno (TM) 610": ("Qualcomm", "Adreno 610", 16384),
    "Adreno (TM) 540": ("Qualcomm", "Adreno 540", 16384),
    "Adreno (TM) 506": ("Qualcomm", "Adreno 506", 8192),
    "Adreno (TM) 405": ("Qualcomm", "Adreno 405", 8192),
    "Mali-G78 MP20": ("ARM", "Mali-G78 MP20", 16384),
    "Mali-G715": ("ARM", "Mali-G715", 16384),
    "Mali-G710 MC10": ("ARM", "Mali-G710 MC10", 16384),
    "Mali-G68 MC4": ("ARM", "Mali-G68 MC4", 16384),
    "Mali-G68 MC2": ("ARM", "Mali-G68 MC2", 16384),
    "Mali-G615 MC6": ("ARM", "Mali-G615 MC6", 16384),
    "Mali-G610 MC6": ("ARM", "Mali-G610 MC6", 16384),
    "Mali-G57 MC2": ("ARM", "Mali-G57 MC2", 16384),
    "Mali-G57 MC1": ("ARM", "Mali-G57 MC1", 16384),
    "Mali-G52 MC2": ("ARM", "Mali-G52 MC2", 16384),
    "Mali-G52 MC1": ("ARM", "Mali-G52 MC1", 16384),
    "Immortalis-G925": ("ARM", "Immortalis-G925", 16384),
    "Immortalis-G720 MC12": ("ARM", "Immortalis-G720 MC12", 16384),
    "Immortalis-G715 MC10": ("ARM", "Immortalis-G715 MC10", 16384),
    "Immortalis-G715": ("ARM", "Immortalis-G715", 16384),
    "Immortalis-G710 MC10": ("ARM", "Immortalis-G710 MC10", 16384),
    "PowerVR GE8320": ("MediaTek", "PowerVR GE8320", 8192),
    "PowerVR Rogue GE7400": ("MediaTek", "PowerVR Rogue GE7400", 16384),
}

#: The fallback part for a renderer the table does not name.
DEFAULT_GPU = ("ARM", "Mali-G57 MC2", 16384)

#: The vendor name the catalogue uses -> the vendor the schema accepts.
#: The schema names the *SoC* vendor, and a PowerVR part in a handset of
#: this catalogue sits on a MediaTek or Unisoc SoC, while a Mali part
#: sits on an ARM one and an Adreno on a Qualcomm one.
SOC_VENDOR_BY_GPU_VENDOR: dict[str, str] = {
    "ARM": "ARM",
    "Qualcomm": "Qualcomm",
    "Imagination": "MediaTek",
    "Imagination Technologies": "MediaTek",
    "Samsung": "Samsung",
    "MediaTek": "MediaTek",
    "Broadcom": "Broadcom",
    "Google": "Google",
}

#: The vendor name the catalogue uses -> the vendor the WebGL schema
#: accepts.  The schema names the *graphics* vendor, and the catalogue
#: shortens the Imagination one.
WEBGL_VENDOR_BY_GPU_VENDOR: dict[str, str] = {
    "ARM": "ARM",
    "Qualcomm": "Qualcomm",
    "Imagination": "Imagination Technologies",
    "Samsung": "Samsung",
    "MediaTek": "ARM",
    "Google": "Google Inc. (ARM)",
}

#: The SoC vendor by graphics vendor.  The board is the reference design
#: the SoC's vendor publishes, and the socket name is what the build
#: fingerprint carries.
BOARD_BY_GPU_VENDOR: dict[str, str] = {
    "Qualcomm": "qcom",
    "ARM": "qcom",
    "MediaTek": "mtk",
    "Imagination": "mtk",
    "Samsung": "qcom",
    "Google": "qcom",
}

#: The CPU cluster layout by core count.  The layout is what the
#: platform reports through JNI, and it follows the big/little
#: arrangement of the part: an eight-core part is almost always one
#: prime core, three big cores and four little ones.
#:
#: Every layout here adds up to the core count it stands for, because
#: the count is read back off the layout.  A layout that named a
#: different number of clusters than the chipset carries would put a
#: core count on the record that contradicts the SoC next to it.
#:
#: The layouts are the ones the core table holds, so that a chipset
#: and the layout that carries it can never drift apart.
CLUSTER_BY_CORES: dict[int, str] = dict(LAYOUT_BY_CORES)


#: The layout of the chipset, by the name of the chipset.
#:
#: A chipset is built out of its own cores, and a site that reads the
#: layout reads the names of the cores out of it.  A Snapdragon 8 Gen
#: says "Cortex-X3/A78/A55", a Helio G99 says "Cortex-A76/A55" and an
#: Exynos 1480 says "Cortex-A78/A55", so the layout of a chipset is
#: its own and not the one of the count it carries.
CLUSTER_BY_CHIPSET: dict[str, str] = {
    "snapdragon 200": "2x2 Cortex-A53/A53",
    "snapdragon 400": "2x2 Cortex-A53/A53",
    "snapdragon 410": "2x2 Cortex-A53/A53",
    "snapdragon 425": "2x2 Cortex-A53/A53",
    "snapdragon 430": "2x2 Cortex-A53/A53",
    "snapdragon 615": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 625": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 626": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 630": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 636": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 660": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 662": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 665": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 670": "1x2x3x2 Cortex-A75/A55",
    "snapdragon 675": "1x3x4 Cortex-A55/A55/A55",
    "snapdragon 678": "1x2x3x2 Cortex-A75/A55",
    "snapdragon 680": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 685": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 690": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 695": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 712": "1x2x3x2 Cortex-A75/A55",
    "snapdragon 720g": "1x2x3x2 Cortex-A76/A55",
    "snapdragon 730": "1x2x3x2 Cortex-A75/A55",
    "snapdragon 730g": "1x2x3x2 Cortex-A75/A55",
    "snapdragon 732g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 750g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 765g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 768g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 778g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 780g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 782g": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 8 gen 1": "1x3x4 Cortex-X2/A710/A710",
    "snapdragon 8 gen 2": "1x3x4 Cortex-X3/A710/A710",
    "snapdragon 8 gen 3": "1x2x3x2 Cortex-X4/A720/A720",
    "snapdragon 8 gen 4": "1x3x4 Cortex-X4/A720/A640",
    "snapdragon 8 elite": "1x3x4 Cortex-X3/A78/A55",
    "snapdragon 8 elite gen 5": "1x3x4 Cortex-X4/A78/A55",
    "snapdragon 7 gen 2": "1x3x4 Cortex-A78/A55/A55",
    "snapdragon 4+ gen 2": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 460": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 480": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 480+": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 429": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 215": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 212": "2x2 Cortex-A53/A53",
    "snapdragon 427": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 415": "2x2 Cortex-A53/A53",
    "snapdragon 616": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 650": "1x3x4 Cortex-A53/A53/A53",
    "snapdragon 652": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio a22": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio a25": "2x2 Cortex-A53/A53",
    "mediatek helio g25": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio g37": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio g88": "1x3x4 Cortex-A76/A55/A55",
    "mediatek helio g99": "1x3x4 Cortex-A76/A55/A55",
    "mediatek helio p10": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio p20": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio p22": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio p25": "1x3x4 Cortex-A53/A53/A53",
    "mediatek helio p35": "1x3x4 Cortex-A53/A53/A53",
    "mediatek dimensity 6100+": "1x3x4 Cortex-A76/A55/A55",
    "mediatek dimensity 7050": "1x3x4 Cortex-A78/A55/A55",
    "mediatek dimensity 900": "1x3x4 Cortex-A78/A55/A55",
    "mediatek dimensity 920": "1x3x4 Cortex-A78/A55/A55",
    "mediatek dimensity 930": "1x3x4 Cortex-A78/A55/A55",
    "mediatek dimensity 1080": "1x3x4 Cortex-A78/A55/A55",
    "mediatek dimensity 1200": "1x2x3x2 Cortex-A78/A55",
    "mediatek dimensity 9000": "1x3x4 Cortex-X3/A78/A55",
    "mediatek dimensity 9000+": "1x3x4 Cortex-X3/A78/A55",
    "mediatek dimensity 9200": "1x3x4 Cortex-X3/A78/A55",
    "mediatek dimensity 9300": "1x2x3x2 Cortex-X3/A720/A720",
    "mediatek dimensity 9400": "1x2x3x2 Cortex-X3/A720/A720",
    "exynos 1280": "1x3x4 Cortex-A78/A55/A55",
    "exynos 1330": "1x3x4 Cortex-A78/A55/A55",
    "exynos 1380": "1x3x4 Cortex-A78/A55/A55",
    "exynos 1480": "1x3x4 Cortex-A78/A55/A55",
    "exynos 2100": "1x3x4 Cortex-X3/A78/A55",
    "exynos 2200": "1x3x4 Cortex-X3/A78/A55",
    "exynos 2400": "1x3x4 Cortex-X3/A78/A55",
    "exynos 5430": "1x3x4 Cortex-A15/A7",
    "exynos 4210": "2x Cortex-A7",
    "google tensor": "1x3x4 Cortex-X1/A78/A55",
    "google tensor g3": "1x3x4 Cortex-X3/A715/A510",
    "google tensor g4": "1x2x3x2 Cortex-X4/A720/A720",
    "unisoc t606": "1x3x4 Cortex-A75/A55/A55",
    "unisoc t610": "1x2x3x2 Cortex-A75/A55",
    "unisoc tiger t612": "1x2x3x2 Cortex-A75/A55",
    "unisoc tiger t616": "1x2x3x2 Cortex-A75/A55",
    "unisoc sc9832": "2x2 Cortex-A53/A53",
    "intel atom z2580": "2x Cortex-A7",
    "ti omap 3630": "1x Cortex-A8",
}


def _layout_for(soc_model: str, cores: int) -> str:
    """The cluster layout of the chipset *soc_model*.

    The layout of a chipset is its own: a site that reads the layout
    reads the names of the cores out of it, so a Snapdragon says
    "Cortex-X3" and a Helio says "Cortex-A76".  A chipset the table
    does not name falls back to the layout of the count it carries.
    """

    layout = CLUSTER_BY_CHIPSET.get(soc_model.strip().lower())
    if layout is not None:
        return layout

    return CLUSTER_BY_CORES.get(cores, "1x3x4 Cortex-A78/A55/A55")

#: The density buckets the platform reports, and the device pixel ratio
#: each of them carries.  Chromium reads the ratio off the display's
#: density bucket, so the two have to agree: a 3.0 ratio on a 420 dpi
#: bucket is a combination no device ships.
DENSITY_BY_RATIO: list[tuple[float, int]] = [
    (1.0, 160),
    (1.5, 240),
    (1.75, 280),
    (2.0, 320),
    (2.25, 360),
    (2.5, 400),
    (2.625, 420),
    (2.75, 440),
    (3.0, 480),
    (3.25, 520),
    (3.5, 560),
    (4.0, 640),
]


#: The device pixel ratios the platform actually reports.  Chromium
#: reports the ratio from the display's density bucket, so the value is
#: always one of these and never an arbitrary fraction.
VALID_RATIOS: tuple[float, ...] = (1.0, 1.5, 1.75, 2.0, 2.25, 2.5, 2.625, 2.75, 3.0, 3.25, 3.5, 4.0)


def _nearest_ratio(ratio: float) -> float:
    """Snap a device pixel ratio onto one the platform reports.

    The reference catalogue carries a ratio that was rounded when it was
    written -- 2.63 for 2.625 -- and Chromium reports the value that
    belongs to the density bucket, so the ratio has to be snapped onto
    the real one before it goes anywhere.
    """

    return min(VALID_RATIOS, key=lambda candidate: abs(candidate - ratio))


#: The core counts the platform reports.  The count is not rounded and
#: not clamped -- Chromium hands the page the number of logical cores
#: the SoC has -- so the catalogue may only offer counts a handset
#: actually carries.
LEGAL_CORE_COUNTS: tuple[int, ...] = (4, 6, 8, 12)

#: The memory amounts a handset is sold with.  The amount is not
#: rounded to a power of two: a six gigabyte handset is a six gigabyte
#: handset, and Chromium's own approximation is what rounds it.
#:
#: These are the industry's sizes.  A phone with ten gigabytes exists
#: only in the one limited edition that carried it, and a phone with
#: eighteen exists in the gaming line that pushed the envelope; the set
#: of sizes is otherwise closed, because the dies come in these
#: capacities and the boards are built around them.
LEGAL_RAM: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 24)


def _nearest_core_count(cores: int) -> int:
    """Snap a core count onto one a handset actually carries."""

    return min(LEGAL_CORE_COUNTS, key=lambda candidate: abs(candidate - cores))


def _nearest_ram(ram: int) -> int:
    """Snap a memory amount onto one a handset is sold with."""

    return min(LEGAL_RAM, key=lambda candidate: abs(candidate - ram))


def density_for(ratio: float) -> int:
    """The density bucket a device pixel ratio belongs to."""

    return DENSITY_BY_RATIO[
        min(
            range(len(DENSITY_BY_RATIO)),
            key=lambda index: abs(DENSITY_BY_RATIO[index][0] - ratio),
        )
    ][1]


def codename_for(model: str) -> str:
    """The device codename of a handset.

    The catalogue carries the model code -- ``SM-S921U``, ``CPH2573``,
    ``2201116TG`` -- and the codename of the handset is the string its
    build fingerprint names.  Where the code is a short, lower-case
    token it is the codename already; where it is a long, structured
    code it is not, and the codename is derived from the model name
    instead.

    The codename has to be stable across the whole fleet of one model,
    because it is the string that goes into the fingerprint.  It also
    has to differ between two models, because the codename is one of
    the strings a site can sort a fleet on -- a fleet of two thousand
    handsets that all reported the board ``qcom`` is a fleet the site
    folds into one bucket on the strength of that one string.

    The digest is taken from the model code, so the same model always
    gets the same codename and two models never get the same one.
    """

    cleaned = model.strip()
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,20}", cleaned) and not cleaned.isdigit():
        return cleaned
    # A structured code: take the letters and digits out of the model
    # name and shorten it to something a codename looks like, then add
    # a short digest so that two handsets whose names agree do not
    # end up with the same codename.
    slug = re.sub(r"[^a-z0-9]+", "", cleaned.lower())
    if len(slug) < 3:
        slug = "dev"
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:4]
    return f"{slug[:10]}{digest}"


def _incremental_for(model_code: str) -> str:
    """The incremental build number of a handset's build.

    The number is derived from the model code with a hash that does not
    change between runs: Python's own ``hash`` is salted per process,
    and a catalogue that regenerated differently on every machine would
    be a catalogue nobody could trust.
    """

    digest = hashlib.sha256(model_code.encode("utf-8")).digest()
    return str(9000000 + int.from_bytes(digest[:4], "big") % 900000)


def _soc_model(gpu_arch: str, cores: int) -> str:
    """The SoC of a handset, named so that one SoC means one core count.

    The rule the fleet keeps is that a chipset has one core count: a
    page that reads the model, the core count and the graphics part can
    tell when they disagree, because no real chipset offers two core
    counts.  The catalogue names the graphics part and not the chipset,
    and the same graphics part ships on chipsets of different sizes, so
    the chipset name here carries the core count with it.
    """

    return f"{gpu_arch} {cores}-core"


def _chipset_for(renderer: str, cores: int, vendor: str) -> tuple[str, int]:
    """The chipset of a handset with the graphics part *renderer*.

    The answer is a chipset that really exists and that really carries
    the number of cores the row names, so that the graphics part, the
    chipset and the core count -- the three readings a site takes of a
    handset -- agree with each other.

    A graphics part that ships with more than one chipset gives a
    chipset that carries the count the row names, so that the fleet
    spreads over the real parts of the market instead of answering
    with one chipset for every handset that carries the same graphics.
    """

    from novaprofile.core.soc_cores import chipset_for

    name, count = chipset_for(renderer, cores)
    return name, count


def clean_renderer(raw: str) -> str:
    """The graphics part a catalogue row names, without the noise.

    The reference catalogue was assembled by scraping specification
    sheets, and a good number of rows carry a renderer string that two
    scrapes ran together: ``Adreno (TM) 3064002`` is the Adreno 306
    followed by a number from the next column, ``PowerVR GE8300POWERVR``
    is the GE8300 followed by the vendor word again, and
    ``Broadcom VideoCore IV / Adreno 305`` is two parts in one cell.

    A page reads the renderer as a single part name, so the fleet has to
    report a single part name too.  This picks the part out of the
    noise: the longest run that looks like a real core name wins.
    """

    text = re.sub(r"\(TM\)|\bTM\b", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # A row that names two parts separated by a slash keeps the first
    # one, which is the part the vendor leads the row with.
    if "/" in text:
        text = text.split("/", 1)[0].strip()

    # The scrapes ran two parts into one cell.  The first part is the
    # one the row means, and the rest is the beginning of the next
    # cell, so the cell is cut at the start of the second part.
    text = _cut_at_second_part(text)

    # A trailing run of digits that belongs to the next column of the
    # sheet is dropped, but only when it is long enough to be a
    # spreadsheet coordinate rather than a core model.  A model
    # number that belongs to the part is kept: the "GM9446" of the
    # PowerVR GM9446 is the number of the part, and a part whose
    # number is cut off is a part with no number at all.
    text = _split_trailing_digits(text)

    return text


#: The family names a renderer carries.
_RENDERER_FAMILIES = (
    "Adreno",
    "Mali",
    "Immortalis",
    "PowerVR",
    "Xclipse",
    "SGX",
    "IMG",
    "VideoCore",
    "Maleoon",
)


#: The gluing of two renderer names into one cell.
#:
#: The gluing is the run of a part and the run of the next one: the
#: "Adreno 3064002" is the Adreno 306 followed by the number of the
#: next column, and the "PowerVR GE8300POWERVR" is the GE8300
#: followed by the vendor word again.
#:
#: The run of digits has to be *followed* by another part, because a
#: part whose own model number is four digits long -- the PowerVR
#: GM9446, the IMG BXM-8-256 -- is one part and not two.  Reading the
#: GM9446 as the end of the GM and the start of a glued 9446 leaves
#: the renderer as the name of the vendor and no part at all.
_GLUED_RENDERER = re.compile(
    r"(?:Adreno|Mali|Immortalis|PowerVR|Xclipse|SGX|IMG|VideoCore|Maleoon)"
    r".{0,14}?\d{4,}"
    r"(?=\s*(?:Adreno|Mali|Immortalis|PowerVR|Xclipse|SGX|IMG|VideoCore|Maleoon|[A-Z]{3,}))"
)

#: A cell that names the same family of part twice.
#:
#: The scrapes ran the vendor word of the next cell into the cell
#: before it: the "PowerVR GE8300POWERVR" is the PowerVR GE8300 and
#: the beginning of the next row.  The run of the family name that
#: follows the number of the part is the start of the next cell.
_REPEATED_FAMILY = re.compile(
    r"^(?P<part>.*?(?:Adreno|Mali|Immortalis|PowerVR|Xclipse|SGX|IMG|VideoCore|Maleoon)"
    r"[\s\-][A-Za-z0-9]{1,6})"
    r"(?P<again>Adreno|Mali|Immortalis|PowerVR|Xclipse|SGX|IMG|VideoCore|Maleoon)\b"
)


def _cut_at_second_part(text: str) -> str:
    """The first part of a cell that two scrapes ran together.

    A cell that names two parts holds the part the row means and the
    beginning of the next cell.  The cut is the start of the second
    part, which is the point where the family name of a part shows up
    again.

    The family name alone is not enough to place the cut, because a
    part carries the family name in the middle of its own name -- the
    "Adreno 619" of the "Adreno 619 Adreno 619" -- and the model
    number of the first part is the run of characters between the two
    family names.  The cut is placed after that run, so the first part
    keeps its own number.

    The "PowerVR GE8300POWERVR" is a cell where the second part is
    not a part at all but the family name that begins the next cell:
    the number of the part runs straight into it, and the cut falls
    between the number and the family name.
    """

    families = "|".join(re.escape(name) for name in _RENDERER_FAMILIES)

    # Every family name of the cell, in the order they appear.  A cell
    # with two scrapes run together names the family twice: the first
    # run belongs to the part the row means, the second one begins
    # the next cell.  The second run is often in capitals, because
    # the scrapes copied the vendor word out of the header of the
    # sheet -- the "POWERVR" of the "PowerVR GE8300POWERVR".
    found = list(re.finditer(r"(?P<family>%s)" % families, text))
    if len(found) < 2:
        shouted = list(re.finditer(r"(?P<family>%s)" % families, text, re.IGNORECASE))
        if len(shouted) < 2:
            return text
        found = shouted

    first = found[0]
    second = found[1]

    # The run between the two family names is the number of the first
    # part, with the letters that some parts carry in front of it --
    # the "GE" of the PowerVR GE8300 -- and with the suffix that some
    # parts carry behind it -- the "MC6" of the Mali-G610 MC6.
    between = text[first.end() : second.start()]

    # The number of the part is the run of characters that holds a
    # digit.  Everything after the number and before the second family
    # name is the suffix of the part and is kept with it.
    number = re.search(r"[A-Za-z]*\d[A-Za-z0-9\-]*", between)
    if not number:
        return text

    cut = first.end() + number.end()

    # The suffix of the part -- the "MC6" of the Mali-G610 MC6 -- runs
    # to the end of the run between the two family names.  It is part
    # of the name of the part and is kept with it.  The suffix holds a
    # digit of its own -- the "6" of the "MC6" -- so the run of the
    # suffix is a letter run with a digit attached to it.
    suffix = re.match(r"\s*[A-Za-z]{0,3}\d{0,2}(?![A-Za-z0-9])", between[number.end() :])
    if suffix:
        cut += suffix.end()

    return text[:cut].strip()


def _split_trailing_digits(text: str) -> str:
    """The part of a renderer whose number was cut off by the gluing.

    The scrapes wrote the number of the next column right after the
    number of the part: the "Adreno 3064002" is the Adreno 306 and
    the 4002 of the next column, and the "Adreno 306" is the part.

    The number of a part is three digits and the number of the next
    column is four or more, so a run of seven digits is split between
    the two and the part keeps its own three.  A run of four digits is
    the number of a part whose own number is four digits long -- the
    "GM9446" of the PowerVR GM9446 -- and it is kept whole.
    """

    match = re.search(
        r"^(?P<head>.*?(?:Adreno|Mali|Immortalis|PowerVR|Xclipse|SGX|IMG|VideoCore|Maleoon)"
        r"(?:[\s\-][A-Za-z]{0,4})?)\s*"
        r"(?P<digits>\d{3})(?P<rest>\d+)$",
        text,
    )
    if not match:
        return text

    head = match.group("head").rstrip()
    digits = match.group("digits")
    rest = match.group("rest")

    # The number of a part is four digits at the most -- the "GM9446"
    # of the PowerVR GM9446 is four -- so a run that is longer than
    # that carries the number of the next column of the sheet with
    # it.  The part keeps three of the digits and the rest is dropped.
    if len(digits) + len(rest) > 4:
        return f"{head} {digits}".strip()

    # The run is the number of the part and is kept whole.
    return text



def gpu_facts(renderer: str) -> tuple[str, str, str, int]:
    """The vendor, the architecture, the WebGL vendor and the texture limit.

    The catalogue row names the part, and the part names its own
    vendor and its own class.  Deriving the three facts from the part
    name -- rather than from a table of the handful of parts the
    hand-written catalogue happens to cover -- is what keeps the two
    thousand expanded handsets as consistent as the hand-written ones.
    """

    from novaprofile.core.fleet_validator import _CLASS_TEXTURES, _gpu_classes

    part = renderer.strip()
    if not part:
        return ("ARM", "Mali-G57 MC2", "ARM", 16384)

    families = _gpu_classes(part)
    band = _CLASS_TEXTURES.get(
        "flagship" if "flagship" in families else ("mid" if "mid" in families else "budget"),
        (8192,),
    )
    # The Mali-G615 MC6 is the one part in the fleet that reports
    # 32768, and the Mali-G615 MC2 the one mid part that reports
    # 16384.  Everything else in the class reports the class's floor.
    limit = band[0]
    if part == "Mali-G615 MC6":
        limit = 32768
    elif part == "Mali-G615 MC2":
        limit = 16384

    if part.startswith("Adreno"):
        vendor, webgl_vendor = "Qualcomm", "Qualcomm"
    elif part.startswith("Xclipse"):
        vendor, webgl_vendor = "Samsung", "ARM"
    elif part.startswith("Immortalis") or part.startswith("Mali"):
        vendor, webgl_vendor = "ARM", "ARM"
    elif part.startswith("PowerVR") or part.startswith("SGX") or part.startswith("IMG"):
        vendor, webgl_vendor = "Imagination Technologies", "Imagination Technologies"
    elif part.startswith("VideoCore"):
        vendor, webgl_vendor = "Broadcom", "Broadcom"
    else:
        vendor, webgl_vendor = "ARM", "ARM"

    return (vendor, part, webgl_vendor, limit)


def board_for(codename: str, gpu_vendor: str) -> str:
    """The board a handset of this class carries.

    The board is the name the build fingerprint carries, and for the
    great majority of handsets it is the device codename rather than
    the SoC vendor's reference design: only the handsets whose board
    *is* the reference design -- the ones that ship with the chipset
    itself rather than a board built around it -- carry the vendor's
    name.

    The distinction matters for the fleet.  If every handset reported
    the board ``qcom``, then a site could sort two thousand handsets
    into one bucket on that one string, and the bucket would be a
    grouping key.
    """

    if gpu_vendor == "Google":
        return codename
    if gpu_vendor in ("Qualcomm", "ARM", "Imagination", "Samsung", "MediaTek"):
        # The board of a handset built around the chipset carries the
        # codename; only the reference designs carry the vendor's name.
        return codename
    return "qcom"


def slugify(text: str) -> str:
    """A preset id fragment."""

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "device"


def build(preset_id: str, row: dict[str, Any]) -> DevicePreset:
    """Build one preset from one catalogue row."""

    brand = str(row.get("brand") or "Unknown")
    name = str(row.get("name") or row.get("model") or "Unknown")
    model_code = str(row.get("model") or name)
    release = str(row.get("android") or "13")
    # The engine supports the releases from Android 13 onwards: the
    # schema carries the release and the SDK level as a closed set,
    # and a handset that has not been updated past Android 12 is not a
    # profile the product claims to be able to make.
    if release not in SDK_BY_RELEASE or int(release) < 13:
        release = "13"
    sdk = SDK_BY_RELEASE[release]

    screen = row.get("screen") or {}
    width = int(screen.get("width") or 360)
    height = int(screen.get("height") or 780)
    ratio = _nearest_ratio(float(screen.get("pixelRatio") or 3.0))

    # The catalogue carries the *logical* panel, which is what the
    # platform reports: a 360-wide logical panel on a 3.0 ratio is a
    # 1080-wide physical panel, and the screen the page reads is the
    # 360.
    density = density_for(ratio)

    ram = int(row.get("physical_ram_gb") or 4)
    cores = int(row.get("cpu_threads") or 8)
    # A catalogue row may carry a core count the platform does not
    # report -- nine and ten are marketing numbers, and sixteen is a
    # workstation.  The count the page reads is the number of logical
    # cores the SoC has, and it is one of four, six, eight or twelve.
    cores = _nearest_core_count(cores)
    ram = _nearest_ram(ram)

    renderer = clean_renderer(str(row.get("gpu_renderer") or ""))
    catalogue_vendor = str(row.get("gpu_vendor") or "").strip()
    gpu_vendor, gpu_arch, webgl_vendor, max_texture = gpu_facts(renderer)
    # The catalogue names the graphics vendor itself, and that name is
    # what the WebGL vendor string has to say.  The table above only
    # supplies the architecture and the limits.
    if catalogue_vendor:
        gpu_vendor = catalogue_vendor
    soc_vendor = SOC_VENDOR_BY_GPU_VENDOR.get(gpu_vendor, "Qualcomm")

    # The chipset of the handset, and the core count that chipset
    # carries.
    #
    # The catalogue row names the graphics part and not the chipset,
    # and the two have to agree: a page that reads the graphics part,
    # the chipset and the core count compares the three, and a
    # graphics part written next to a number -- "Mali-G57 MC2
    # 8-core" -- is not a chipset that ever shipped.  The chipset is
    # the real one that carries that graphics part, and the count is
    # the count that chipset really has.
    soc_model, cores = _chipset_for(renderer, cores, soc_vendor)
    # The vendor of the SoC is the vendor of the chipset, and the
    # chipset is the one the graphics part really ships with: a
    # Tensor sits on a Google part and an Exynos on a Samsung one,
    # whatever the graphics part of the row suggests.
    soc_vendor = vendor_for_chipset(soc_model)

    gpu = GpuProfile(
        unmasked_vendor=webgl_vendor,
        unmasked_renderer=renderer or gpu_arch,
        vendor="WebKit",
        renderer="WebKit WebGL",
        version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
        max_texture_size=max_texture,
        max_cube_map_texture_size=max_texture,
        # The renderbuffer and the viewport are bounded by the same
        # memory as the texture, so a card that samples a 32768
        # texture renders into a 32768 renderbuffer too.  A profile
        # that capped the texture alone would be a configuration no
        # driver produces.
        max_renderbuffer_size=max_texture,
        max_viewport_dims=(max_texture, max_texture),
    )

    codename = codename_for(model_code)
    board = board_for(codename, gpu_vendor)

    # The memory configurations the line was sold in.
    #
    # The amounts are the industry's: two, three, four, six, eight,
    # twelve and sixteen gigabytes.  The arithmetic that steps a base
    # up and down by two walks past them -- a line with eight gigabytes
    # would be given ten and fourteen and twenty, and none of the three
    # is a configuration that line was sold in.
    #
    # So the set is the real amounts: the one the row names, and the
    # ones that sit around it in the industry's list.  A line sold
    # with four was also sold with six, and a line sold with sixteen
    # was also sold with twelve; those are the steps the dies and the
    # boards actually come in.
    ram_options = tuple(
        sorted(
            {
                ram,
                *(
                    candidate
                    for candidate in LEGAL_RAM
                    if abs(candidate - ram) <= 4
                ),
            }
        )
    )

    # The core count the model's chipset reports.
    #
    # A chipset carries a fixed number of cores: the Snapdragon 8 Gen 1
    # is an eight-core part and every handset with it reports eight.
    # The count is a property of the chip, not a range the handset
    # picks from, so the set holds that one count.  Offering four,
    # six, eight and twelve for the same chipset put a core count on
    # the record that contradicted the SoC model read next to it, and
    # a site that compares the two sees the contradiction at once.
    cpu_options = (cores,)

    # The touch-point counts the digitiser of the line reports.
    #
    # The entry units of a line that moved to ten-point panels in its
    # second year shipped with the five-point one, and the platform
    # answered five for those.  Both are real, and both belong to the
    # line.
    touch_points_options = (5, 10)

    dpr_options = tuple(
        sorted(
            {
                ratio,
                _nearest_ratio(max(1.5, ratio - 0.25)),
                _nearest_ratio(ratio + 0.25),
            },
            reverse=True,
        )
    )
    density_options = tuple(sorted({density, 420, 480} & {d for _, d in DENSITY_BY_RATIO})) or (
        density,
    )

    release_variants: list[tuple[str, int]] = []
    for candidate in ("16", "15", "14", "13"):
        if int(candidate) <= int(release):
            release_variants.append((candidate, SDK_BY_RELEASE[candidate]))
    if not release_variants:
        release_variants = [(release, sdk)]

    return DevicePreset(
        preset_id=preset_id,
        manufacturer=brand,
        brand=brand,
        model=model_code,
        device=codename,
        product=codename,
        hardware=board,
        board=board,
        bootloader=(
            f"{codename}-1.0-"
            f"{PATCH_BY_RELEASE.get(release, '2024-01-01').replace('-', '')}"
        ),
        fingerprint_product=codename,
        gpu=gpu,
        screen=Screen(
            width=width,
            height=height,
            avail_width=width,
            avail_height=height - 48,
            avail_left=0,
            avail_top=0,
            color_depth=24,  # type: ignore[arg-type]
            pixel_depth=24,  # type: ignore[arg-type]
            device_pixel_ratio=ratio,  # type: ignore[arg-type]
            density_dpi=density,  # type: ignore[arg-type]
        ),
        abi=AndroidAbiSoc(
            supported_abis=["arm64-v8a", "armeabi-v7a"],
            supported_64_bit_abis=["arm64-v8a"],
            supported_32_bit_abis=["armeabi-v7a"],
            primary_abi="arm64-v8a",
            is_64_bit=True,
            soc_vendor=soc_vendor,  # type: ignore[arg-type]
            soc_model=soc_model,
            cpu_cluster_layout=_layout_for(soc_model, cores),
            gpu_architecture=gpu_arch,
        ),
        release=release,
        sdk_int=sdk,
        security_patch=PATCH_BY_RELEASE.get(release, "2024-01-05"),
        build_id=(
            f"{BUILD_PREFIX_BY_RELEASE.get(release, 'TQ3A')}.{release.replace('.', '')}.001"
        ),
        version_incremental=_incremental_for(model_code),
        ram_options=ram_options,
        cpu_options=cpu_options,
        dpr_options=dpr_options,
        density_options=density_options,
        year=2024 if int(release) >= 14 else 2023,
        release_variants=tuple(release_variants),
        model_variants=(model_code,),
        touch_points_options=touch_points_options,
    )


def main() -> int:
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = raw["presets"]

    presets: list[DevicePreset] = []
    seen: set[str] = set()
    for row in rows:
        model_code = str(row.get("model") or row.get("name") or "")
        if not model_code:
            continue
        base = (
            f"{slugify(str(row.get('brand') or 'unknown'))}-"
            f"{slugify(str(row.get('name') or model_code))}"
        )
        preset_id = base
        suffix = 2
        while preset_id in seen:
            preset_id = f"{base}-{suffix}"
            suffix += 1
        seen.add(preset_id)
        try:
            presets.append(build(preset_id, row))
        except Exception as exc:  # pragma: no cover - a malformed row
            print(f"skip {preset_id}: {exc}", file=sys.stderr)

    lines = [
        '"""The expanded device catalogue.',
        "",
        "This module is generated by ``tools/generate_device_presets.py`` from",
        "the reference catalogue.  Do not edit it by hand: regenerate it.",
        "",
        "Each entry is a handset that actually ships, with the panel, the",
        "memory, the core count and the graphics part taken from the",
        "reference data and the build identity derived from the model code.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from novaprofile.core.devices import (",
        "    AndroidAbiSoc,",
        "    DevicePreset,",
        "    GpuProfile,",
        "    Screen,",
        ")",
        "",
        "GENERATED_PRESETS: tuple[DevicePreset, ...] = (",
    ]
    for preset in presets:
        lines.append("    DevicePreset(")
        lines.append(f"        preset_id={preset.preset_id!r},")
        lines.append(f"        manufacturer={preset.manufacturer!r},")
        lines.append(f"        brand={preset.brand!r},")
        lines.append(f"        model={preset.model!r},")
        lines.append(f"        device={preset.device!r},")
        lines.append(f"        product={preset.product!r},")
        lines.append(f"        hardware={preset.hardware!r},")
        lines.append(f"        board={preset.board!r},")
        lines.append(f"        bootloader={preset.bootloader!r},")
        lines.append(f"        fingerprint_product={preset.fingerprint_product!r},")
        lines.append(f"        gpu={preset.gpu!r},")
        lines.append(f"        screen={preset.screen!r},")
        lines.append(f"        abi={preset.abi!r},")
        lines.append(f"        release={preset.release!r},")
        lines.append(f"        sdk_int={preset.sdk_int!r},")
        lines.append(f"        security_patch={preset.security_patch!r},")
        lines.append(f"        build_id={preset.build_id!r},")
        lines.append(f"        version_incremental={preset.version_incremental!r},")
        lines.append(f"        ram_options={preset.ram_options!r},")
        lines.append(f"        cpu_options={preset.cpu_options!r},")
        lines.append(f"        dpr_options={preset.dpr_options!r},")
        lines.append(f"        density_options={preset.density_options!r},")
        lines.append(f"        year={preset.year!r},")
        lines.append(f"        release_variants={preset.release_variants!r},")
        lines.append(f"        model_variants={preset.model_variants!r},")
        lines.append(f"        touch_points_options={preset.touch_points_options!r},")
        lines.append("    ),")
    lines.append(")")
    lines.append("")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(presets)} presets to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
