"""Which readings of a profile do not vary at all?

Every reading a site can read falls into one of two classes, and the
whole product rests on telling them apart:

* **The bounded ones.**  The Android release, the graphics part, the
  memory, the core count, the panel -- these have a limited set of
  true values, and a unit picks one of them.  They cannot be unique per
  unit, and they are not meant to be: a hundred million handsets do not
  ship with a hundred million Android releases.

* **The unique ones.**  The serial, the build fingerprint, the build
  number, the canvas, the audio, the graphics noise -- these have to
  differ for every unit, or the fleet is one device.

The dangerous reading is the third kind: a field that *looks* like it
varies but does not.  A field that is the same in every profile of the
fleet is a hole in the signature -- it contributes nothing, and worse,
it makes the fields around it look like they separate.

This module finds those fields.  It builds a fleet, walks every field
of every profile, and reports the ones that never change.

The report is not a failure on its own -- a reading that is the same in
every profile of the fleet may still be the truth about the hardware
(the colour depth is twenty-four on every Android panel).  What matters
is that the *fields that are supposed to differ* do differ, and that
this script names the ones that do not.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from novaprofile.core.fleet import generate_fleet  # noqa: E402


def _walk(value: Any, prefix: str) -> dict[str, Any]:
    """Every leaf of a value, by its path."""

    if isinstance(value, dict):
        leaves: dict[str, Any] = {}
        for key, item in value.items():
            leaves.update(_walk(item, f"{prefix}.{key}" if prefix else str(key)))
        return leaves
    if isinstance(value, (list, tuple)):
        leaves = {}
        for index, item in enumerate(value):
            leaves.update(_walk(item, f"{prefix}[{index}]"))
        return leaves
    return {prefix: value}


def _jsonable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def main(argv: list[str]) -> int:
    count = 200
    if len(argv) > 1:
        count = int(argv[1])

    profiles = generate_fleet(count=count, vietnam=True)
    print(f"fleet of {len(profiles)} profiles")

    # The path -> the values it takes, over the fleet.
    values: dict[str, list[str]] = {}
    for profile in profiles:
        dumped = profile.model_dump(mode="json")
        for path, value in _walk(dumped, "").items():
            values.setdefault(path, []).append(_jsonable(value))

    # The paths that never change.
    constant = [
        (path, vals[0])
        for path, vals in values.items()
        if len(set(vals)) == 1
    ]

    # The paths that change, and how much.
    varying = [
        (path, len(set(vals)))
        for path, vals in values.items()
        if len(set(vals)) > 1
    ]

    print()
    print(f"=== {len(constant)} paths that never change over the fleet ===")
    for path, value in sorted(constant):
        print(f"  {path:56s} {value[:70]}")

    print()
    print(f"=== {len(varying)} paths that change ===")
    for path, distinct in sorted(varying, key=lambda item: item[1]):
        print(f"  {path:56s} {distinct} distinct values")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
