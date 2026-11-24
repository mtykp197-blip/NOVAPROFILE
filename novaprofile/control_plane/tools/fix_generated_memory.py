"""Give every generated preset the memory sizes its line was sold in.

The generated catalogue carried memory amounts that no handset has ever
shipped with: one, two and three gigabytes, and configurations like
"four, six, eight, ten and twelve" that are the arithmetic of the
source rather than the configurations of a line.

The rule is the one the fleet reads its presets from, and it lives in
:mod:`novaprofile.core.device_catalog_market`: the amounts the line was
sold in, and the smaller ones it was also sold in, and nothing below
three gigabytes.

This script rewrites the ``ram_options`` of every preset in the
generated catalogue so that it holds that set.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TARGET = HERE / "novaprofile" / "core" / "generated_presets.py"

#: The memory sizes a handset is really sold with.
REAL_RAM_AMOUNTS: tuple[int, ...] = (3, 4, 6, 8, 10, 12, 16, 18, 24)

#: The smallest amount a handset of this fleet ships with.
SMALLEST_RAM = 3


def memory_options(values: tuple[int, ...]) -> tuple[int, ...]:
    """The memory sizes a line with these configurations is sold in."""

    amounts = sorted({int(value) for value in values if int(value) > 0})
    if not amounts:
        return (8,)
    lowest = max(amounts[0], SMALLEST_RAM)
    options = {amount for amount in amounts if amount >= SMALLEST_RAM}
    options.add(lowest)
    for candidate in REAL_RAM_AMOUNTS:
        if candidate <= lowest:
            options.add(candidate)
    return tuple(sorted(options))


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted: dict[int, tuple[int, ...]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "DevicePreset":
            continue
        for keyword in node.keywords:
            if keyword.arg != "ram_options":
                continue
            if not isinstance(keyword.value, ast.Tuple):
                continue
            values = []
            for element in keyword.value.elts:
                if isinstance(element, ast.Constant):
                    values.append(int(element.value))
            wanted[keyword.value.lineno] = memory_options(tuple(values))

    lines = source.splitlines(keepends=True)
    changed = 0
    for lineno, options in wanted.items():
        index = lineno - 1
        if index >= len(lines):
            continue
        line = lines[index]
        rendered = ", ".join(str(value) for value in options)
        new_line, substitutions = re.subn(
            r"ram_options=\([^)]*\)", f"ram_options=({rendered},)", line
        )
        if substitutions:
            lines[index] = new_line
            changed += 1

    TARGET.write_text("".join(lines), encoding="utf-8")
    print(f"rewrote the memory of {changed} presets in {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
