"""Capture the current ``_spread_machines`` output for a regression check."""

import json
import sys

sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\control_plane")

from novaprofile.core.devices import all_presets
from novaprofile.core.fleet import _distinct_machines, _spread_machines

presets = all_presets()
candidates = _distinct_machines(presets)
print("distinct candidates:", len(candidates))

for count in (50, 200, 1000):
    chosen = _spread_machines(presets, count)
    ids = [p.preset_id for p in chosen]
    print(count, "chosen:", len(ids), "digest:", hash(tuple(ids)))
    with open(rf"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\.spread_{count}.json", "w", encoding="utf-8") as handle:
        json.dump(ids, handle)
