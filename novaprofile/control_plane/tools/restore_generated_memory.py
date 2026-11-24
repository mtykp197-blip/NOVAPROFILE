"""Put the source memory configurations back into the generated catalogue.

The generated catalogue was widened: the memory of a line was replaced
by the arithmetic of the source -- every amount below the smallest one
named, plus four, eight and twelve above it -- so a handset that was
sold with two gigabytes was offered one, two, three and four.

Those amounts are not invented: they are what the specification the
catalogue was built from says.  A two gigabyte handset exists, and the
catalogue that names it is more useful than one that pretends the
handset was sold with four as well.

This script restores the memory of every preset from the version the
repository holds, and leaves the core counts alone -- those carry the
new rule, in which a chipset has the one core count it really has.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
TARGET = HERE / "novaprofile" / "core" / "generated_presets.py"
RELATIVE = "novaprofile/control_plane/novaprofile/core/generated_presets.py"

PRESET_ID = re.compile(r"preset_id='([^']+)'")


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
        if "ram_options=(" in line:
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
        print("the repository copy carries no memory configurations")
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
        if "ram_options=(" in line:
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
