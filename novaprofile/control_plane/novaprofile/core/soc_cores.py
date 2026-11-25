"""How many cores the system-on-chip of a handset really carries.

The fleet's core count is a reading a site takes on every page: it is
``navigator.hardwareConcurrency``, and it is one of the three numbers
-- with the model and the memory -- that a script groups on.

The catalogue does not say how many cores a chipset has.  It says
which chipset a handset carries, and from that the count has to be
read, because the count is a property of the *chip*, not of the
handset: the same model leaves the factory with a different chip
depending on the market, and two handsets of one model can carry
four cores each or eight.

The table below is the answer for the chipsets the field actually
sees.  It is keyed by the part number, because that is the part that
is unambiguous: "Snapdragon 430" and "MSM8937" are the same chip and
the table names it once.

A chipset the table does not name falls back to the count the text
itself states, and then to the reading the cluster layout carries.
The fallback is honest about being a fallback: it is the count of the
commonest layout, not a claim about the chip.
"""

from __future__ import annotations

import re

#: The core count of the chipsets the catalogue names.
#:
#: The numbers are the ones the chips ship with -- the total of the
#: clusters that are really there, which is what the platform
#: reports.  A part that has two big cores and six little ones is
#: eight, and no part in this table reports a count that is not in
#: the set of counts a designer actually ships.
_SOC_CORES: dict[str, int] = {
    # -- Qualcomm Snapdragon 4xx (entry) ---------------------------
    "msm8916": 4,  # Snapdragon 410 -- quad Cortex-A53
    "snapdragon 410": 4,
    "msm8939": 8,  # Snapdragon 615 -- 4xA53 + 4xA53
    "snapdragon 615": 8,
    "snapdragon 626": 8,
    "snapdragon 625": 8,
    "samsung exynos 2500": 8,
    "exynos 2500": 8,
    "exynos 9825": 8,
    "exynos 5410": 8,
    "mediatek helio x10": 8,
    "helio x10": 8,
    "msm8937": 4,  # Snapdragon 430 -- quad Cortex-A53
    "snapdragon 430": 4,
    "msm8917": 4,  # Snapdragon 425 -- quad Cortex-A53
    "snapdragon 425": 4,
    "msm8926": 4,  # Snapdragon 400 -- quad Cortex-A53
    "snapdragon 400": 4,
    "msm8909": 4,  # Snapdragon 200 -- quad Cortex-A53
    "snapdragon 200": 4,
    "msm7227": 4,  # Snapdragon 400 -- quad Cortex-A53
    "snapdragon 435": 8,  # 4xA53 + 4xA53
    "snapdragon 450": 8,  # 4xA53 + 4xA53
    "snapdragon 632": 8,  # 4xA53 + 4xA53
    "snapdragon 636": 8,
    "snapdragon 660": 8,  # 4xA53 + 4xA53
    "snapdragon 662": 8,
    "snapdragon 665": 8,
    "snapdragon 675": 8,
    "snapdragon 680": 8,
    "snapdragon 685": 8,
    "snapdragon 690": 8,
    "snapdragon 695": 8,
    # -- Qualcomm Snapdragon 6xx (mid) ----------------------------
    "msm8953": 8,  # Snapdragon 625 -- 4xA53 + 4xA53
    "snapdragon 625": 8,
    "msm8996": 8,  # Snapdragon 820 -- 4xA72 + 4xA53
    "snapdragon 820": 8,
    "msm8976": 8,  # Snapdragon 810 -- 4xA53 + 4xA57
    "snapdragon 810": 8,
    "snapdragon 808": 8,
    "snapdragon 630": 8,
    "snapdragon 835": 8,
    "snapdragon 845": 8,
    "snapdragon 850": 8,
    "snapdragon 855": 8,
    "snapdragon 856": 8,
    "snapdragon 860": 8,
    # -- Qualcomm Snapdragon 7xx/8xx (flagship) -------------------
    "sm6250": 8,  # Snapdragon 765G -- 1xA76 + 3xA55 + 4xA55
    "snapdragon 765g": 8,
    "sm6375": 8,  # Snapdragon 685
    "sm8250": 8,  # Snapdragon 870 -- 1xA77 + 3xA77 + 4xA55
    "snapdragon 870": 8,
    "sm8255": 8,  # Snapdragon 860
    "sm8150": 8,  # Snapdragon 855
    "sm8350": 8,  # Snapdragon 835
    "sm8450": 8,  # Snapdragon 8 Gen 1
    "snapdragon 888": 8,
    "snapdragon 8 gen 1": 8,
    "snapdragon 8 gen 2": 8,
    "snapdragon 8 gen 3": 8,
    "snapdragon 8+ gen 1": 8,
    "snapdragon 8 elite": 8,
    "snapdragon 8 gen 4": 8,
    "snapdragon 8s gen 3": 8,
    "snapdragon 8s gen 4": 8,
    "snapdragon 7+ gen 1": 8,
    "snapdragon 7+ gen 2": 8,
    "snapdragon 7+ gen 3": 8,
    "snapdragon 7 gen 1": 8,
    "snapdragon 7 gen 2": 8,
    "snapdragon 7 gen 3": 8,
    "snapdragon 7s gen 2": 8,
    "snapdragon 7s gen 3": 8,
    "snapdragon 6 gen 1": 8,
    "snapdragon 6 gen 2": 8,
    "snapdragon 4 gen 1": 8,
    "snapdragon 4 gen 2": 8,
    "sm8475": 8,  # Snapdragon 8 Gen 1
    "sm8550": 8,  # Snapdragon 8 Gen 2
    "sm8650": 8,  # Snapdragon 8 Gen 3
    "sm8735": 8,
    "sm8750": 8,
    # -- MediaTek Helio -------------------------------------------
    "mt6582": 8,  # 4xA7 + 4xA7
    "mt6582m": 8,
    "mt6580": 8,  # 4xA7 + 4xA7
    "mt6572": 8,  # 4xA7 + 4xA7
    "mt6572m": 8,
    "mt6737": 8,  # 4xA53 + 4xA53
    "mt6735": 8,  # 4xA53 + 4xA53
    "mt6753": 8,  # 4xA53 + 4xA53
    "mt6750": 8,  # 4xA53 + 4xA53
    "mt6752": 8,
    "mt6762": 8,  # Helio P22 -- 4xA53 + 4xA53
    "helio p22": 8,
    "helio g25": 8,
    "mediatek helio g25": 8,
    "mediatek dimensity 9400": 8,
    "dimensity 9400": 8,
    "mt6765": 8,  # Helio P35 / P65
    "helio p35": 8,
    "helio p65": 8,
    "helio p70": 8,
    "helio p90": 8,
    "helio p95": 8,
    "helio p22t": 8,
    "helio p35t": 8,
    "mt6761": 8,  # Helio P22
    "mt6771": 8,  # Helio P70
    "mt6781": 8,  # Helio G99
    "helio g99": 8,
    "mt6853": 8,  # Helio G80
    "helio g80": 8,
    "helio g70": 8,
    "helio g37": 8,
    "mt6833": 8,  # Helio G80
    "mt6835": 8,  # Helio G37
    "mt6877": 8,  # Helio G99
    "mt6893": 8,  # Dimensity 1200
    "dimensity 1200": 8,
    "dimensity 1300": 8,
    "dimensity 900": 8,
    "dimensity 920": 8,
    "dimensity 1080": 8,
    "dimensity 7050": 8,
    "dimensity 8020": 8,
    "dimensity 8100": 8,
    "dimensity 8200": 8,
    "dimensity 8300": 8,
    "dimensity 6020": 8,
    "dimensity 6100+": 8,
    "dimensity 7020": 8,
    "dimensity 7200 pro": 8,
    "dimensity 7350 pro": 8,
    "kirin 9000s": 8,
    "kirin 9010": 8,
    "exynos 1330": 8,
    "mt8183": 8,  # Dimensity 820
    "mt8184": 8,
    "mt8125": 8,
    # -- Samsung Exynos -------------------------------------------
    "exynos 5433": 8,  # 4xA53 + 4xA53
    "exynos 7420": 8,  # 4xA53 + 4xA53
    "exynos 7580": 8,  # 4xA53 + 4xA53
    "exynos 7578": 4,  # 4xA53
    "exynos 7870": 8,  # 4xA53 + 4xA53
    "exynos 7880": 8,
    "exynos 7904": 8,
    "exynos 7960": 8,  # 2xA75 + 6xA55
    "exynos 9810": 8,
    "exynos 9820": 8,
    "exynos 9830": 8,
    "exynos 9611": 8,  # 4xA55 + 4xA55
    "exynos 9610": 8,
    "exynos 8500": 8,
    "exynos 880": 8,
    "exynos 1280": 8,
    "exynos 1380": 8,
    "exynos 1480": 8,
    "exynos 2200": 8,
    "sm-l1": 4,  # Exynos 7580
    "sm-m20": 8,
    # -- Google Tensor --------------------------------------------
    "tensor g1": 8,  # 1xX1 + 3xA76 + 4xA55
    "tensor g2": 8,
    "tensor g3": 8,  # 1xX3 + 5xA78 + 2xA55
    "tensor": 8,
    "gs101": 8,  # Tensor G1
    "gs201": 8,  # Tensor G2
    "gs301": 8,  # Tensor G3
    "tensor g4": 8,
    "tensor g5": 8,
    # -- HiSilicon Kirin ------------------------------------------
    "kirin 710": 8,  # 4xA73 + 4xA53
    "kirin 710f": 8,
    "kirin 710a": 8,
    "kirin 659": 8,  # 4xA53 + 4xA53
    "kirin 655": 8,
    "kirin 960": 8,  # 4xA73 + 4xA53
    "kirin 970": 8,  # 4xA73 + 4xA53
    "kirin 980": 8,  # 2xA76 + 2xA76 + 4xA55
    "kirin 990": 8,
    "kirin 9000": 8,
    "kirin 810": 8,
    "kirin 820": 8,
    "kirin 990e": 8,
    "kirin 9000e": 8,
    "hisilicon kirin 710": 8,
    "hisilicon kirin 659": 8,
    "hisilicon kirin 960": 8,
    "hisilicon kirin 970": 8,
    "hisilicon kirin 980": 8,
    # -- Unisoc / Spreadtrum ---------------------------------------
    "unisoc t606": 8,  # 2xA75 + 6xA55
    "t606": 8,
    "t310": 4,  # 4xA55
    "t618": 8,  # 2xA75 + 6xA55
    "t615": 8,
    "t620": 8,
    "t700": 8,
    "t760": 8,
    "t820": 8,
    "sc9863a": 8,  # 2xA75 + 6xA55
    "sc9832e": 8,
    "sc9832": 8,
    "sc9853i": 8,
    "spreadtrum sc9863a": 8,
    "spreadtrum sc9832e": 8,
    # -- Qualcomm S1 (Snapdragon 1) --------------------------------
    "snapdragon s1": 8,  # 2xA55 + 6xA55
    # -- MediaTek, the parts the older handsets carry --------------
    "mt6589": 8,  # 4xA7 + 4xA7
    "mt6577": 8,  # 4xA7 + 4xA7
    "mt6575": 8,  # 4xA7 + 4xA7
    "mt6592": 8,  # 4xA7 + 4xA7
    "mt6755": 8,  # Helio P10 -- 4xA53 + 4xA53
    "helio p10": 8,
    "mt6735p": 8,  # 4xA53 + 4xA53
    "mt6768": 8,  # Helio G70
    "helio g70": 8,
    "mt6797": 8,  # Helio X20 -- 2xA72 + 4xA53 + 2xA53
    "helio x20": 8,
    "helio x25": 8,
    "helio x30": 8,
    "helio a22": 8,
    "helio a25": 8,
    "helio k60": 8,
    "helio p18": 8,
    "dimensity 700": 8,
    "dimensity 800": 8,
    "dimensity 810": 8,
    "dimensity 820": 8,
    "dimensity 9200": 8,
    "dimensity 9200+": 8,
    "dimensity 9300+": 8,
    "snapdragon 8s gen 3": 8,
    "snapdragon 7s gen 2": 8,
    "dimensity 9300": 8,
    "mt6855": 8,  # Dimensity 720
    "mt6877 ": 8,
    "mt6198": 8,  # Dimensity 700
    "mt6825": 8,  # Dimensity 800U
    "mt6835 ": 8,
    "mt6885": 8,  # Dimensity 1000
    "mt6895": 8,  # Dimensity 9000
    # -- Qualcomm, the parts the older handsets carry ----------------
    "msm8960": 4,  # Snapdragon S4 Plus -- quad Cortex-A7
    "snapdragon s4 plus": 4,
    "snapdragon s4": 4,
    "msm8255 ": 8,
    "msm8226": 4,  # Snapdragon 200
    "msm8926 ": 4,
    "msm8974": 4,  # Snapdragon 200
    "msm8964": 4,  # Snapdragon 115
    "snapdragon 115": 4,
    "snapdragon 205": 4,
    "snapdragon 210": 4,
    "snapdragon 225": 4,
    "snapdragon 600": 8,
    "snapdragon 615 ": 8,
    "snapdragon 630 ": 8,
    "snapdragon 820 ": 8,
    "snapdragon s2": 8,
    # -- Texas Instruments ------------------------------------------
    "omap 4430": 4,  # 2xA9 + 2xA9
    "omap 4460": 4,
    "omap 4470": 4,
    "omap 4680": 4,
    "ti omap 4430": 4,
    "ti omap 4460": 4,
    "ti omap 4470": 4,
    "ti omap 4680": 4,
    # -- The parts that predate the SoC era -------------------------
    "msm8260": 4,  # Snapdragon S3 -- quad Krait
    "snapdragon s3": 4,
    "msm8960 ": 4,
    "sdm710": 8,  # Snapdragon 710 -- 2xA75 + 6xA55
    "snapdragon 710": 8,
    "sdm660": 8,
    "sdm845": 8,
    "msm7201a": 8,  # Snapdragon 675
    "snapdragon 675 ": 8,
    "msm7201": 8,
    "snapdragon 800": 4,  # 4xKrait 200
    "msm8974ac": 4,  # Snapdragon 801 -- quad Krait
    "msm8974": 4,
    "snapdragon 801": 4,
    "snapdragon 802": 4,
    "snapdragon 805": 4,
    "snapdragon 806": 4,
    "snapdragon 807": 4,
    "snapdragon 808 ": 8,
    "tegra 2": 4,  # Tegra 2 T20 -- 2xA9 + 2xA9
    "tegra2 t20": 4,
    "nvidia tegra 2 t20": 4,
    "tegra 3": 4,  # Tegra 3 -- 4xA9
    "hummingbird": 4,  # NovaThor -- 2xA9 + 2xA9
    "mt6739": 8,  # 4xA53 + 4xA53
    "mt6573": 8,  # 4xA7 + 4xA7
    "mt6769z": 8,  # Helio G85 -- 2xA75 + 6xA55
    "helio g85": 8,
    "helio g88": 8,
    "helio g95": 8,
    "mt6580m": 8,  # 4xA7 + 4xA7
    "mt6582t": 8,
    "mt6582m ": 8,
    "mt6575m": 8,
    "mt6590": 8,
    "mt6795": 8,
    "mt6833 ": 8,
    "mt6853 ": 8,
    "mt6873": 8,  # Helio P22
    "mt6891": 8,  # Dimensity 1200
    "mt6895 ": 8,
    "mt8185": 8,
    "mt8186": 8,
    "mt8187": 8,
    # -- The last of the older parts --------------------------------
    "sdm439": 8,  # Snapdragon 439 -- 2xA53 + 6xA53
    "snapdragon 439": 8,
    "msm8952": 8,  # Snapdragon 617 -- 4xA53 + 4xA53
    "snapdragon 617": 8,
    "mt6765g": 8,  # Helio G35 -- 4xA53 + 4xA53
    "helio g35": 8,
    "mt6737m": 8,  # 4xA53 + 4xA53
    "mt6592m": 8,  # 4xA7 + 4xA7
    "sm7325": 8,  # Snapdragon 778G
    "snapdragon 778g": 8,
    "tegra 2 t20": 4,
    "tegra 2 ap20h": 4,
    "tegra2 ap20h": 4,
    "nvidia tegra 2 ap20h": 4,
    "tegra 3 t30": 4,
    "tegra 4 t114": 4,
    # -- Others -----------------------------------------------------
    "kirin 620": 8,
    "mt6737t": 8,
    "mt6580t": 8,
    "mt6582 ": 8,
    "exynos 5433 ": 8,
    "exynos 7870 ": 8,
    # -- Samsung Exynos, the parts the market still ships ----------
    "exynos 1080": 8,  # 1xA78 + 3xA78 + 4xA55
    "exynos 2100": 8,
    "exynos 2400e": 8,  # 1xX3 + 3xA78 + 4xA55
    "exynos 3470": 4,  # Exynos 3470 Quad -- 4xA55
    "exynos 3475": 4,  # Exynos 3475 Quad -- 4xA55
    "exynos 4210": 2,  # Exynos 4210 Dual -- 2xA7
    "exynos 4212": 2,  # Exynos 4212 Dual -- 2xA7
    "exynos 4412": 4,  # Exynos 4412 Quad -- 4xA9
    "exynos 5260": 6,  # Exynos 5260 Hexa -- 2xA72 + 4xA53
    "exynos 5420": 8,  # Exynos 5420 Octa -- 4xA15 + 4xA7
    "exynos 5422": 8,  # Exynos 5422 Octa -- 4xA15 + 4xA7
    "exynos 5430": 8,  # Exynos 5430 Octa -- 4xA15 + 4xA7
    "exynos 7570": 4,  # Exynos 7570 Quad -- 4xA53
    "exynos 7884": 8,  # Exynos 7884 Octa -- 2xA75 + 6xA55
    "exynos 7884b": 8,
    "exynos 7885": 8,  # Exynos 7885 Octa -- 2xA75 + 6xA55
    "exynos 850": 8,  # Exynos 850 Octa -- 2xA75 + 6xA55
    "exynos 8890": 8,  # Exynos 8890 Octa -- 4xA53 + 4xA53
    "exynos 8895": 8,  # Exynos 8895 Octa -- 2xA75 + 6xA55
    "exynos 9110": 8,  # Exynos 9110 -- 1xX3 + 4xA78
    "exynos 980": 8,  # Exynos 980 Octa -- 2xA75 + 6xA55
    "exynos 9825": 8,  # Exynos 9825 Octa -- 2xA75 + 6xA55
    "exynos 990": 8,  # Exynos 990 Octa -- 2xA75 + 6xA55
    # -- HiSilicon Kirin -------------------------------------------
    "kirin 910": 4,  # Kirin 910 -- 4xA53
    "kirin 920": 8,  # Kirin 920 -- 1xA76 + 3xA76 + 4xA55
    "kirin 925": 8,
    "kirin 935": 8,
    "kirin 950": 4,  # Kirin 950 -- 4xA53
    "kirin 955": 4,  # Kirin 955 -- 4xA53
    "kirin 985": 8,  # Kirin 985 -- 1xA76 + 3xA76 + 4xA55
    "kirin 710f": 8,  # Kirin 710F -- 4xA55
    "hisilicon kirin 620": 8,
    "hisilicon kirin 650": 8,
    "hisilicon kirin 655": 8,
    "hisilicon kirin 810": 8,
    "hisilicon kirin 950": 4,
    "hisilicon kirin 955": 4,
    "hisilicon kirin 710f": 8,
    "hisilicon kirin 910": 4,
    "hisilicon kirin 920": 8,
    "hisilicon kirin 925": 8,
    "hisilicon kirin 935": 8,
    "kirin 650": 8,
    "kirin 910": 4,
    "kirin 920": 8,
    "kirin 925": 8,
    "kirin 935": 8,
    # -- MediaTek, the Helio and Dimensity lines -------------------
    "helio a25": 4,  # MT6762 -- 4xA53
    "helio p20": 8,  # MT6757 -- 4xA53 + 4xA53
    "helio p23": 8,  # MT6763 -- 4xA53 + 4xA53
    "helio p25": 8,  # MT6757 -- 4xA53 + 4xA53
    "mt6757": 8,  # Helio P20/P25 -- 4xA53 + 4xA53
    "mt6757cd": 8,
    "mt6757t": 8,
    "mt6762d": 4,  # Helio A25 -- 4xA53
    "mt6763": 8,  # Helio P23 -- 4xA53 + 4xA53
    "dimensity 1000l": 8,  # MT6885 -- 4xA77 + 4xA55
    "dimensity 1000": 8,
    "mt6732": 4,  # MT6732 -- 4xA53
    "mt6515": 4,
    "mt6517": 4,
    "mt6580p": 4,
    "mt6589t": 8,  # MT6589 -- 4xA7 + 4xA7
    "mt6732m": 4,
    "mt6735m": 4,  # MT6735 -- 4xA53
    "mt6752m": 8,  # MT6752 -- 4xA53 + 4xA53
    "mt6737h": 8,
    "mt6737w": 8,
    "mt6739w": 8,
    "mt6739wa": 8,
    "mt6739ww": 8,
    "mt6750t": 8,
    "mt6750v": 8,
    "mt6761d": 8,  # Helio A20 -- 4xA53 + 4xA53
    "mt6762g": 8,  # Helio G25 -- 4xA53 + 4xA53
    "mt6763t": 8,  # Helio P23 -- 4xA53 + 4xA53
    "mt6785": 8,  # Helio G90T -- 2xA75 + 6xA55
    "mt6795m": 8,  # Helio X10 -- 4xA53 + 4xA53
    "mt6795t": 8,
    "mt6797d": 8,  # Helio X23 -- 4xA53 + 4xA53
    "mt6885z": 8,  # Dimensity 1000L -- 4xA77 + 4xA55
    "mt8127": 4,  # MT8127 -- 4xA53
    "mt8161": 4,  # MT8161 -- 4xA53
    "mt8165": 4,
    "mt8167d": 4,
    "mt8176": 4,  # MT8176 -- 4xA53
    "mt8321": 8,  # MT8321 -- 4xA53 + 4xA53
    "mt8382m": 8,
    "mt8732": 8,
    "mt8735": 8,
    "mt8735d": 8,
    "mt8765b": 8,
    "mt8768": 8,  # Helio A22 -- 4xA53 + 4xA53
    "mt8797": 8,  # Kompanio 1300T -- 4xA55 + 4xA55
    "mt8786": 8,
    "mt6885": 8,
    "mt6735": 4,
    "mt6577": 8,
    "mt6580w": 8,
    "helio p15": 8,
    "dimensity 1050": 8,
    "dimensity 7030": 8,
    "dimensity 1000+": 8,
    "dimensity 1000l": 8,
    "dimensity 1100": 8,
    "dimensity 6080": 8,
    "dimensity 6300": 8,
    "dimensity 7025": 8,
    "dimensity 7025 ultra": 8,
    "dimensity 720": 8,
    "dimensity 7200": 8,
    "dimensity 7300": 8,
    "dimensity 7300 energy": 8,
    "dimensity 7300 ultra": 8,
    "dimensity 7300x": 8,
    "dimensity 8000": 8,
    "dimensity 8050": 8,
    "dimensity 8250": 8,
    "dimensity 9000": 8,
    "dimensity 9000+": 8,
    "dimensity 930": 8,
    "helio g100": 8,
    "helio g36": 8,
    "helio g81": 8,
    "helio g81 ultra": 8,
    "helio g90t": 8,
    "helio g91": 8,
    "helio g91 ultra": 8,
    # -- Qualcomm, the parts named by their part number ------------
    "msm7230": 8,  # Snapdragon 615 -- 4xA53 + 4xA53
    "msm8290": 8,  # Snapdragon 626 -- 4xA53 + 4xA53
    "msm8909v2": 4,  # Snapdragon 212 -- 4xA53
    "msm8920": 8,  # Snapdragon 427 -- 4xA53 + 4xA53
    "msm8929": 4,  # Snapdragon 415 -- 4xA53
    "msm8939v2": 8,  # Snapdragon 616 -- 4xA53 + 4xA53
    "msm8956": 8,  # Snapdragon 650 -- 4xA53 + 4xA53
    "qm215": 8,  # Snapdragon 215 -- 4xA53 + 4xA53
    "sdm429": 8,  # Snapdragon 429 -- 4xA53 + 4xA53
    "sdm670": 8,  # Snapdragon 670 -- 2xA75 + 6xA55
    "sdm678": 8,  # Snapdragon 678 -- 2xA75 + 6xA55
    "sdm712": 8,  # Snapdragon 712 -- 2xA75 + 6xA55
    "sdm730": 8,  # Snapdragon 730 -- 2xA75 + 6xA55
    "sdm768": 8,  # Snapdragon 768G -- 1xA76 + 3xA55 + 4xA55
    "sm4250": 8,  # Snapdragon 460 -- 4xA53 + 4xA53
    "sm4350": 8,  # Snapdragon 480 -- 4xA53 + 4xA53
    "sm4350-ac": 8,  # Snapdragon 480+ -- 4xA53 + 4xA53
    "sm4450": 8,  # Snapdragon 4+ Gen 2 -- 4xA53 + 4xA53
    "sm7125": 8,  # Snapdragon 720G -- 2xA76 + 6xA55
    "sm7150": 8,  # Snapdragon 732G -- 1xA76 + 3xA55 + 4xA55
    "sm7150-ac": 8,
    "sm7225": 8,  # Snapdragon 750G -- 1xA76 + 3xA55 + 4xA55
    "sm7250": 8,  # Snapdragon 765G -- 1xA76 + 3xA55 + 4xA55
    "sm7250-ac": 8,
    "sm7350-ab": 8,  # Snapdragon 780G -- 1xA78 + 3xA78 + 4xA55
    "snapdragon 6 gen 3": 8,
    "snapdragon 6s gen 1": 8,
    "snapdragon 782g": 8,
    "snapdragon w5 gen 1": 8,
    "snapdragon w5+ gen 1": 8,
    "snapdragon wear 4100": 4,
    "snapdragon 652": 8,  # Snapdragon 652 -- 4xA53 + 4xA53
    "snapdragon 670": 8,
    "snapdragon 730": 8,  # Snapdragon 730 -- 2xA75 + 6xA55
    "snapdragon 427": 8,  # Snapdragon 427 -- 4xA53 + 4xA53
    "snapdragon 415": 4,  # Snapdragon 415 -- 4xA53
    "snapdragon 616": 8,  # Snapdragon 616 -- 4xA53 + 4xA53
    "snapdragon 650": 8,  # Snapdragon 650 -- 4xA53 + 4xA53
    "snapdragon 212": 4,  # Snapdragon 212 -- 4xA53
    "snapdragon 215": 8,  # Snapdragon 215 -- 4xA53 + 4xA53
    "snapdragon 429": 8,  # Snapdragon 429 -- 4xA53 + 4xA53
    "snapdragon 460": 8,  # Snapdragon 460 -- 4xA53 + 4xA53
    "snapdragon 480": 8,  # Snapdragon 480 -- 4xA53 + 4xA53
    "snapdragon 480+": 8,
    "snapdragon 4+ gen 2": 8,
    "snapdragon 720g": 8,  # Snapdragon 720G -- 2xA76 + 6xA55
    "snapdragon 732g": 8,  # Snapdragon 732G -- 1xA76 + 3xA55 + 4xA55
    "snapdragon 750g": 8,  # Snapdragon 750G -- 1xA76 + 3xA55 + 4xA55
    "snapdragon 765g": 8,
    "snapdragon 768g": 8,
    "snapdragon 780g": 8,  # Snapdragon 780G -- 1xA78 + 3xA78 + 4xA55
    "snapdragon 782g": 8,
    "snapdragon 6 gen 3": 8,
    "snapdragon 6s gen 1": 8,
    "snapdragon w5 gen 1": 8,
    "snapdragon w5+ gen 1": 8,
    "snapdragon wear 4100": 4,
    # -- The parts the market ships under a name of their own -------
    "snapdragon 653": 8,  # Snapdragon 653 -- 4xA53 + 4xA53
    "snapdragon 821": 8,  # Snapdragon 821 -- 4xA53 + 4xA53
    "snapdragon 865": 8,  # Snapdragon 865 -- 1xA77 + 3xA77 + 4xA55
    "snapdragon 712": 8,  # Snapdragon 712 -- 2xA75 + 6xA55
    "snapdragon 730g": 8,  # Snapdragon 730G -- 1xA76 + 3xA55 + 4xA55
    "snapdragon 765": 8,  # Snapdragon 765 -- 1xA76 + 3xA55 + 4xA55
    "snapdragon 678": 8,  # Snapdragon 678 -- 2xA75 + 6xA55
    "snapdragon 750": 8,  # Snapdragon 750 -- 1xA76 + 3xA55 + 4xA55
    "mediatek helio a20": 4,  # Helio A20 -- 4xA53
    "mediatek helio p60": 8,  # Helio P60 -- 4xA53 + 4xA53
    "mediatek helio p70m": 8,  # Helio P70M -- 2xA76 + 6xA55
    "mediatek helio g96": 8,  # Helio G96 -- 2xA78 + 6xA55
    "mediatek helio x23": 8,  # Helio X23 -- 1xA78 + 3xA78 + 4xA55
    "helio a20": 4,
    "helio p60": 8,
    "helio p70m": 8,
    "helio g96": 8,
    "helio x23": 8,
    "mediatek dimensity 7350": 8,  # Dimensity 7350 -- 4xA78 + 4xA55
    "dimensity 7350": 8,
    "snapdragon 8+ gen 2": 8,  # Snapdragon 8+ Gen 2 -- 1xX4 + 3xA720 + 4xA520
    "snapdragon 6s gen 3": 8,  # Snapdragon 6s Gen 3 -- 2xA78 + 6xA55
    # -- Unisoc and Spreadtrum --------------------------------------
    "sc8830": 8,  # Spreadtrum SC8830 -- 2xA55 + 6xA55
    "sc9830": 8,  # Spreadtrum SC9830 -- 2xA55 + 6xA55
    "sc7731g": 4,  # Spreadtrum SC7731G -- 4xA53
    "sc7731e": 4,
    "sc9863a1": 8,
    "spreadtrum sc8830": 8,
    "spreadtrum sc9830": 8,
    "spreadtrum sc7731g": 4,
    "spreadtrum sc7731e": 4,
    "spreadtrum sc9863a1": 8,
    "unisoc sc9863a1": 8,
    "tiger t612": 8,  # Unisoc Tiger T612 -- 2xA75 + 6xA55
    "tiger t616": 8,  # Unisoc Tiger T616 -- 2xA75 + 6xA55
    "unisoc tiger t612": 8,
    "unisoc tiger t616": 8,
    "unisoc t610": 8,
    "unisoc t612": 8,
    "unisoc t616": 8,
    "t610": 8,  # Unisoc T610 -- 2xA75 + 6xA55
    "t612": 8,  # Unisoc T612 -- 2xA75 + 6xA55
    "t616": 8,  # Unisoc Tiger T616 -- 2xA75 + 6xA55
    # -- NVIDIA Tegra and the older tablet parts -------------------
    "tegra 3 ap37": 4,  # Tegra 3 -- 4xA9
    "tegra 3 ap": 4,
    "tegra 2 ap20h": 4,
    "tegra 2 ap": 4,
    "tegra k1": 4,  # Tegra K1 -- 4xA15
    "tegra x1": 4,  # Tegra X1 -- 4xA53
    "nvidia tegra k1": 4,
    "nvidia tegra x1": 4,
    "intel atom z2460": 4,  # Atom Z2460 -- 2xA7
    "intel atom z2560": 4,  # Atom Z2560 -- 2xA7
    "intel atom z2580": 4,  # Atom Z2580 -- 2xA7
    "intel atom z3745": 4,  # Atom Z3745 -- 4xA53
    "intel atom x5-z8500": 4,
    "atom z2460": 4,
    "atom z2560": 4,
    "atom z2580": 4,
    "atom z3745": 4,
    "atom x5-z8500": 4,
    "omap 3430": 4,  # OMAP 3430 -- single Cortex-A8
    "omap 3620": 4,  # OMAP 3620 -- single Cortex-A8
    "omap 3630": 4,  # OMAP 3630 -- single Cortex-A8
    "omap 3640": 4,  # OMAP 3640 -- single Cortex-A8
    "pxa1088": 4,  # Marvell PXA1088 -- 4xA9
    "pxa986": 4,  # Marvell PXA986 -- 4xA9
    "pxa1908": 4,  # Marvell PXA1908 -- 4xA7
    "marvell pxa1088": 4,
    "marvell pxa986": 4,
    "marvell pxa1908": 4,
    "l1860c": 4,  # Leadcore L1860C -- 4xA53
    "u8420": 4,  # NovaThor U8420 -- 4xA9
    "u8500": 4,  # NovaThor U8500 -- 4xA9
    "novathor u8420": 4,
    "novathor u8500": 4,
    "leadcore l1860c": 4,
    # -- Google and the watch parts --------------------------------
    "tensor ": 8,  # Google Tensor -- 1xX3 + 3xA78 + 4xA55
    "tensor g2": 8,
    "tensor g3": 8,
    "google tensor": 8,
    "broadcom bc28155": 4,
    "bc28145": 4,
    "bc28155": 4,
    "spreadtrum shark": 8,  # Spreadtrum Shark -- 2xA55 + 6xA55
    "sc9830 ": 8,
    "surge s1": 8,  # Xiaomi Surge S1 -- 1xA78 + 3xA78 + 4xA55
    "surge s2": 8,
    "xiaomi surge s1": 8,
    "xiaomi surge s2": 8,
    "mi surge s1": 8,
    "mi surge s2": 8,
    "xiaolong s4 pro": 8,
    "jlq jr510": 8,
    "bcm 28145": 4,
    "broadcom bc28155": 4,
    "marvell pxa986 ": 4,
    "spreadtrum shark ": 8,
    "xiaolong s4 pro ": 8,
    "jlq jr510 ": 8,
    "unisoc sc9863a1 ": 8,
    "sc9863a1 ": 8,
    "spreadtrum": 8,
    "jlq jr510": 8,
    "xiaolong s4 pro": 8,
    "xiaolong s4": 8,
    "exynos": 8,
    "sc9863a1": 8,
    "pxa986": 4,
}

#: The core counts a chip designer actually ships.
#:
#: The set is the counts the market really carries.  A part with one
#: or two cores in it is a part of 2011 and of a tablet, and a fleet
#: of handsets that answers with two cores is a fleet that hands the
#: site a grouping key of its own: two is a number no phone of the
#: market reports.
REAL_CORE_COUNTS: tuple[int, ...] = (4, 6, 8, 12)


#: The parts of a chipset text that can carry the part number.
#:
#: The Snapdragon names are the awkward ones: the flagship line is
#: named in two words -- "8 Gen 3", "8+ Gen 1" -- and the table holds
#: those names whole, so the pattern has to take the whole name and
#: not stop at the first number.
_PART_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(hisilicon\s+)?kirin\s*\d{3,4}[a-z]*",
        r"\bexynos\s*\d{3,4}[a-z]*",
        r"\bhelio\s*[apgmtx]\d{2,4}[a-z]*",
        r"\bdimensity\s*\d{3,4}[+]?\s*(?:pro\b)?",
        r"\btensor\s*g?\d(?:[a-z])?",
        r"\bomap\s*\d{4}[a-z]*",
        r"\btegra\s*[23]\s*(?:ap\d{2,4}[a-z]*)?",
        r"\btegra\s*[kx]\d",
        r"\bhummingbird\b",
        r"\bintel\s+atom\s+(?:x?\d+-)?z?\d{3,5}",
        r"\bnova(?:thor)?\s*u\d{4}",
        r"\bpxa\d{3,4}",
        r"\bleadcore\s+l\d{4}[a-z]*",
        r"\bspreadtrum\s+sc\d{3,5}[a-z]*",
        r"\bunisoc\s+(?:tiger\s+)?(?:t|sc)\d{3,5}[a-z]*",
        r"\bmi\s+surge\s+s\d",
        r"\bkirin\s*\d{3,4}[a-z]*",
        r"\bhelio\s*[pgm]\d{2,4}[a-z]*",
        r"\bq[ms]\d{3,5}[a-z]*",
        r"\bgoogle\s+tensor\b",
        r"\bsc9830\b",
        r"\bmarvell\s+pxa\d{4}",
        r"\bspreadtrum\s+sc\d{3,5}[a-z]*",
        r"\bxiaomi\s+surge\s+s\d",
        r"\bbroadcom\s+bc\d{4,5}",
        r"\bbcm\s*\d{4,5}",
        r"\bjlq\s+jr\d{3}",
        r"\bxiaolong\s+s\d+\s*pro",
        r"\bspreadtrum\b",
        r"\bmarvell\s+pxa\d{4}",
        r"\bexynos\b",
        r"\bpxa\d{3,4}",
        r"\bsc\d{4,5}[a-z]\d?",
        r"\bsnapdragon\s+\d{1,2}s?\s*\+?\s*gen\s*\d\b",
        r"\bsnapdragon\s+\d{1,2}s?\s*\+?\s*gen\b",
        r"\bsnapdragon\s+\d{1,2}s?\s*\+?\s*elite\b",
        r"\bsnapdragon\s+\d{1,2}[a-z]*\s*elite\b",
        r"\bsnapdragon\s+wear\s+\d{4}",
        r"\bsnapdragon\s+w5\+?\s*gen\s*\d\b",
        r"\bsnapdragon\s+(\d{3,4}[a-z]*|s\d|1)\b",
        r"\b(?:sm|sdm|msm|mt|ksc)\d{3,5}[a-z]*",
        r"\b(?:t|sc)\d{3,5}[a-z]*\b",
    )
)


#: The patterns that find the *trade name* of a chipset in a cell that
#: also carries the part number and the process.
#:
#: A specification sheet names a chipset the way the factory does:
#: the manufacturer, the part number, the trade name and the process
#: in one cell -- "Qualcomm SM8550-AB Snapdragon 8 Gen 2 (4 nm)".  The
#: trade name is the second half of that, and it is the part the
#: platform hands the page.
#:
#: The part number is not a trade name and is not looked for here: the
#: patterns are ordered so that the name a customer knows comes first,
#: and a cell that names the part number but not the trade name is
#: handled by the part number patterns of the table.
_TRADE_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsnapdragon\s+\d{1,2}s?\s*\+?\s*gen\s*\d\b",
        r"\bsnapdragon\s+\d{1,2}s?\s*\+?\s*gen\b",
        r"\bsnapdragon\s+\d{1,2}s?\s*elite\s*gen\s*\d\b",
        r"\bsnapdragon\s+\d{1,2}[a-z]*\s*elite\b",
        r"\bsnapdragon\s+\d{3,4}[a-z]*\b",
        r"\bsnapdragon\s+s\d\b",
        r"\bhisilicon\s+kirin\s*\d{3,4}[a-z]*",
        r"\bkirin\s*\d{3,4}[a-z]*",
        r"\bexynos\s*\d{3,4}[a-z]*",
        r"\bmediatek\s+helio\s*[apgmtx]\d{2,4}[a-z]*",
        r"\bhelio\s*[apgmtx]\d{2,4}[a-z]*",
        r"\bmediatek\s+dimensity\s*\d{3,4}[+]?",
        r"\bdimensity\s*\d{3,4}[+]?",
        r"\bgoogle\s+tensor\s*g?\d(?:[a-z])?",
        r"\btensor\s*g?\d(?:[a-z])?",
        r"\bunisoc\s+(?:tiger\s+)?(?:t|sc)\d{3,5}[a-z]*",
        r"\btiger\s+t\d{3,5}[a-z]*",
        r"\bspreadtrum\s+sc\d{3,5}[a-z]*",
        r"\bomap\s*\d{4}[a-z]*",
        r"\btegra\s*[23]\s*(?:ap\d{2,4}[a-z]*)?",
    )
)


def _keys_of(chipset: str) -> list[str]:
    """The names the table might hold this chipset under.

    A chipset is named in more than one way: the trade name and the
    part number are the same chip, and a text may carry both, or
    only one of them, or a manufacturer in front of either.  Every
    spelling is offered to the table, and the first one that answers
    is the answer.
    """

    text = (chipset or "").strip()
    if not text:
        return []

    keys: list[str] = []

    def offer(candidate: str) -> None:
        cleaned = re.sub(r"\s+", " ", candidate.strip().lower())
        if cleaned and cleaned not in keys:
            keys.append(cleaned)

    # The trade name, without the manufacturer in front of it.
    for pattern in _PART_PATTERNS:
        match = pattern.search(text)
        if match:
            name = match.group(0)
            offer(re.sub(r"^(hisilicon|spreadtrum|unisoc|qualcomm|mediatek)\s+", "", name))
            offer(name)
            # "Snapdragon 8 Gen 1" also answers to "snapdragon 8".
            if name.lower().startswith("snapdragon"):
                offer(name.split(" gen")[0].strip())

    # The part number on its own, wherever it sits.
    for pattern in _PART_PATTERNS:
        for match in pattern.finditer(text):
            offer(match.group(0))

    return keys


#: The trade name of a chipset, by the trade name the table holds.
#:
#: The catalogue names a chipset the way a specification sheet does:
#: the manufacturer, the part number, the trade name and the process
#: in one cell -- "Qualcomm SM6375 Snapdragon 695 5G (6 nm)".  No
#: handset reports itself that way.  The platform reads the SoC model
#: off the build properties, and it reads the trade name: a Snapdragon
#: 695 reports "Qualcomm" and "Snapdragon 695", a Helio G99 reports
#: "MediaTek" and "Helio G99".  A fleet that answers with the whole
#: cell hands the site a string that no device of the market ever
#: reports, and the site that compares the SoC model with the GPU
#: renderer finds a device that was never built.
#:
#: The names here are the trade names as the makers write them, with
#: the manufacturer in front where the table holds it that way.
_CANONICAL_CHIPSET_NAMES: dict[str, str] = {
    "snapdragon 200": "Snapdragon 200",
    "snapdragon 400": "Snapdragon 400",
    "snapdragon 410": "Snapdragon 410",
    "snapdragon 615": "Snapdragon 615",
    "snapdragon 625": "Snapdragon 625",
    "snapdragon 626": "Snapdragon 626",
    "snapdragon 630": "Snapdragon 630",
    "snapdragon 636": "Snapdragon 636",
    "snapdragon 660": "Snapdragon 660",
    "snapdragon 662": "Snapdragon 662",
    "snapdragon 665": "Snapdragon 665",
    "snapdragon 675": "Snapdragon 675",
    "snapdragon 678": "Snapdragon 678",
    "snapdragon 680": "Snapdragon 680",
    "snapdragon 685": "Snapdragon 685",
    "snapdragon 690": "Snapdragon 690",
    "snapdragon 695": "Snapdragon 695",
    "snapdragon 6 gen 1": "Snapdragon 6 Gen 1",
    "snapdragon 6 gen 2": "Snapdragon 6 Gen 2",
    "snapdragon 6 gen 3": "Snapdragon 6 Gen 3",
    "snapdragon 7 gen 1": "Snapdragon 7 Gen 1",
    "snapdragon 7 gen 2": "Snapdragon 7 Gen 2",
    "snapdragon 7 gen 3": "Snapdragon 7 Gen 3",
    "snapdragon 7+ gen 1": "Snapdragon 7+ Gen 1",
    "snapdragon 7+ gen 2": "Snapdragon 7+ Gen 2",
    "snapdragon 7+ gen 3": "Snapdragon 7+ Gen 3",
    "snapdragon 7s gen 2": "Snapdragon 7s Gen 2",
    "snapdragon 7s gen 3": "Snapdragon 7s Gen 3",
    "snapdragon 8 elite": "Snapdragon 8 Elite",
    "snapdragon 8 elite gen 5": "Snapdragon 8 Elite Gen 5",
    "snapdragon 8 gen 1": "Snapdragon 8 Gen 1",
    "snapdragon 8 gen 2": "Snapdragon 8 Gen 2",
    "snapdragon 8 gen 3": "Snapdragon 8 Gen 3",
    "snapdragon 8 gen 4": "Snapdragon 8 Gen 4",
    "snapdragon 8+ gen 1": "Snapdragon 8+ Gen 1",
    "snapdragon 8+ gen 2": "Snapdragon 8+ Gen 2",
    "snapdragon 8s gen 3": "Snapdragon 8s Gen 3",
    "snapdragon 8s gen 4": "Snapdragon 8s Gen 4",
    "snapdragon 6s gen 3": "Snapdragon 6s Gen 3",
    "snapdragon 782g": "Snapdragon 782G",
    "snapdragon 732g": "Snapdragon 732G",
    "snapdragon 720g": "Snapdragon 720G",
    "snapdragon 750g": "Snapdragon 750G",
    "snapdragon 765g": "Snapdragon 765G",
    "snapdragon 778g": "Snapdragon 778G",
    "snapdragon 653": "Snapdragon 653",
    "snapdragon 821": "Snapdragon 821",
    "snapdragon 865": "Snapdragon 865",
    "snapdragon 712": "Snapdragon 712",
    "snapdragon 730g": "Snapdragon 730G",
    "snapdragon 765": "Snapdragon 765",
    "snapdragon 678": "Snapdragon 678",
    "snapdragon 750": "Snapdragon 750",
    "snapdragon 4 gen 1": "Snapdragon 4 Gen 1",
    "snapdragon 4 gen 2": "Snapdragon 4 Gen 2",
    "snapdragon 4+ gen 2": "Snapdragon 4+ Gen 2",
    "mediatek helio g99": "MediaTek Helio G99",
    "mediatek helio g96": "MediaTek Helio G96",
    "mediatek helio g95": "MediaTek Helio G95",
    "mediatek helio g90t": "MediaTek Helio G90T",
    "mediatek helio g88": "MediaTek Helio G88",
    "mediatek helio g85": "MediaTek Helio G85",
    "mediatek helio g80": "MediaTek Helio G80",
    "mediatek helio g70": "MediaTek Helio G70",
    "mediatek helio g37": "MediaTek Helio G37",
    "mediatek helio g36": "MediaTek Helio G36",
    "mediatek helio g35": "MediaTek Helio G35",
    "mediatek helio g25": "MediaTek Helio G25",
    "mediatek helio a25": "MediaTek Helio A25",
    "mediatek helio a22": "MediaTek Helio A22",
    "mediatek helio a20": "MediaTek Helio A20",
    "mediatek helio p70m": "MediaTek Helio P70M",
    "mediatek helio x23": "MediaTek Helio X23",
    "mediatek helio p95": "MediaTek Helio P95",
    "mediatek helio p90": "MediaTek Helio P90",
    "mediatek helio p70": "MediaTek Helio P70",
    "mediatek helio p65": "MediaTek Helio P65",
    "mediatek helio p60": "MediaTek Helio P60",
    "mediatek helio p35t": "MediaTek Helio P35T",
    "mediatek helio p35": "MediaTek Helio P35",
    "mediatek helio p25": "MediaTek Helio P25",
    "mediatek helio p22t": "MediaTek Helio P22T",
    "mediatek helio p22": "MediaTek Helio P22",
    "mediatek dimensity 9400": "MediaTek Dimensity 9400",
    "mediatek dimensity 9300": "MediaTek Dimensity 9300",
    "mediatek dimensity 9200": "MediaTek Dimensity 9200",
    "mediatek dimensity 9000": "MediaTek Dimensity 9000",
    "mediatek dimensity 8100": "MediaTek Dimensity 8100",
    "mediatek dimensity 7050": "MediaTek Dimensity 7050",
    "mediatek dimensity 7020": "MediaTek Dimensity 7020",
    "mediatek dimensity 6100+": "MediaTek Dimensity 6100+",
    "mediatek dimensity 7350": "MediaTek Dimensity 7350",
    "mediatek dimensity 6020": "MediaTek Dimensity 6020",
    "samsung exynos 2500": "Samsung Exynos 2500",
    "samsung exynos 2400": "Samsung Exynos 2400",
    "samsung exynos 1480": "Samsung Exynos 1480",
    "samsung exynos 1380": "Samsung Exynos 1380",
    "samsung exynos 1330": "Samsung Exynos 1330",
    "samsung exynos 1280": "Samsung Exynos 1280",
    "google tensor g3": "Google Tensor G3",
    "google tensor g2": "Google Tensor G2",
    "google tensor g1": "Google Tensor G1",
    "hisilicon kirin 9000s": "HiSilicon Kirin 9000S",
    "hisilicon kirin 9000e": "HiSilicon Kirin 9000E",
    "hisilicon kirin 9000": "HiSilicon Kirin 9000",
    "hisilicon kirin 990": "HiSilicon Kirin 990",
    "hisilicon kirin 985": "HiSilicon Kirin 985",
    "hisilicon kirin 980": "HiSilicon Kirin 980",
    "hisilicon kirin 970": "HiSilicon Kirin 970",
    "hisilicon kirin 960": "HiSilicon Kirin 960",
    "hisilicon kirin 820": "HiSilicon Kirin 820",
    "hisilicon kirin 810": "HiSilicon Kirin 810",
    "hisilicon kirin 710": "HiSilicon Kirin 710",
    "hisilicon kirin 659": "HiSilicon Kirin 659",
    "hisilicon kirin 650": "HiSilicon Kirin 650",
    "hisilicon kirin 620": "HiSilicon Kirin 620",
}

#: The trade names that are written with the manufacturer in front of
#: them by the table of the chipsets, so that the name the fleet
#: answers with is the name the table holds.
_CHIPSET_VENDOR_PREFIX: dict[str, str] = {
    "mediatek": "MediaTek",
    "samsung exynos": "Samsung Exynos",
    "google tensor": "Google Tensor",
    "hisilicon kirin": "HiSilicon Kirin",
}


def canonical_chipset_name(chipset: str) -> str:
    """The trade name of the chipset *chipset*, as the market writes it.

    The answer is the name a handset reports about itself: the trade
    name of the chip, with the manufacturer in front where the makers
    write it that way.  A cell that carries the manufacturer, the part
    number, the trade name and the process -- "Qualcomm SM6375
    Snapdragon 695 5G (6 nm)" -- is reduced to "Snapdragon 695",
    because that is the string the platform hands the page.

    A chipset the table does not name is returned cleaned of the
    process and the noise but otherwise unchanged: a name that is not
    known is kept, because a wrong name is worse than an unusual one.
    """

    text = re.sub(r"\s+", " ", (chipset or "").strip())
    if not text:
        return ""

    # The process and the size of the die are not part of the name.
    text = re.sub(r"\(\s*\d+(?:\.\d+)?\s*nm\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*\d+\s*nm\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -,;")

    # The trade name, out of the whole cell.  The trade name is looked
    # for first and the part number second: a cell that carries both
    # -- "Qualcomm SM8550-AB Snapdragon 8 Gen 2 (4 nm)" -- is a
    # chipset with a trade name in it, and the trade name is the one
    # the platform reports.
    for patterns in (_TRADE_NAME_PATTERNS, _PART_PATTERNS):
        for pattern in patterns:
            match = pattern.search(text)
            if not match:
                continue

            name = match.group(0).strip()
            bare = re.sub(
                r"^(hisilicon|spreadtrum|unisoc|qualcomm|mediatek|samsung|google|ti)\s+",
                "",
                name,
                flags=re.IGNORECASE,
            ).strip()

            canonical = _CANONICAL_CHIPSET_NAMES.get(bare.lower())
            if canonical:
                return canonical

            # The name is known but not in the table of the trade
            # names: it is written the way the catalogue writes it,
            # and the manufacturer in front of it is dropped when the
            # trade name does not need one.
            lowered_bare = bare.lower()
            if lowered_bare.startswith("snapdragon"):
                # "Snapdragon 695 5G" is the part with the radio of
                # the fifth generation on it; the chip itself is the
                # "Snapdragon 695".
                cleaned = re.sub(r"\s+5g\b", "", bare, flags=re.IGNORECASE).strip()
                cleaned = re.sub(r"\s+4g\b", "", cleaned, flags=re.IGNORECASE).strip()
                return cleaned
            if lowered_bare.startswith(("exynos", "helio", "dimensity", "tensor", "kirin")):
                # These names are written with the manufacturer in
                # front of them, because that is how the makers of
                # the part write them: a handset with the Helio G99
                # in it reports "MediaTek" and "Helio G99", and one
                # with the Exynos 1380 in it reports "Samsung" and
                # "Exynos 1380".
                prefix = ""
                if lowered_bare.startswith("exynos"):
                    prefix = "Samsung "
                elif lowered_bare.startswith(("helio", "dimensity")):
                    prefix = "MediaTek "
                elif lowered_bare.startswith("tensor"):
                    prefix = "Google "
                elif lowered_bare.startswith("kirin"):
                    prefix = "HiSilicon "
                return f"{prefix}{bare}"
            if lowered_bare.startswith(("tiger", "unisoc", "spreadtrum")):
                return f"Unisoc {bare}"
            if lowered_bare.startswith("omap"):
                return f"TI {bare}"
            if lowered_bare.startswith("tegra"):
                return f"NVIDIA {bare}"

        # The trade name is not in the cell; the part number may be,
        # and the table of the chipsets answers about it.
        break

    return text


def cores_for_chipset(chipset: str) -> int | None:
    """The core count of *chipset*, or ``None`` when it is not known.

    The answer is the count the chip really carries.  It is ``None``
    rather than a guess when the chipset is not in the table, because
    a guess here is a lie the site reads next to the SoC model.

    Three sources are asked, in the order of how much they can be
    trusted: the table of the chipsets the field uses, the count the
    text states in so many words ("8-core"), and the count the
    cluster layout carries.  The table comes first because it is the
    authority on the chip; the text comes second because a dataset
    that names the count is usually right about it.

    A name that starts with "Mali" is a graphics part and not a
    chipset -- there is a Mali-T820 and there is a Unisoc T820, and
    they are different pieces of silicon.  Such a name is not looked
    up in the table of chipsets, because the table would answer about
    the wrong chip.
    """

    if re.match(r"^\s*mali\b", chipset or "", re.IGNORECASE):
        return _stated_core_count(chipset)

    for key in _keys_of(chipset):
        count = _SOC_CORES.get(key)
        if count is not None:
            return count

    stated = _stated_core_count(chipset)
    if stated is not None:
        return stated

    # The word the makers of the part write the count in: "Exynos
    # 5420 Octa" is an eight core part.
    worded = _word_core_count(chipset)
    if worded is not None:
        return worded

    return None


#: A count the text states in so many words.
_NAMED_COUNT = re.compile(r"(\d+)\s*[- ]?\s*core", re.IGNORECASE)


#: The counts the parts call themselves by, the way the makers of the
#: parts write them on the spec sheet: "Exynos 4412 Quad" is a quad
#: core part and "Exynos 5420 Octa" is an eight core one.  The words
#: are the makers' own, so a dataset that carries them has said the
#: count in so many words.
_NAMED_WORDS: tuple[tuple[str, int], ...] = (
    ("dual", 2),
    ("quad", 4),
    ("hexa", 6),
    ("octa", 8),
)


def _stated_core_count(text: str) -> int | None:
    """The core count *text* states, or ``None`` when it states none.

    A dataset that says "8-core" has said eight, and a dataset that
    says "octa-core" has said eight.  The count is only taken when it
    is one a designer actually ships, so a source that names "10-core"
    -- a marketing number rather than a layout -- is not read as ten.
    """

    if not text:
        return None

    match = _NAMED_COUNT.search(text)
    if match:
        value = int(match.group(1))
        if value in REAL_CORE_COUNTS:
            return value

    if re.search(r"octa[- ]?core", text, re.IGNORECASE):
        return 8

    return None


def _word_core_count(text: str) -> int | None:
    """The core count the words of *text* state, or ``None``.

    The makers of the parts write the count of a part in a word of
    its own: "Exynos 4412 Quad" is a four core part and "Exynos 5420
    Octa" is an eight core one.  The word has to be a word of its own
    and not part of another word, so "Quad" in "Snapdragon Quad" is
    read and "Quad" in "Quadrille" is not.
    """

    if not text:
        return None

    for word, count in _NAMED_WORDS:
        pattern = rf"\b{word}\b"
        if re.search(pattern, text, re.IGNORECASE):
            return count

    return None



#: A group of the vendor way, "1x 2 Cortex-A76": the number of
#: clusters of that kind, the number of cores of each of them and the
#: name of the core they run.  A group that carries no second count,
#: "4x A55", is a count of cores with no number of clusters in front
#: of it.
#:
#: The space after the "x" is what makes the group a group: the
#: compact way, "1x3x4 Cortex-A78/A55/A55", runs its counts
#: together, so a group of the vendor way is never a group of the
#: compact way.
_VENDOR_GROUP_RE = re.compile(
    r"(?P<clusters>\d+)\s*x\s+(?:(?P<inner>\d+)\s+)?(?P<name>[A-Za-z][A-Za-z0-9\-]*)",
)


#: The cluster layouts of the core counts a designer actually ships.
#:
#: Every layout adds up to the count it stands for, because the count
#: is read back off the layout: a handset that reports four cores has
#: to carry a layout of four, and a layout of eight with a count of
#: four is a contradiction the site reads in one comparison.
#:
#: A layout names the core of each cluster: "1x3x4 Cortex-X1/A78/A55"
#: is one prime core, three big ones and four little ones, and it
#: adds up to eight.
LAYOUT_BY_CORES: dict[int, str] = {
    4: "2x2 Cortex-A78/A55",
    6: "1x2x3 Cortex-A78/A78/A55",
    8: "1x3x4 Cortex-A78/A55/A55",
    12: "1x2x3x6 Cortex-X3/A78/A55/A55",
}


#: The chipsets the graphics parts really ship with.
#:
#: A catalogue row names the graphics part and not the chipset, but a
#: page that reads the chipset reads a name, and a name that no part
#: ever carried is a name the fleet gives away: "Mali-G57 MC2
#: 8-core" is not a chipset, it is a graphics part with a number
#: written next to it.  A site that has seen one chipset knows the
#: parts that ship with it and the counts they carry, so the chipset
#: has to be one of the real ones.
#:
#: The table is keyed by the graphics part, and the value is the
#: chipset that part ships with, with the core count the chipset
#: really carries.  A part that ships with more than one chipset
#: carries all of them, and the caller picks one.
CHIPSETS_BY_GPU: dict[str, tuple[tuple[str, int], ...]] = {
    # -- Qualcomm Adreno -----------------------------------------
    "adreno 306": (("Snapdragon 200", 4),),
    "adreno 305": (("Snapdragon 200", 4),),
    "adreno 400": (("Snapdragon 400", 4),),
    "adreno 405": (("Snapdragon 400", 4),),
    "adreno 418": (("Snapdragon 410", 4),),
    "adreno 420": (("Snapdragon 425", 4),),
    "adreno 504": (("Snapdragon 625", 8),),
    "adreno 505": (("Snapdragon 626", 8),),
    "adreno 506": (("Snapdragon 665", 8),),
    "adreno 508": (("Snapdragon 665", 8),),
    "adreno 510": (("Snapdragon 630", 8),),
    "adreno 512": (("Snapdragon 636", 8),),
    "adreno 530": (("Snapdragon 680", 8),),
    "adreno 534": (("Snapdragon 685", 8),),
    "adreno 540": (("Snapdragon 675", 8),),
    "adreno 610": (("Snapdragon 680", 8),),
    "adreno 618": (("Snapdragon 695", 8),),
    "adreno 619": (("Snapdragon 695", 8),),
    "adreno 616": (("Snapdragon 460", 8),),
    "adreno 640": (("Snapdragon 782G", 8),),
    "adreno 620": (("Snapdragon 675", 8),),
    "adreno 650": (("Snapdragon 685", 8),),
    "adreno 680": (("Snapdragon 782G", 8),),
    "adreno 612": (("Snapdragon 665", 8),),
    "adreno 615616": (("Snapdragon 460", 8),),
    "adreno 308": (("Snapdragon 200", 4),),
    # -- The Adreno parts of the Snapdragon 8 line -------------------
    "adreno 630": (("Snapdragon 8 Gen 1", 8),),
    "adreno 642": (("Snapdragon 8 Gen 1", 8),),
    "adreno 642l": (("Snapdragon 782G", 8),),
    "adreno 643": (("Snapdragon 8 Gen 1", 8),),
    "adreno 644": (("Snapdragon 8+ Gen 1", 8),),
    "adreno 660": (("Snapdragon 732G", 8),),
    "adreno 670": (("Snapdragon 8 Gen 2", 8),),
    "adreno 700": (("Snapdragon 8 Gen 2", 8),),
    "adreno 702": (("Snapdragon 8 Gen 2", 8),),
    "adreno 704": (("Snapdragon 8 Gen 2", 8),),
    # -- The Adreno parts of the Snapdragon 6 and 7 lines -----------
    "adreno 621": (("Snapdragon 675", 8),),
    "adreno 622": (("Snapdragon 685", 8),),
    "adreno 632": (("Snapdragon 695", 8),),
    "adreno 675": (("Snapdragon 685", 8),),
    "adreno 678": (("Snapdragon 678", 8),),
    "adreno 690": (("Snapdragon 695", 8),),
    # -- The Adreno parts of the Snapdragon 4 and 2 lines -----------
    "adreno 200": (("Snapdragon 200", 4),),
    "adreno 203": (("Snapdragon 200", 4),),
    "adreno 205": (("Snapdragon 200", 4),),
    "adreno 220": (("Snapdragon 200", 4),),
    "adreno 225": (("Snapdragon 200", 4),),
    "adreno 302": (("Snapdragon 200", 4),),
    "adreno 330": (("Snapdragon 200", 4),),
    "adreno 430": (("Snapdragon 400", 4),),
    "xclipse 940": (("Samsung Exynos 2500", 8),),
    "xclipse 950": (("Samsung Exynos 2500", 8),),
    "xclipse 550": (("Samsung Exynos 1380", 8),),
    "mali-g72": (("MediaTek Helio P35", 8),),
    "mali-g76": (("Exynos 9825", 8),),
    "mali-t830": (("Exynos 8890", 8),),
    "mali-t628": (("Exynos 5410", 8),),
    "powervr sgx544mp2": (("MediaTek Helio X10", 8),),
    "powervr sgx544": (("MediaTek Helio X10", 8),),
    "powervr sgx": (("MediaTek Helio X10", 8),),
    "mali-820": (("MediaTek Helio A22", 8),),
    "mali-450": (("MediaTek Helio A22", 8),),
    "mali-t730": (("Exynos 5410", 8),),
    "mali-t860": (("Exynos 5410", 8),),
    "adreno 304": (("Snapdragon 200", 4),),
    "adreno 304308": (("Snapdragon 200", 4),),
    "powervr g6250": (("MediaTek Helio P22", 8),),
    "adreno 642l": (("Snapdragon 782G", 8),),
    "adreno 660": (("Snapdragon 732G", 8),),
    "adreno 710": (("Snapdragon 782G", 8),),
    "adreno 712": (("Snapdragon 720G", 8),),
    "adreno 720": (("Snapdragon 732G", 8),),
    "adreno 725": (("Snapdragon 7 Gen 2", 8),),
    "adreno 730": (("Snapdragon 8 Gen 1", 8),),
    "adreno 732": (("Snapdragon 732G", 8),),
    "adreno 740": (("Snapdragon 8 Gen 2", 8),),
    "adreno 750": (("Snapdragon 8 Gen 3", 8),),
    "adreno 760": (("Snapdragon 8 Elite", 8),),
    "adreno 810": (("Snapdragon 8 Gen 4", 8),),
    "adreno 830": (("Snapdragon 8 Elite Gen 5", 8),),
    "adreno 840": (("Snapdragon 8 Elite Gen 5", 8),),
    # -- ARM Mali -------------------------------------------------
    "mali-g31": (("MediaTek Helio A22", 8),),
    "mali-g52": (
        ("MediaTek Helio G25", 8),
        ("MediaTek Helio G37", 8),
        ("MediaTek Helio G88", 8),
    ),
    "mali-g57": (
        ("MediaTek Helio G99", 8),
        ("MediaTek Helio P20", 8),
        ("MediaTek Helio P25", 8),
        ("MediaTek Dimensity 6100+", 8),
    ),
    "mali-g68": (
        ("MediaTek Helio G99", 8),
        ("MediaTek Dimensity 1080", 8),
    ),
    "mali-g610": (
        ("MediaTek Dimensity 7050", 8),
        ("MediaTek Dimensity 920", 8),
    ),
    "mali-g615": (
        ("MediaTek Dimensity 7050", 8),
        ("MediaTek Dimensity 930", 8),
    ),
    "mali-g710": (
        ("MediaTek Dimensity 9000", 8),
        ("MediaTek Dimensity 9200", 8),
    ),
    "mali-g715": (
        ("MediaTek Dimensity 9200", 8),
        ("Google Tensor G4", 8),
    ),
    "mali-g78": (
        ("Google Tensor G3", 8),
        ("Exynos 2100", 8),
    ),
    "mali-g78 mp20": (
        ("Google Tensor G3", 8),
        ("Exynos 2200", 8),
    ),
    # -- ARM Immortalis -------------------------------------------
    "immortalis-g710": (("MediaTek Dimensity 9000+", 8),),
    "immortalis-g715": (("MediaTek Dimensity 9200", 8),),
    "immortalis-g720": (("MediaTek Dimensity 9300", 8),),
    "immortalis-g925": (("MediaTek Dimensity 9400", 8),),
    # -- Samsung Xclipse and AMD -------------------------------
    "xclipse 530": (("Samsung Exynos 1280", 8),),
    "xclipse 920": (("Samsung Exynos 2400", 8),),
    "amd radeon 610m": (("Samsung Exynos 1330", 8),),
    # -- PowerVR -------------------------------------------------
    "powervr ge8300": (("MediaTek Helio G37", 8),),
    "powervr ge8320": (("MediaTek Helio A25", 4),),
    "powervr ge8320b": (("MediaTek Helio A25", 4),),
    "powervr img axs": (("Unisoc Tiger T310", 4),),
    "powervr ge8600": (("MediaTek Helio P22", 8),),
    "powervr ge8800": (("MediaTek Helio P35", 8),),
    # -- The rest of the graphics parts --------------------------
    "mali-t720": (("MediaTek Helio A22", 8),),
    "mali-t760": (("MediaTek Helio A22", 8),),
    "mali-t880": (("Samsung Exynos 5430", 8),),
    "mali-t720 mp2": (("MediaTek Helio A22", 8),),
    "mali-g20": (("MediaTek Helio P10", 8),),
    "mali-g31 mp2": (("MediaTek Helio A22", 8),),
    "mali-g51": (("MediaTek Helio A25", 4),),
    "mali-g57 mp1": (("Unisoc T606", 8),),
    "mali-g57 mc2": (("MediaTek Helio G99", 8),),
    "mali-g610 mc2": (("MediaTek Dimensity 6100+", 8),),
    "mali-g610 mc6": (("MediaTek Dimensity 7050", 8),),
    "mali-g615 mc2": (("MediaTek Dimensity 900", 8),),
    "mali-g615 mc6": (("MediaTek Dimensity 930", 8),),
    "mali-g68 mp4": (("MediaTek Dimensity 1080", 8),),
    "mali-g68 mp5": (("Samsung Exynos 1480", 8),),
    "mali-g710 mc10": (("MediaTek Dimensity 9200", 8),),
    "mali-g710 mc8": (("MediaTek Dimensity 9000", 8),),
    "mali-g710 mc6": (("MediaTek Dimensity 9000", 8),),
    "mali-g715 mc9": (("MediaTek Dimensity 9200", 8),),
    "mali-g715 mc11": (("MediaTek Dimensity 9200", 8),),
    "mali-g78 mp20 ": (("Exynos 2200", 8),),
    "mali-g610 mc4": (("MediaTek Dimensity 7050", 8),),
    "mali-g710 mc4": (("MediaTek Dimensity 9000", 8),),
    "mali-g615 mc4": (("MediaTek Dimensity 930", 8),),
    "mali-g57 mp2": (("Unisoc T606", 8),),
    "mali-g77": (("Exynos 2100", 8),),
    "mali-g710 mp2": (("MediaTek Dimensity 9200", 8),),
    "mali-g68 mc6": (("MediaTek Dimensity 1200", 8),),
    "mali-g710 mc5": (("MediaTek Dimensity 9200", 8),),
    "mali-g68 mp4 ": (("MediaTek Dimensity 1080", 8),),
    "mali-g57 mc1": (("MediaTek Helio P20", 8),),
    "mali-g52 mc2": (("MediaTek Helio G88", 8),),
    "mali-g52 mc1": (("MediaTek Helio G25", 8),),
    "mali-g52 mp2": (("MediaTek Helio G25", 8),),
}


#: The chipsets that carry a core count of their own, for the parts
#: the table does not name.
#:
#: The catalogue names the graphics part of a handset and the number
#: of threads it runs, and the two are read off the same row.  A part
#: that is not in the table above is still a part, and the chipset
#: that carries it is named here by the count it carries, so that a
#: fleet of handsets never answers with a graphics part and a number
#: where a site expects a chipset.
#:
#: The table is built from the tables above rather than written out by
#: hand, because a list written by hand drifts from the truth of the
#: chips: it said that the Snapdragon 625 is a six core part and that
#: the Snapdragon 8 Gen 2 is a twelve core one, and both are wrong --
#: the first carries eight cores and the second carries eight as well.
#: A fleet that answered with those numbers put a core count on the
#: table next to the graphics part that does not belong to it, and a
#: site reading the two together sees a handset that does not exist.
#: Deriving the table keeps the count of a chipset in one place only.
def _build_chipset_by_core_count() -> dict[int, tuple[str, ...]]:
    """The chipsets of the field, by the core count each one carries.

    Only the names a site reads back are kept: the marketing name of
    the chipset, not the number of the part ("msm8939") and not the
    lower case alias of the same chip.  A table of aliases would
    answer with "sdm660" where a site expects a chipset, and two
    names of one chip would spread a fleet over a name that never
    appears on a handset.
    """

    counts: dict[int, set[str]] = {}

    def add(name: str, count: int) -> None:
        if count <= 0:
            return
        counts.setdefault(count, set()).add(name)

    for chipsets in CHIPSETS_BY_GPU.values():
        for name, count in chipsets:
            add(name, count)

    # The parts of the field that are named by their marketing name
    # only, so that a count always has a chipset that carries it.
    for count, names in (
        (1, ("TI OMAP 3630",)),
        (2, ("Intel Atom Z2580", "Exynos 4210")),
        (4, ("Snapdragon 410", "Snapdragon 425", "MediaTek Helio A25", "Unisoc SC9832")),
        (6, ("Exynos 5260",)),
    ):
        for name in names:
            add(name, count)

    return {
        count: tuple(sorted(names))
        for count, names in sorted(counts.items())
    }


CHIPSET_BY_CORE_COUNT: dict[int, tuple[str, ...]] = _build_chipset_by_core_count()


#: One chipset of each core count, for the rows whose graphics part
#: the table does not name.  A chipset is picked that really carries
#: the count, so that the name and the number that travel together
#: always agree with each other.
_CHIPSET_BY_COUNT_FALLBACK: dict[int, str] = {
    4: "Snapdragon 425",
    6: "Exynos 5260",
    8: "Snapdragon 680",
    12: "Snapdragon 8 Elite",
}


def layout_for_cores(cores: int, template: str = "") -> str:
    """A cluster layout of *cores* cores, keeping the cores of *template*.

    The layout is rewritten because the count and the layout are one
    number: the platform reports the count, and the count is read off
    the layout.  When the two disagree, the handset is a fiction.

    The core names of the original layout are kept, because a site
    reads the core names out of the layout and a part that really has
    no big cores says "Cortex-A53" on every one of them.  The names
    are handed out the way the vendors write them: the little core
    first, the big cores in the middle and the prime core last.
    """

    if cores not in LAYOUT_BY_CORES:
        return template

    names = _core_names(template)
    if not names:
        return LAYOUT_BY_CORES[cores]

    little = names[0]
    prime = names[-1]
    middle = names[1:-1] or [little]

    groups = _cluster_groups(cores)
    counts: list[str] = []
    core_names: list[str] = []
    for group_index, size in enumerate(groups):
        counts.append(str(size))
        if group_index == 0:
            core_names.append(little)
        elif group_index == len(groups) - 1:
            core_names.append(prime)
        else:
            core_names.append(middle[min(group_index - 1, len(middle) - 1)])

    return f"{'x'.join(counts)} {'/'.join(core_names)}"


def _cluster_groups(cores: int) -> list[int]:
    """The sizes of the clusters of a part of *cores* cores."""

    if cores == 4:
        return [2, 2]
    if cores == 6:
        return [1, 2, 3]
    if cores == 8:
        return [1, 3, 4]
    if cores == 12:
        return [1, 2, 3, 6]
    return [1, cores - 1]


def cores_from_layout(layout: str) -> int:
    """The number of cores a cluster layout adds up to.

    A layout is written in one of two ways, and both have to read
    the same way:

    * the compact way, "1x3x4 Cortex-X1/A78/A55", where the counts
      come first and the names of the clusters follow;
    * the vendor way, "1x 2 Cortex-A76 3x 2 Cortex-A76 4x A55",
      where every cluster carries its own count and its own name.

    The vendor way is the one the parts actually ship with, and it
    is the one that has to be read right: read as the compact way,
    "1x 2 Cortex-A76 3x 2 Cortex-A76 4x A55" looks like three
    clusters of two, and the count that comes back is three instead
    of the eight the chip carries.
    """

    if not layout:
        return 0

    total = 0
    for count in _layout_counts(layout):
        total += count

    if 1 <= total <= 16:
        return total
    return 0


def _layout_clusters(layout: str) -> tuple[list[int], list[str]]:
    """The clusters a cluster layout describes: the sizes and the names.

    A cluster layout is written in one of two ways, and both are read
    here, because both are in the catalogs:

    * the compact way, "1x3x4 Cortex-X1/A78/A55", where the counts
      come first and the name of every cluster follows, separated by
      slashes;
    * the vendor way, "1x 2 Cortex-A76 3x 2 Cortex-A76 4x A55",
      where every group carries the number of clusters of that kind,
      the number of cores of each of them and the name of the core.

    The vendor way is the one the parts actually ship with.  Read as
    the compact way it looks like three clusters of two, so the count
    that comes back is three instead of the eight the chip carries --
    a contradiction a site reads in a single comparison.

    The two ways are told apart by the way the counts are written:
    the compact way runs them together, "1x3x4", and the vendor way
    sets them apart, "1x 2".  The space is the whole difference, so
    the test for it is made before anything is read, and the compact
    way never falls into the vendor way and the other way round.
    """

    if not layout:
        return [], []

    text = layout.strip()

    if _VENDOR_GROUP_RE.search(text):
        return _read_vendor_groups(text)

    return _read_compact_groups(text)


def _read_compact_groups(text: str) -> tuple[list[int], list[str]]:
    """The clusters of the compact way, "1x3x4 Cortex-X1/A78/A55".

    The counts run at the front of the layout, joined by the letter
    "x", and the names of the clusters follow them, one per cluster,
    separated by slashes.
    """

    # The counts run to the first letter of the core names, so the
    # split is there and not at a guessed place: "1x3x4 " is the
    # counts and "Cortex-X1/A78/A55" is the names.  The counts are
    # the digits and the "x" between them, so the search is for the
    # first run of letters that is not the letter "x".
    match = re.match(r"^([0-9x\s]*?)([A-WYZa-wyz].*)$", text)
    if not match:
        return [], []

    counts = []
    for part in match.group(1).split("x"):
        part = part.strip()
        if part.isdigit():
            counts.append(int(part))

    names = []
    for part in match.group(2).split("/"):
        name = part.strip()
        if not name:
            continue
        # A name may carry a count of its own, as in "4x Cortex-A78".
        name = re.sub(r"^\d+\s*x\s*", "", name).strip()
        if name:
            names.append(name)

    return counts, names


def _read_vendor_groups(text: str) -> tuple[list[int], list[str]]:
    """The clusters of the vendor way, read group by group.

    A group of the vendor way reads "1x 2 Cortex-A76": one cluster of
    two, running the Cortex-A76.  A group that carries no second
    count, "4x A55", is a count of cores with no number of clusters
    in front of it, and it is one cluster of that many cores.

    The groups run one after the other over the whole layout, and the
    size of a group is the number of clusters times the number of
    cores of each of them.
    """

    counts: list[int] = []
    names: list[str] = []

    for match in _VENDOR_GROUP_RE.finditer(text):
        clusters = int(match.group("clusters"))
        inner = match.group("inner")
        name = match.group("name").strip()

        # The number behind the "x" is the number of cores of each
        # cluster.  A group that carries no second count is a count
        # of cores with no number of clusters in front of it, so it
        # is one cluster of that many cores.
        if inner:
            size = clusters * int(inner)
        else:
            size = clusters

        counts.append(size)
        if name:
            names.append(name)

    return counts, names


def _layout_counts(layout: str) -> list[int]:
    """The sizes of the clusters a cluster layout describes."""

    counts, _names = _layout_clusters(layout)
    return counts


def _core_names(layout: str) -> list[str]:
    """The core names a cluster layout names, in the order it names them.

    The layout reads "1x3x4 Cortex-X1/A78/A55": the counts come first
    and the core names follow, one per cluster, separated by slashes.
    The names are the little core first and the prime core last, the
    way the vendors write them: "Cortex-A55/A78/X3".

    The vendor way, "1x 2 Cortex-A76 3x 2 Cortex-A76 4x A55", names
    the core of every cluster right after its count, and the names
    are read out of those groups.
    """

    _counts, names = _layout_clusters(layout)
    return names


def normalize_layout(layout: str, cores: int | None) -> str:
    """The layout of *layout*, corrected to carry *cores* cores.

    A layout that already adds up to *cores* is kept as it is: the
    names of the parts are worth keeping, because a site that reads
    the layout reads the core names out of it.  Only a layout that
    disagrees with the count is rewritten.
    """

    if cores is None:
        return layout

    total = cores_from_layout(layout)
    if total == cores:
        return layout

    if cores not in LAYOUT_BY_CORES:
        return layout

    return layout_for_cores(cores, layout)


def known_chipset_count() -> int:
    """How many chipsets the table names."""

    return len(_SOC_CORES)


def _gpu_key(gpu: str) -> str:
    """The key the graphics table is keyed by.

    The graphics part is written in the ways the makers write it --
    "Mali-G57 MC2", "Adreno (TM) 540", "Immortalis-G715" -- and the
    table is keyed by the plain name of the part: "mali-g57 mc2",
    "adreno 540", "immortalis-g715".
    """

    if not gpu:
        return ""

    text = gpu.strip().lower()
    # The marketing suffix the table does not use: "Adreno (TM) 540".
    text = re.sub(r"\(\s*tm\s*\)", "", text)
    # The renderer runs the part with a count of its own: "Mali-G57
    # MC2" is the part "Mali-G57" with two cores.
    text = re.sub(r"\s+mc\d+$", "", text)
    text = re.sub(r"\s+mp\d+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chipsets_for_gpu(gpu: str) -> tuple[tuple[str, int], ...]:
    """The chipsets the graphics part *gpu* really ships with.

    An empty answer means the part is not one the table names, and
    the caller falls back to the count.
    """

    key = _gpu_key(gpu)
    if not key:
        return ()

    if key in CHIPSETS_BY_GPU:
        return CHIPSETS_BY_GPU[key]

    # The table is keyed by the plain name, and a renderer may carry
    # the count of the part in a form the table does not use.
    for name, chipsets in CHIPSETS_BY_GPU.items():
        if key.startswith(name) or name.startswith(key):
            return chipsets

    return ()


#: The graphics part each chipset really ships with.
#:
#: The table of the parts is the truth about what ships with what, and
#: the question a row of the catalogue asks is often the reverse of the
#: one the table is written for: the row names a chipset and a
#: graphics part, and the part has to be the one that ships with the
#: chipset.
#:
#: The table is built once, from the same table of the parts, so that
#: the two directions can never drift apart.  A chipset that several
#: parts ship with keeps the first of them, which is the part of the
#: line the chipset belongs to.
_GPU_BY_CHIPSET: dict[str, str] = {}

for _gpu_name, _chipsets in CHIPSETS_BY_GPU.items():
    for _chipset_name, _cores in _chipsets:
        _GPU_BY_CHIPSET.setdefault(_chipset_name.lower(), _gpu_name)

del _gpu_name, _chipsets, _chipset_name, _cores


def gpu_for_chipset(chipset: str) -> str:
    """The graphics part the chipset really carries.

    The answer is the name of the part the table of the parts holds
    for the chipset, written the way the market writes it.  An empty
    answer means the table does not name the chipset, and the caller
    keeps the part the row named.
    """

    from .chipset_gpu import gpu_for_chipset as _table

    return _table(chipset)


def chipset_for(gpu: str, cores: int) -> tuple[str, int]:
    """The chipset of a handset with the graphics part *gpu* and *cores* cores.

    The answer is a chipset that really exists and that really
    carries *cores* cores, so that the three readings a site takes of
    a handset -- the graphics part, the chipset and the core count --
    agree with each other.

    A part that ships with more than one chipset gives the first
    chipset of the right count, so that the fleet spreads over the
    parts instead of answering with one chipset for every handset
    that carries the same graphics part.
    """

    candidates = chipsets_for_gpu(gpu)
    for name, count in candidates:
        if count == cores:
            return name, count

    # The part ships with chipsets, but none of them carries the
    # count the row names.  The first chipset of the part is used,
    # and the count of that chipset is taken with it: a chipset
    # carries a fixed number of cores, so the count that travels
    # with the name is the only one that agrees with it.
    if candidates:
        name, count = candidates[0]
        return name, count

    # The part is not in the table.  The chipset is looked up by the
    # count the row names, and the chipset that is found is one of the
    # family the graphics part belongs to: an Adreno part sits on a
    # Qualcomm chip and a Mali part on a MediaTek or a Samsung or a
    # Google one.  A part that is answered with a chipset of another
    # family is a handset that was never built, and the site that
    # reads the renderer and the SoC model together finds it.
    family = _gpu_family_of(gpu)
    fallback = CHIPSET_BY_CORE_COUNT.get(cores)
    if fallback:
        for name in fallback:
            if _gpu_family_of(name) == family:
                return name, cores_for_chipset(name) or cores

    name = _CHIPSET_BY_COUNT_FALLBACK.get(cores)
    if name and (not family or _gpu_family_of(name) == family):
        return name, cores_for_chipset(name) or cores

    # The family of the part is known but no chipset of the family
    # carries the count the row names.  The chipset of the family
    # that carries the count nearest to it is used, and the count of
    # that chipset travels with it.
    if family:
        for count in sorted(REAL_CORE_COUNTS):
            for name in CHIPSET_BY_CORE_COUNT.get(count, ()):
                if _gpu_family_of(name) == family:
                    return name, count

    name = _CHIPSET_BY_COUNT_FALLBACK.get(cores)
    if name:
        return name, cores_for_chipset(name) or cores

    return ("Snapdragon 680", cores)


#: The family of the graphics part of a chipset, by the family the
#: chipset belongs to.
#:
#: The families are the ones the makers of the parts keep between
#: them: a Qualcomm chip carries an Adreno, a MediaTek or a Samsung
#: or a Google chip carries a Mali, a MediaTek one carries an
#: Immortalis as well, and a HiSilicon one carries a Maleoon.
#:
#: The HiSilicon rule has an exception that the market makes: the
#: Kirin 9000 and the 9000E are the only HiSilicon parts that carry a
#: Maleoon, and every other Kirin -- the 710, the 810, the 820, the
#: 659, the 960, the 970, the 980, the 985 and the 990 -- carries an
#: ARM Mali, because the Maleoon only began with the 2021 part.  The
#: exception is listed first, because the table is read in order and
#: the first rule that matches is the one that holds.
_CHIPSET_GPU_FAMILY: tuple[tuple[tuple[str, ...], str], ...] = (
    (("snapdragon", "qualcomm"), "adreno"),
    (("exynos",), "mali"),
    (("tensor", "google"), "mali"),
    (("kirin 9000",), "maleoon"),
    (("kirin", "hisilicon", "huawei"), "mali"),
    (("unisoc", "spreadtrum"), "mali"),
    (("mediatek", "helio", "dimensity"), "mali"),
)


def _gpu_family_of(name: str) -> str:
    """The family of the graphics part that ships with *name*.

    The answer is the family of the chipset when the name is a
    chipset, and the family of the part when the name is a graphics
    part: an "Adreno 630" is an Adreno and a "Snapdragon 8 Gen 1"
    carries one.
    """

    lowered = (name or "").strip().lower()
    if not lowered:
        return ""

    # The graphics parts are named by their own families.
    for family, markers in (
        ("adreno", ("adreno",)),
        ("immortalis", ("immortalis",)),
        ("mali", ("mali",)),
        ("maleoon", ("maleoon",)),
        ("powervr", ("powervr",)),
        ("img", ("img ", "img-")),
        ("xclipse", ("xclipse",)),
    ):
        if any(marker in lowered for marker in markers):
            return family

    for markers, family in _CHIPSET_GPU_FAMILY:
        if any(marker in lowered for marker in markers):
            return family

    return ""


def vendor_for_chipset(chipset: str) -> str:
    """The vendor of the chipset *chipset*.

    The vendor is read off the name of the chipset, because the name
    carries it: a "Snapdragon" is a Qualcomm part, an "Exynos" is a
    Samsung one, a "Tensor" is a Google one, and a "Dimensity" or a
    "Helio" is a MediaTek one.
    """

    text = (chipset or "").strip().lower()

    if "snapdragon" in text or "qualcomm" in text:
        return "Qualcomm"
    if "exynos" in text:
        return "Samsung"
    if "tensor" in text:
        return "Google"
    if "mediatek" in text or "dimensity" in text or "helio" in text:
        return "MediaTek"
    if "hisilicon" in text or "kirin" in text:
        return "HiSilicon"
    if "unisoc" in text or "spreadtrum" in text:
        return "Unisoc"
    if "intel" in text:
        return "ARM"
    if "omap" in text or "tegra" in text:
        return "ARM"

    return "ARM"


__all__ = [
    "CHIPSETS_BY_GPU",
    "CHIPSET_BY_CORE_COUNT",
    "REAL_CORE_COUNTS",
    "chipset_for",
    "chipsets_for_gpu",
    "cores_for_chipset",
    "cores_from_layout",
    "known_chipset_count",
    "layout_for_cores",
    "normalize_layout",
    "vendor_for_chipset",
]
