"""Give every generated preset the core count of its own chipset.

The generated catalogue widened every chipset to the neighbouring
*real* core counts, so a Snapdragon 8 Gen 1 -- eight cores and always
eight cores -- was offered as a four-core and a twelve-core handset as
well.  A site that reads the SoC model and the core count out of one
record finds the contradiction immediately, and the contradiction sits
in the two fields next to each other.

This script rewrites the ``cpu_options`` of every preset in the
generated catalogue so that it holds the core count of the chipset the
preset names.  Where the chipset names a count, that is the count; a
chipset whose name carries no count is read off its cluster layout.

The file is parsed rather than patched with a regular expression
because a preset's ``cpu_options`` line is not tied to its ``abi``
line by any fixed distance, and pairing the wrong two would put a
handset's core count on another handset's chipset.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TARGET = HERE / "novaprofile" / "core" / "generated_presets.py"

#: The core counts a chip designer actually ships.
REAL_CORE_COUNTS: tuple[int, ...] = (4, 6, 8, 12)

#: A chipset that names its core count.
NAMED_COUNT = re.compile(r"(\d+)\s*[- ]?\s*core", re.IGNORECASE)

#: The layout that gives the count when the name does not.
CLUSTER_TOTAL = re.compile(r"(\d+)\s*x\s*(\d+)\s*x\s*(\d+)", re.IGNORECASE)


def nearest_real(value: int) -> int:
    """The core count a chip designer actually ships, near ``value``."""

    if value in REAL_CORE_COUNTS:
        return value
    return min(REAL_CORE_COUNTS, key=lambda candidate: (abs(candidate - value), candidate))


def core_count_for(soc_model: str, cluster_layout: str) -> int:
    """The core count a chipset of this model reports."""

    match = NAMED_COUNT.search(soc_model or "")
    if match:
        value = int(match.group(1))
        if 2 <= value <= 16:
            return nearest_real(value)

    match = CLUSTER_TOTAL.search(cluster_layout or "")
    if match:
        total = sum(int(part) for part in match.groups())
        if 2 <= total <= 16:
            return nearest_real(total)

    return 8


def soc_and_layout(call: ast.Call) -> tuple[str, str]:
    """The SoC model and the cluster layout of an ``AndroidAbiSoc`` call."""

    model = ""
    layout = ""
    for keyword in call.keywords:
        if keyword.arg == "soc_model" and isinstance(keyword.value, ast.Constant):
            model = str(keyword.value.value)
        elif keyword.arg == "cpu_cluster_layout" and isinstance(
            keyword.value, ast.Constant
        ):
            layout = str(keyword.value.value)
    return model, layout


def _abi_of(value: ast.expr) -> tuple[str, str]:
    """The SoC model and the cluster layout of an ``abi`` keyword."""

    if not isinstance(value, ast.Call):
        return "", ""
    func = value.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name != "AndroidAbiSoc":
        return "", ""
    return soc_and_layout(value)


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # The line of the ``cpu_options=`` of every preset that carries one,
    # together with the core count that preset should report.
    #
    # The preset and its chipset are two keywords of the same
    # ``DevicePreset`` call, so the call is what pairs them: the
    # ``abi`` keyword names the chipset and the ``cpu_options``
    # keyword carries the count.
    wanted: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "DevicePreset":
            continue

        model = ""
        layout = ""
        options_line = None
        for keyword in node.keywords:
            if keyword.arg == "abi":
                model, layout = _abi_of(keyword.value)
            elif keyword.arg == "cpu_options":
                if isinstance(keyword.value, ast.Tuple):
                    options_line = keyword.value.lineno

        if not model or options_line is None:
            continue
        wanted[options_line] = core_count_for(model, layout)

    lines = source.splitlines(keepends=True)
    changed = 0
    for lineno, count in wanted.items():
        index = lineno - 1
        if index >= len(lines):
            continue
        line = lines[index]
        new_line, substitutions = re.subn(
            r"cpu_options=\([^)]*\)", f"cpu_options=({count},)", line
        )
        if substitutions:
            lines[index] = new_line
            changed += 1

    TARGET.write_text("".join(lines), encoding="utf-8")
    print(f"rewrote the core count of {changed} presets in {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
