"""The graphics part each chipset really ships with.

The table of the parts in :mod:`novaprofile.core.soc_cores` is keyed by
the graphics part, because that is the direction the fleet needs most
often: a row of the catalogue names a graphics part and asks for the
chipset that carries it.

Some rows of the catalogue go the other way.  A row names the family
of the part without the part itself -- the "Adreno" of the Moto G75,
the "Mali" of the Infinix Hot 4 Pro -- and the answer has to be the
part that really ships with the chip, because a phone never reports
the family of its graphics part on its own.

This module holds that answer: the part each chipset of the market
really carries.  The pairings are the ones the parts really ship with,
and the entries are the ones the catalogue asks about.
"""

from __future__ import annotations

__all__ = [
    "GPU_BY_CHIPSET",
    "gpu_for_chipset",
    "is_generic_family",
]


#: The graphics part each chipset of the market really carries.
#:
#: The key is the name of the chipset in lower case, and the value is
#: the name of the part the way the market writes it.
GPU_BY_CHIPSET: dict[str, str] = {
    # -- Qualcomm Snapdragon, the 8 line ---------------------------
    "snapdragon 8 elite": "Adreno 750",
    "snapdragon 8 gen 4": "Adreno 750",
    "snapdragon 8s gen 4": "Adreno 730",
    "snapdragon 8 gen 3": "Adreno 740",
    "snapdragon 8+ gen 3": "Adreno 740",
    "snapdragon 8+ gen 2": "Adreno 730",
    "snapdragon 8 gen 2": "Adreno 730",
    "snapdragon 8 gen 1": "Adreno 730",
    "snapdragon 888": "Adreno 660",
    "snapdragon 888+": "Adreno 660",
    "snapdragon 870": "Adreno 650",
    "snapdragon 865+": "Adreno 650",
    "snapdragon 865": "Adreno 650",
    "snapdragon 855": "Adreno 640",
    "snapdragon 845": "Adreno 630",
    "snapdragon 835": "Adreno 630",
    "snapdragon 830": "Adreno 630",
    "snapdragon 820": "Adreno 630",
    "snapdragon 810": "Adreno 630",
    "snapdragon 800": "Adreno 330",
    "snapdragon 782g": "Adreno 642L",
    "snapdragon 780g": "Adreno 642L",
    "snapdragon 778g": "Adreno 642L",
    "snapdragon 775g": "Adreno 642L",
    "snapdragon 765g": "Adreno 619",
    "snapdragon 7+ gen 2": "Adreno 725",
    "snapdragon 7 gen 2": "Adreno 725",
    "snapdragon 7+ gen 1": "Adreno 660",
    "snapdragon 7 gen 1": "Adreno 660",
    "snapdragon 732g": "Adreno 618",
    "snapdragon 730g": "Adreno 618",
    "snapdragon 730": "Adreno 618",
    "snapdragon 720g": "Adreno 618",
    "snapdragon 712": "Adreno 610",
    "snapdragon 710": "Adreno 504",
    "snapdragon 705": "Adreno 610",
    "snapdragon 6 gen 3": "Adreno 710",
    "snapdragon 6s gen 3": "Adreno 710",
    "snapdragon 6 gen 2": "Adreno 710",
    "snapdragon 6 gen 1": "Adreno 708",
    "snapdragon 695": "Adreno 619",
    "snapdragon 690": "Adreno 690",
    "snapdragon 685": "Adreno 675",
    "snapdragon 680": "Adreno 610",
    "snapdragon 678": "Adreno 678",
    "snapdragon 675": "Adreno 620",
    "snapdragon 670": "Adreno 642",
    "snapdragon 665": "Adreno 610",
    "snapdragon 662": "Adreno 610",
    "snapdragon 660": "Adreno 512",
    "snapdragon 636": "Adreno 512",
    "snapdragon 632": "Adreno 632",
    "snapdragon 630": "Adreno 510",
    "snapdragon 625": "Adreno 506",
    "snapdragon 626": "Adreno 506",
    "snapdragon 622": "Adreno 622",
    "snapdragon 621": "Adreno 621",
    "snapdragon 616": "Adreno 616",
    "snapdragon 615": "Adreno 508",
    "snapdragon 612": "Adreno 508",
    "snapdragon 610": "Adreno 506",
    "snapdragon 600": "Adreno 505",
    "snapdragon 595": "Adreno 504",
    "snapdragon 585": "Adreno 504",
    "snapdragon 570": "Adreno 504",
    "snapdragon 480": "Adreno 506",
    "snapdragon 460": "Adreno 616",
    "snapdragon 450": "Adreno 504",
    "snapdragon 435": "Adreno 505",
    "snapdragon 430": "Adreno 305",
    "snapdragon 425": "Adreno 420",
    "snapdragon 410": "Adreno 418",
    "snapdragon 405": "Adreno 305",
    "snapdragon 400": "Adreno 305",
    "snapdragon 200": "Adreno 200",
    "snapdragon 215": "Adreno 203",
    "snapdragon 630": "Adreno 510",
    # -- MediaTek Helio, the modern lines -------------------------
    "mediatek helio p95": "IMG GM9446",
    "mediatek helio p90": "PowerVR GM9446",
    "mediatek helio p80": "Mali-G610 MC6",
    "mediatek helio p70": "Mali-G57 MC2",
    "mediatek helio p70m": "Mali-G57 MC2",
    "mediatek helio p60": "Mali-G57 MC2",
    "mediatek helio p35": "Mali-G57 MC2",
    "mediatek helio p35t": "Mali-G57 MC2",
    "mediatek helio p25": "Mali-G57 MC2",
    "mediatek helio p22t": "Mali-G57 MC2",
    "mediatek helio p22": "Mali-G57 MC2",
    "mediatek helio p20": "Mali-G57 MC2",
    "mediatek helio p10": "Mali-G20",
    "mediatek helio a25": "Mali-G51",
    "mediatek helio a22": "Mali-T720 MP2",
    "mediatek helio a20": "PowerVR GE8320",
    "mediatek helio x30": "Mali-T880",
    "mediatek helio x25": "Mali-T880",
    "mediatek helio x23": "Mali-G57 MC2",
    "mediatek helio x20": "Mali-T760",
    "mediatek helio x10": "Mali-T720 MP2",
    "mediatek helio g99": "Mali-G57 MC2",
    "mediatek helio g96": "Mali-G57 MC2",
    "mediatek helio g88": "Mali-G52 MC2",
    "mediatek helio g85": "Mali-G52 MC2",
    "mediatek helio g80": "Mali-G57 MC2",
    "mediatek helio g70": "Mali-G57 MC2",
    "mediatek helio g37": "Mali-G57 MC2",
    "mediatek helio g36": "Mali-G57 MC2",
    "mediatek helio g35": "Mali-G57 MC2",
    "mediatek helio g25": "Mali-G52 MC2",
    "mediatek helio g37 ": "Mali-G57 MC2",
    # -- MediaTek Dimensity ---------------------------------------
    "mediatek dimensity 9400": "Immortalis-G925 MC12",
    "mediatek dimensity 9300": "Immortalis-G720 MC10",
    "mediatek dimensity 9200": "Mali-G715 MC11",
    "mediatek dimensity 9000+": "Immortalis-G710 MC10",
    "mediatek dimensity 9000": "Mali-G710 MC8",
    "mediatek dimensity 8200": "Mali-G610 MC6",
    "mediatek dimensity 8100": "Mali-G610 MC6",
    "mediatek dimensity 8000": "Mali-G610 MC6",
    "mediatek dimensity 800": "Mali-G610 MC6",
    "mediatek dimensity 7050": "Mali-G610 MC6",
    "mediatek dimensity 7025": "Mali-G57 MC2",
    "mediatek dimensity 7020": "Mali-G57 MC2",
    "mediatek dimensity 6400": "Mali-G57 MC2",
    "mediatek dimensity 6300": "Mali-G57 MC2",
    "mediatek dimensity 6100+": "Mali-G610 MC2",
    "mediatek dimensity 6080": "Mali-G57 MC2",
    "mediatek dimensity 6020": "Mali-G57 MC2",
    "mediatek dimensity 1300": "Mali-G57 MC2",
    "mediatek dimensity 1200": "Mali-G68 MC6",
    "mediatek dimensity 1100": "Mali-G57 MC2",
    "mediatek dimensity 1080": "Mali-G68 MP4",
    "mediatek dimensity 930": "Mali-G615 MC6",
    "mediatek dimensity 900": "Mali-G615 MC2",
    "mediatek dimensity 830": "Mali-G57 MC2",
    "mediatek dimensity 820": "Mali-G57 MC2",
    "mediatek dimensity 810": "Mali-G57 MC2",
    "mediatek dimensity 800": "Mali-G57 MC2",
    "mediatek dimensity 720": "Mali-G57 MC2",
    "mediatek dimensity 700": "Mali-G57 MC2",
    "mediatek dimensity 600": "Mali-G57 MC2",
    # -- The MediaTek of the older lines --------------------------
    "mediatek mt6737w": "Mali-T720 MP2",
    "mediatek mt6737t": "Mali-T720 MP2",
    "mediatek mt6737": "Mali-T720 MP2",
    "mediatek mt6735": "Mali-T720",
    "mediatek mt6739wa": "PowerVR GE8100",
    "mediatek mt6739ww": "PowerVR GE8100",
    "mediatek mt6739t": "PowerVR GE8100",
    "mediatek mt6739": "PowerVR GE8100",
    "mediatek mt6789": "Mali-G57 MC2",
    "mediatek mt6797": "Mali-G72",
    "mediatek mt6795": "Mali-T880",
    "mediatek mt6853": "Mali-G57 MC2",
    "mediatek mt6855": "Mali-G57 MC2",
    "mediatek mt6753": "Mali-T720 MP2",
    "mediatek mt6755": "Mali-T760",
    "mediatek mt6757": "Mali-T760",
    "mediatek mt6589t": "PowerVR SGX544MP2",
    "mediatek mt6589": "PowerVR SGX544",
    "mediatek mt6582": "Mali-T720 MP2",
    "mediatek mt6580": "Mali-T720 MP2",
    "mediatek mt6577": "PowerVR SGX531u",
    "mediatek mt6575": "Mali-T720 MP2",
    "mediatek mt6517": "PowerVR SGX531u",
    "mediatek mt6515": "PowerVR SGX531",
    "mediatek mt5939": "Mali-400",
    "mediatek mt5925": "Mali-400",
    # -- Samsung Exynos --------------------------------------------
    "samsung exynos 2500": "Xclipse 950",
    "samsung exynos 2400": "Xclipse 920",
    "samsung exynos 2200": "Xclipse 920",
    "samsung exynos 2100": "Mali-G78 MP20",
    "samsung exynos 2000": "Mali-G68 MP5",
    "samsung exynos 1480": "Mali-G68 MP5",
    "samsung exynos 1380": "Xclipse 550",
    "samsung exynos 1330": "AMD Radeon 610M",
    "samsung exynos 1280": "Xclipse 530",
    "samsung exynos 9900": "Mali-G77 MP11",
    "samsung exynos 9825": "Mali-G76 MC12",
    "samsung exynos 9820": "Mali-G76 MC12",
    "samsung exynos 9810": "Mali-G72 MP12",
    "samsung exynos 9610": "Mali-G72 MP12",
    "samsung exynos 9609": "Mali-G71 MP12",
    "samsung exynos 8890": "Mali-T880 MP12",
    "samsung exynos 8895": "Mali-T889 MP12",
    "samsung exynos 8830": "Mali-T880 MP12",
    "samsung exynos 7870": "Mali-T604 MP2",
    "samsung exynos 7420": "Mali-T760 MP4",
    "samsung exynos 5430": "Mali-T880 MP6",
    "samsung exynos 5410": "Mali-T720 MP4",
    "samsung exynos 5260": "Mali-T624",
    "samsung exynos 5210": "Mali-T624 MP2",
    "samsung exynos 4412": "Mali-T604",
    "samsung exynos 4210": "Mali-T604 MP2",
    "samsung exynos 4410": "Mali-T604",
    # -- Google Tensor ---------------------------------------------
    "google tensor g5": "Mali-G715 MC10",
    "google tensor g4": "Mali-G715 MC9",
    "google tensor g3": "Mali-G78 MP20",
    "google tensor g2": "Mali-G78 MP20",
    "google tensor g1": "Mali-G78 MP20",
    "google tensor": "Mali-G78 MP20",
    # -- HiSilicon Kirin -------------------------------------------
    "hisilicon kirin 9000e": "Maleoon 910",
    "hisilicon kirin 9000": "Maleoon 910",
    "hisilicon kirin 990e": "Mali-G76 MP16",
    "hisilicon kirin 990": "Mali-G76 MP16",
    "hisilicon kirin 985": "Mali-G76 MP16",
    "hisilicon kirin 980": "Mali-G76 MP16",
    "hisilicon kirin 970": "Mali-G72 MP12",
    "hisilicon kirin 960": "Mali-G71 MP8",
    "hisilicon kirin 820": "Mali-G78 MC10",
    "hisilicon kirin 810": "Mali-G76 MC4",
    "hisilicon kirin 710": "Mali-G51 MP4",
    "hisilicon kirin 659": "Mali-T720 MP2",
    "hisilicon kirin 650": "Mali-T720 MP2",
    "hisilicon kirin 620": "Mali-T624 MP2",
    # -- Unisoc / Spreadtrum ---------------------------------------
    "unisoc t760": "Mali-G57 MC2",
    "unisoc t740": "Mali-G57 MC2",
    "unisoc t700": "Mali-G57 MC2",
    "unisoc t620": "Mali-G57 MC2",
    "unisoc t618": "Mali-G52 MC2",
    "unisoc t615": "Mali-G57 MC2",
    "unisoc t606": "Mali-G57 MP2",
    "unisoc t310": "PowerVR IMG AXS",
    "unisoc sc9863a": "Mali-T720",
    "unisoc sc9832e": "Mali-T720",
    "unisoc sc9832": "Mali-T720",
    "unisoc sc9863": "Mali-T720",
    "unisoc sc9610": "Mali-T720",
    # -- The parts that were never Qualcomm ------------------------
    "nvidia tegra x1": "Nvidia Maxwell",
    "nvidia tegra k1": "Kepler DX1",
    "nvidia tegra 3": "ULP GeForce",
    "nvidia tegra 2": "ULP GeForce",
    "ti omap 4430": "PowerVR SGX540",
    "ti omap 3630": "PowerVR SGX530",
    "ti omap 3640": "PowerVR SGX530",
    "ti omap 3620": "PowerVR SGX530",
    "ti omap 3430": "PowerVR SGX530",
    "intel atom z3745": "Intel HD Graphics",
    "intel atom z3580": "PowerVR SGX544",
    "intel atom z2560": "PowerVR SGX544MP2",
    "intel atom z2580": "PowerVR SGX544MP2",
    "intel atom z2460": "PowerVR SGX540",
    "broadcom bc28155": "Broadcom VideoCore IV",
    "marvell pxa986": "Vivante GC1000",
    "marvell pxa1088": "Vivante GC1000",
}


#: The names of the families that stand for a part without being one.
#:
#: A renderer that carries nothing but the name of a family is a row
#: of a specification sheet that was never filled in: the "Adreno" of
#: the Moto G75, the "Mali" of the Infinix Hot 4 Pro.  A phone never
#: reports the family of its graphics part on its own.
_GENERIC_FAMILIES = frozenset(
    {
        "adreno",
        "mali",
        "immortalis",
        "maleoon",
        "xclipse",
        "powervr",
        "img",
        "sgx",
        "geforce",
        "videocore",
    }
)

#: The vendor words that stand in front of the name of a family.
_VENDOR_WORDS = (
    "google inc.",
    "imagination",
    "qualcomm",
    "nvidia",
    "samsung",
    "intel",
    "arm",
)


def is_generic_family(renderer: str) -> bool:
    """The renderer names the family of a part but not the part.

    The answer is true for a renderer that carries nothing but the
    name of a family, and false for a renderer that names a real
    part.
    """

    text = (renderer or "").strip().lower()
    if not text:
        return False

    # A renderer that carries a number names a part.
    if any(character.isdigit() for character in text):
        return False

    # The vendor word in front of the family is not a part either.
    for vendor in _VENDOR_WORDS:
        if text.startswith(vendor + " "):
            text = text[len(vendor) + 1 :].strip()
            break

    return text in _GENERIC_FAMILIES


def gpu_for_chipset(chipset: str) -> str:
    """The graphics part the chipset really carries.

    The answer is the name of the part the market writes, and an empty
    answer means the table does not name the chipset -- the caller
    then keeps the part the row named.
    """

    name = (chipset or "").strip().lower()
    if not name:
        return ""

    part = GPU_BY_CHIPSET.get(name)
    if part:
        return part

    # The chipset may be named with the vendor in front of it, or with
    # the process node behind it: the "Qualcomm Snapdragon 6 Gen 3
    # (4 nm)" of the catalogue is the "Snapdragon 6 Gen 3" of the
    # table.
    for chipset_name, part in GPU_BY_CHIPSET.items():
        if name.endswith(chipset_name) or chipset_name.endswith(name):
            return part

    return ""
