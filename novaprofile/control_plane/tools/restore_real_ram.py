"""Restore the real memory configurations of the hand-written presets.

The catalogue of the reference devices was written by hand, and the
memory it names is the memory those handsets were sold in: a Pixel 8
Pro is an eight and a twelve gigabyte phone, and there is no two, three
or twenty gigabyte version of it.

The widening script rewrote those facts into a set that walks up and
down from the smallest amount by four, eight and twelve and adds every
smaller amount below it.  That produced a Pixel 8 Pro offered in two,
three, four, six, eight, twelve, sixteen and twenty gigabytes -- a
handset that no shop has ever sold, and a set of readings that a page
can check against the model name it already knows.

This script puts the real configurations back.  The presets are paired
by their identifier, so the memory of one handset can never land on
another.

The core counts are left alone: they are already the ones the chipset
reports.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
TARGET = HERE / "novaprofile" / "core" / "devices.py"
RELATIVE = "novaprofile/control_plane/novaprofile/core/devices.py"

PRESET_ID = re.compile(r'preset_id="([^"]+)"')
RAM_LINE = re.compile(r"ram_options=\([^)]*\),")


def memory_of(source: str) -> dict[str, str]:
    """The memory configuration of every preset, keyed by its identifier."""

    options: dict[str, str] = {}
    current: str | None = None
    for line in source.splitlines():
        match = PRESET_ID.search(line)
        if match:
            current = match.group(1)
            continue
        if current is None:
            continue
        if RAM_LINE.search(line):
            options[current] = line.strip()
            current = None
    return options


def main() -> int:
    original = subprocess.run(
        ["git", "show", f"HEAD:{RELATIVE}"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    wanted = memory_of(original)
    if not wanted:
        print("the original catalogue carries no memory configurations")
        return 1

    lines = TARGET.read_text(encoding="utf-8").splitlines(keepends=True)
    current: str | None = None
    restored = 0
    for index, line in enumerate(lines):
        match = PRESET_ID.search(line)
        if match:
            current = match.group(1)
            continue
        if current is None:
            continue
        if RAM_LINE.search(line):
            replacement = wanted.get(current)
            if replacement is not None:
                lines[index] = replacement + "\n"
                restored += 1
            current = None

    TARGET.write_text("".join(lines), encoding="utf-8")
    print(f"restored the memory of {restored} presets in {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
