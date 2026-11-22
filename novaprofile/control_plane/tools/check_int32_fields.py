"""Find the profile fields the browser core cannot read.

The browser core reads its configuration out of a JSON document with a
32-bit number reader: a value that does not fit in a signed 32-bit
integer is not an integer to it, and the whole profile is then refused.

This script walks a generated profile and reports every field whose
value does not fit, so that the boundary is known rather than
discovered by a browser that refuses to start.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


def walk(value: Any, path: str, found: list[str]) -> None:
    """Report every integer in a document that does not fit in 32 bits."""

    if isinstance(value, dict):
        for key, item in value.items():
            walk(item, f"{path}.{key}" if path else str(key), found)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            walk(item, f"{path}[{index}]", found)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int) and not (INT32_MIN <= value <= INT32_MAX):
        found.append(f"{path} = {value}")


def main() -> int:
    """Report the fields that do not fit."""

    control_plane = Path(__file__).resolve().parents[1]
    if str(control_plane) not in sys.path:
        sys.path.insert(0, str(control_plane))

    from novaprofile.core.devices import all_presets
    from novaprofile.core.generator import ProfileGenerator

    presets = all_presets()
    generator = ProfileGenerator()

    failures: list[str] = []
    for index, preset in enumerate(presets[:200]):
        config = generator.generate(preset_id=preset.preset_id, stratum=index)
        document = dict(config.model_dump(mode="json"))
        found: list[str] = []
        walk(document, "", found)
        failures.extend(f"{preset.preset_id}: {item}" for item in found)

    if failures:
        print(f"{len(failures)} fields do not fit in a 32-bit integer:")
        for item in failures[:40]:
            print(f"  {item}")
        return 1

    print("every field of every profile fits in a 32-bit integer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
