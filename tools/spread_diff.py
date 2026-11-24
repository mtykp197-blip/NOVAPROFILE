"""Find the first round where the new selection differs from the baseline."""

import json
import sys

sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\control_plane")

from novaprofile.core.devices import all_presets
from novaprofile.core.fleet import _distinct_machines, _spread_machines

presets = all_presets()
candidates = _distinct_machines(presets)
by_id = {p.preset_id: i for i, p in enumerate(candidates)}

with open(r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\.spread_50.json", encoding="utf-8") as handle:
    baseline = json.load(handle)

new = [p.preset_id for p in _spread_machines(presets, 50)]

for i in range(50):
    if new[i] != baseline[i]:
        print("round", i)
        print("  baseline:", baseline[i], "index", by_id.get(baseline[i]))
        print("  new     :", new[i], "index", by_id.get(new[i]))
        print("  baseline tail:", baseline[i:])
        print("  new tail     :", new[i:])
        break
