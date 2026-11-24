"""Compare the new ``_spread_machines`` against the recorded baseline."""

import json
import sys

sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\control_plane")

from novaprofile.core.devices import all_presets
from novaprofile.core.fleet import _spread_machines

presets = all_presets()

for count in (50, 200, 1000):
    with open(rf"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\.spread_{count}.json", encoding="utf-8") as handle:
        baseline = json.load(handle)
    chosen = [p.preset_id for p in _spread_machines(presets, count)]
    same = chosen == baseline
    print(count, "match:", same, "len:", len(chosen))
    if not same:
        for i, (a, b) in enumerate(zip(chosen, baseline)):
            if a != b:
                print("  first difference at", i, a, "!=", b)
                break
