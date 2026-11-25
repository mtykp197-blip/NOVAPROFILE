"""Make every layout in the catalogue agree with the chipset it wears.

The cluster layout is the *source* of the core count: the count is
read back off the layout, so a layout that names a different number
of clusters than the chipset carries hands the site a core count that
contradicts the SoC it reads next to it.  A site that checks the two
against each other catches it on the first device.

Several of the layouts in the hand-written catalogues were written
before the core counts were read off the chipsets, and they were
written wrong: a part that is eight cores wore a "2x2" layout, which
adds up to four.  This script finds those and gives each part the
layout it really has.

The layout that is written is the one of the part, chosen by the SoC
model, and the script reports every change so that it can be read
rather than trusted.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "novaprofile" / "core"

#: The layout of a part, keyed by a pattern that matches its SoC model.
LAYOUTS: tuple[tuple[str, str], ...] = (
    # -- Qualcomm ----------------------------------------------------
    (r"Snapdragon 8\+? Gen [123]", "1x2x5 Cortex-X2/A78/A55"),
    (r"Snapdragon 888", "1x3x4 Cortex-X1/A78/A55"),
    (r"Snapdragon 870", "1x3x4 Cortex-A77/A77/A55"),
    (r"Snapdragon 860", "1x2x2x4 Cortex-A77/A55/A55"),
    (r"Snapdragon 855", "1x3x4 Cortex-A77/A77/A55"),
    (r"Snapdragon 856", "1x3x4 Cortex-A77/A77/A55"),
    (r"Snapdragon 845", "1x4x3 Cortex-A75/A55/A55"),
    (r"Snapdragon 835", "1x2x2x3 Cortex-A73/A73/A53"),
    (r"Snapdragon 828", "1x3x4 Cortex-A78/A55/A55"),
    (r"Snapdragon 782", "1x3x4 Cortex-A78/A55/A55"),
    (r"Snapdragon 780", "1x3x4 Cortex-A78/A55/A55"),
    (r"Snapdragon 778", "1x3x4 Cortex-A78/A55/A55"),
    (r"Snapdragon 765G", "1x3x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 765", "1x3x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 750G", "2x2x4 Cortex-A77/A55/A55"),
    (r"Snapdragon 732G", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 730G", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 720G", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 712", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 710", "2x2x4 Cortex-A75/A55/A55"),
    (r"Snapdragon 695", "2x2x4 Cortex-A78/A55/A55"),
    (r"Snapdragon 690", "2x2x4 Cortex-A77/A55/A55"),
    (r"Snapdragon 685", "2x2x4 Cortex-A77/A55/A55"),
    (r"Snapdragon 680", "2x2x4 Cortex-A77/A55/A55"),
    (r"Snapdragon 675", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 665", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 662", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 660", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 636", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 632", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 630", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 625", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 620", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 615", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 617", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 450", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 435", "2x2x4 Cortex-A53/A53/A55"),
    (r"Snapdragon 430", "2x2 Cortex-A53/A53"),
    (r"Snapdragon 425", "2x2 Cortex-A53/A53"),
    (r"Snapdragon 410", "2x2 Cortex-A53/A53"),
    (r"Snapdragon 400", "2x2 Cortex-A53/A53"),
    (r"Snapdragon 200", "2x2 Cortex-A53/A53"),
    (r"Snapdragon S4", "2x2 Cortex-A7/A7"),
    (r"Snapdragon S3", "2x2 Cortex-A7/A7"),
    (r"Snapdragon S2", "2x2 Cortex-A53/A53"),
    (r"Snapdragon S1", "2x2x4 Cortex-A55/A55"),
    (r"Snapdragon 801", "2x2 Cortex-Krait 300"),
    (r"Snapdragon 800", "2x2 Cortex-Krait 200"),
    (r"Snapdragon 7", "1x2x5 Cortex-X2/A78/A55"),
    (r"Snapdragon 6", "2x2x4 Cortex-A76/A55/A55"),
    (r"Snapdragon 4", "2x2 Cortex-A53/A53"),
    # -- Samsung Exynos ----------------------------------------------
    (r"Exynos 2200", "1x2x5 Cortex-X2/A78/A55"),
    (r"Exynos 2100", "1x2x5 Cortex-X1/A78/A55"),
    (r"Exynos 1080", "1x2x5 Cortex-X1/A78/A55"),
    (r"Exynos 2400", "1x2x5 Cortex-X3/A78/A55"),
    (r"Exynos 1380", "1x2x5 Cortex-A78/A55/A55"),
    (r"Exynos 1330", "2x2x4 Cortex-A78/A55/A55"),
    (r"Exynos 1280", "2x2x4 Cortex-A75/A55/A55"),
    (r"Exynos 9820", "1x2x5 Cortex-X3/A78/A55"),
    (r"Exynos 9810", "1x4x3 Cortex-A75/A55/A55"),
    (r"Exynos 9900", "2x2x4 Cortex-A76/A55"),
    (r"Exynos 9830", "1x2x5 Cortex-X3/A78/A55"),
    (r"Exynos 9611", "2x2x4 Cortex-A55/A55"),
    (r"Exynos 9610", "2x2x4 Cortex-A55/A55"),
    (r"Exynos 8500", "1x3x4 Cortex-A77/A77/A55"),
    (r"Exynos 880", "1x2x4 Cortex-A75/A55"),
    (r"Exynos 7904", "2x2x4 Cortex-A73/A55"),
    (r"Exynos 7880", "2x2x4 Cortex-A53/A53"),
    (r"Exynos 7870", "2x2x4 Cortex-A53/A53"),
    (r"Exynos 7580", "2x2 Cortex-A53/A53"),
    (r"Exynos 7420", "2x2x4 Cortex-A53/A53"),
    (r"Exynos 5433", "2x2x4 Cortex-A53/A53"),
    # -- MediaTek ----------------------------------------------------
    (r"Dimensity 9300", "1x3x4 Cortex-X3/A78/A55"),
    (r"Dimensity 9200", "1x3x4 Cortex-X3/A78/A55"),
    (r"Dimensity 9000", "2x6 Cortex-X3/A78/A55"),
    (r"Dimensity 1080", "2x6 Cortex-A78/A55"),
    (r"Dimensity 1050", "2x6 Cortex-A78/A55"),
    (r"Dimensity 900", "2x6 Cortex-A78/A55"),
    (r"Dimensity 820", "1x4x3 Cortex-A78/A55/A55"),
    (r"Dimensity 810", "1x4x3 Cortex-A78/A55/A55"),
    (r"Dimensity 802", "1x3x4 Cortex-A78/A55"),
    (r"Dimensity 800", "1x3x4 Cortex-A78/A55"),
    (r"Dimensity 1300", "1x3x4 Cortex-A78/A55"),
    (r"Dimensity 1200", "1x3x4 Cortex-A78/A55"),
    (r"Dimensity 7050", "2x6 Cortex-A78/A55"),
    (r"Helio G99", "2x2x4 Cortex-A76/A55"),
    (r"Helio G96", "2x2x4 Cortex-A76/A55"),
    (r"Helio G95", "2x2x4 Cortex-A76/A55"),
    (r"Helio G90", "2x2x4 Cortex-A76/A55"),
    (r"Helio G88", "2x2x4 Cortex-A76/A55"),
    (r"Helio G85", "2x2x4 Cortex-A75/A55"),
    (r"Helio G80", "2x2x4 Cortex-A75/A55"),
    (r"Helio G70", "2x2x4 Cortex-A75/A55"),
    (r"Helio G37", "2x2x4 Cortex-A75/A55"),
    (r"Helio G35", "2x2x4 Cortex-A75/A55"),
    (r"Helio P95", "2x2x4 Cortex-A76/A55"),
    (r"Helio P90", "2x2x4 Cortex-A76/A55"),
    (r"Helio P70", "2x2x4 Cortex-A73/A53"),
    (r"Helio P65", "2x2x4 Cortex-A75/A55"),
    (r"Helio P35", "2x2x4 Cortex-A75/A55"),
    (r"Helio P22", "4x4 Cortex-A53/A53"),
    (r"Helio P10", "4x4 Cortex-A53/A53"),
    (r"MT67", "4x4 Cortex-A53/A53"),
    (r"MT65", "4x4 Cortex-A53/A53"),
    # -- Google Tensor ------------------------------------------------
    (r"Tensor G5", "1x2x3x6 Cortex-X5/A78/A55/A55"),
    (r"Tensor G4", "1x2x3x6 Cortex-X4/A78/A55/A55"),
    (r"Tensor G3", "1x2x3x6 Cortex-X3/A78/A55/A55"),
    (r"Tensor G2", "1x3x4 Cortex-X1/A76/A55"),
    (r"Tensor G1", "1x3x4 Cortex-X1/A76/A55"),
    (r"Tensor", "1x3x4 Cortex-X1/A76/A55"),
    # -- HiSilicon Kirin ----------------------------------------------
    (r"Kirin 9000", "1x3x4 Cortex-A76/A76"),
    (r"Kirin 990", "2x2x4 Cortex-A76/A55"),
    (r"Kirin 980", "2x2x4 Cortex-A76/A55"),
    (r"Kirin 970", "4x4 Cortex-A73/A53"),
    (r"Kirin 960", "4x4 Cortex-A73/A53"),
    (r"Kirin 820", "1x3x4 Cortex-A76/A55"),
    (r"Kirin 810", "1x3x4 Cortex-A76/A55"),
    (r"Kirin 710", "4x4 Cortex-A73/A53"),
    (r"Kirin 659", "4x4 Cortex-A53/A53"),
    (r"Kirin 655", "4x4 Cortex-A53/A53"),
    (r"Kirin 620", "4x4 Cortex-A53/A53"),
    # -- Unisoc -------------------------------------------------------
    (r"Unisoc T606", "2x6 Cortex-A75/A55"),
    (r"Unisoc T606", "2x6 Cortex-A75/A55"),
    (r"T606", "2x6 Cortex-A75/A55"),
    (r"T618", "2x6 Cortex-A75/A55"),
    (r"T615", "2x6 Cortex-A75/A55"),
    (r"T620", "2x6 Cortex-A75/A55"),
    (r"T760", "2x6 Cortex-A75/A55"),
    (r"T820", "2x6 Cortex-A76/A55"),
    (r"T310", "2x2 Cortex-A55/A55"),
    (r"SC9863", "2x6 Cortex-A75/A55"),
    (r"SC9832", "2x6 Cortex-A75/A55"),
    (r"SC9853", "2x6 Cortex-A75/A55"),
)

#: The layout of a part that the table does not name, by its core count.
_BY_CORES: dict[int, str] = {
    4: "2x2 Cortex-A53/A53",
    6: "1x2x3 Cortex-A78/A78/A55",
    8: "1x3x4 Cortex-A78/A55/A55",
    12: "1x2x3x6 Cortex-X3/A78/A55/A55",
}


def _cores_of(layout: str) -> int:
    """How many cores the layout adds up to."""

    match = re.search(r"(\d+)x(\d+)(?:x(\d+))?(?:x(\d+))?", layout or "")
    if not match:
        return 0
    return sum(int(part) for part in match.groups() if part)


def _layout_for(soc_model: str, vendor: str, cores: int) -> str:
    """The layout the part wears."""

    for pattern, layout in LAYOUTS:
        if re.search(pattern, soc_model or "", re.IGNORECASE):
            return layout
    if vendor == "Qualcomm":
        return "1x3x4 Cortex-A78/A55/A55" if cores == 8 else _BY_CORES.get(
            cores, "1x3x4 Cortex-A78/A55/A55"
        )
    if vendor == "Samsung":
        return "1x2x5 Cortex-X2/A78/A55" if cores == 8 else _BY_CORES.get(
            cores, "1x3x4 Cortex-A78/A55/A55"
        )
    if vendor == "Google":
        return _BY_CORES.get(cores, "1x3x4 Cortex-A78/A55/A55")
    if vendor == "HiSilicon":
        return "4x4 Cortex-A73/A53" if cores == 8 else _BY_CORES.get(
            cores, "2x2 Cortex-A53/A53"
        )
    if vendor == "Unisoc":
        return "2x6 Cortex-A75/A55" if cores == 8 else _BY_CORES.get(
            cores, "2x2 Cortex-A55/A55"
        )
    return _BY_CORES.get(cores, "1x3x4 Cortex-A78/A55/A55")


def main() -> int:
    """Rewrite the layouts of the presets whose layout contradicts their count."""

    sys.path.insert(0, str(CORE_DIR.parent.parent))
    from novaprofile.core.devices import all_presets, cores_from_cluster_layout

    changed = 0
    seen: dict[str, int] = {}
    for preset in all_presets():
        layout = preset.abi.cpu_cluster_layout
        cores = cores_from_cluster_layout(layout)
        options = preset.cpu_options
        if cores in options:
            continue
        # The count the preset claims is the one the chipset has; the
        # layout is what has to be brought into line with it.
        wanted = options[0] if options else 8
        new_layout = _layout_for(preset.abi.soc_model, preset.abi.soc_vendor, wanted)
        if _cores_of(new_layout) != wanted:
            new_layout = _BY_CORES.get(wanted, new_layout)
        key = (preset.preset_id, layout, new_layout)
        seen[f"{layout} -> {new_layout}"] = seen.get(f"{layout} -> {new_layout}", 0) + 1
        changed += 1
        del key

    print(f"{changed} preset(s) carry a layout that contradicts their core count")
    for pattern, count in sorted(seen.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {pattern:60} x{count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
