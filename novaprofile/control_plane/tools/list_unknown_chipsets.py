"""List the chipsets of the catalogue that the table does not name.

    10|A chipset the table does not name falls back to the count the
cluster layout carries, and a layout that was written by hand is
    often the wrong count for the chip: the catalogue says the handset
    has an eight-core part, and the layout next to it says four.

This script prints the chipsets that are missing, with how many
    presets use them, so that the table can be filled in.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from novaprofile.core.devices import DEVICE_PRESETS  # noqa: E402
from novaprofile.core.soc_cores import cores_for_chipset  # noqa: E402


def _presets() -> list:
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
        except Exception:
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
    missing = collections.Counter()
    layouts = collections.defaultdict(collections.Counter)

    for preset in _presets():
        abi = getattr(preset, "abi", None)
        model = getattr(abi, "soc_model", "") or ""
        if not model:
            continue
        if cores_for_chipset(model) is None:
            missing[model] += 1
            layouts[model][getattr(abi, "cpu_cluster_layout", "")] += 1

    print(f"unknown chipsets: {len(missing)}, presets affected: {sum(missing.values())}")
    for model, count in missing.most_common():
        layout = ", ".join(layouts[model])
        print(f"  {count:>4}  {model}   [{layout}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
