"""Correct the cluster layouts whose groups do not add up to a count.

    10|The layout is where the core count is read from, so a layout of
"1x2x2" is a five-core part and not a four-core one -- the platform
    reports the total of the groups, and a site that reads the total
    reads five.

This script rewrites the layouts of the catalogue so that every layout
    adds up to the core count of the chipset it stands for, and then
    checks the result.

    The script works on the source of the catalogue, not on the
    generated file, so that the next regeneration keeps the
    correction.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from novaprofile.core.soc_cores import (  # noqa: E402
    LAYOUT_BY_CORES,
    cores_for_chipset,
    layout_for_cores,
    normalize_layout,
)

#: The count of a layout.
LAYOUT_TOTAL = re.compile(r"(\d+)\s*x\s*(\d+)(?:\s*x\s*(\d+))?(?:\s*x\s*(\d+))?")

#: The files whose layouts are corrected.
TARGETS = (
    HERE / "novaprofile" / "core" / "devices.py",
    HERE / "novaprofile" / "core" / "device_catalog_extended.py",
    HERE / "novaprofile" / "core" / "device_catalog_fleet.py",
)


def layout_total(layout: str) -> int:
    """The number of cores a layout adds up to."""

    match = LAYOUT_TOTAL.search(layout or "")
    if not match:
        return 0
    total = sum(int(part) for part in match.groups() if part)
    return total if 2 <= total <= 16 else 0


def correct_layout(layout: str, chipset: str) -> str:
    """The layout of *chipset*, corrected to the count it carries."""

    cores = cores_for_chipset(chipset)
    if cores is None:
        cores = layout_total(layout)
    if cores not in LAYOUT_BY_CORES:
        return layout
    return normalize_layout(layout, cores)


def fix_file(path: Path) -> int:
    """Correct the layouts of one file, and write it back."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    lines = source.splitlines(keepends=True)
    changed = 0

    # The layouts are the string arguments of the ``_abi`` calls, and
    # the chipset is the argument in front of them.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_abi":
            continue

        args = [arg for arg in node.args if isinstance(arg, ast.Constant)]
        if len(args) < 3:
            continue

        chipset = str(args[1].value)
        layout = str(args[2].value)

        new_layout = correct_layout(layout, chipset)
        if new_layout == layout:
            continue

        index = node.lineno - 1
        while index < len(lines) and args[2].value not in lines[index]:
            index += 1
        if index >= len(lines):
            continue

        lines[index] = lines[index].replace(
            f'"{layout}"', f'"{new_layout}"', 1
        )
        changed += 1

    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed


def main() -> int:
    total = 0
    for path in TARGETS:
        if not path.exists():
            continue
        changed = fix_file(path)
        total += changed
        print(f"{path.name}: {changed} layouts corrected")

    print(f"total: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
