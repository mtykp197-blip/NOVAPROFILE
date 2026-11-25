"""Check that every preset's core count agrees with its cluster layout.

The core count the platform reports and the cluster layout the SoC
carries sit next to each other in the record, and the count is read
back off the layout.  A preset whose two disagree hands a site a
handset whose core count contradicts its own chipset, which is the
easiest kind of tell there is.

    10|This script walks every preset in the catalogue -- the hand
written ones, the extended ones, the fleet ones and the generated
    ones -- and checks the two against each other, then prints the
    distribution of the counts so that a catalogue where everything
    came out as eight cores is visible at a glance.

The counts are also compared with the table of the chipsets the field
uses, so a chipset that the table knows is reported with the count
that chip really has.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from novaprofile.core.devices import (  # noqa: E402
    DEVICE_PRESETS,
    cores_from_cluster_layout,
)
from novaprofile.core.soc_cores import cores_for_chipset  # noqa: E402


def _presets() -> list:
    """Every preset of the catalogue, from every source."""

    seen: dict[str, object] = {}

    for preset in DEVICE_PRESETS:
        seen[preset.model] = preset

    for module_name in (
        "novaprofile.core.device_catalog_extended",
        "novaprofile.core.device_catalog_fleet",
        "novaprofile.core.generated_presets",
    ):
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as error:  # pragma: no cover - a source may be absent
            print(f"  (skipped {module_name}: {error})")
            continue
        for name in dir(module):
            value = getattr(module, name)
            if not isinstance(value, (list, tuple)):
                continue
            for item in value:
                model = getattr(item, "model", None)
                if isinstance(model, str):
                    seen.setdefault(model, item)

    return list(seen.values())


def main() -> int:
    presets = _presets()
    print(f"presets: {len(presets)}")

    contradictions: list[tuple[str, str, int, int]] = []
    unknown_chips = collections.Counter()
    distribution = collections.Counter()
    option_spread = collections.Counter()

    for preset in presets:
        abi = getattr(preset, "abi", None)
        layout = getattr(abi, "cpu_cluster_layout", "") or ""
        model_name = getattr(abi, "soc_model", "") or ""
        options = tuple(getattr(preset, "cpu_options", ()) or ())

        layout_cores = cores_from_cluster_layout(layout)
        chip_cores = cores_for_chipset(model_name)

        distribution[layout_cores] += 1
        option_spread[len(options)] += 1

        if options and layout_cores not in options:
            contradictions.append(
                (preset.model, layout, layout_cores, options)
            )

        if chip_cores is not None and chip_cores != layout_cores:
            contradictions.append(
                (f"{preset.model} [chip]", model_name, chip_cores, layout_cores)
            )
        elif chip_cores is None:
            unknown_chips[model_name] += 1

    print(f"core-count distribution: {dict(sorted(distribution.items()))}")
    print(f"cpu_options width: {dict(sorted(option_spread.items()))}")
    print(f"distinct unknown chipsets: {len(unknown_chips)}")

    if contradictions:
        print(f"CONTRADICTIONS: {len(contradictions)}")
        for row in contradictions[:40]:
            print(f"  {row}")
        return 1

    print("OK: every preset's core count agrees with its cluster layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
