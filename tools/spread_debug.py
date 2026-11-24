"""Compare the score of the two contenders at the round where the choice differs."""

import sys

sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\control_plane")

from novaprofile.core.devices import all_presets
from novaprofile.core.fleet import (
    _READS,
    _WEIGHTS,
    _distinct_machines,
    _core_count,
    _memory_value,
    _touch_points,
)
import collections

presets = all_presets()
candidates = _distinct_machines(presets)
by_id = {p.preset_id: i for i, p in enumerate(candidates)}

values = []
for preset in candidates:
    values.append(
        (
            preset.gpu.unmasked_renderer,
            preset.model,
            preset.build_id,
            preset.screen.width,
            preset.screen.height,
            _core_count(preset),
            _memory_value(preset),
            _touch_points(preset),
            preset.screen.device_pixel_ratio,
        )
    )

rarity = {}
for row in values:
    for value in row:
        rarity[value] = rarity.get(value, 0) + 1


def score_of(index, buckets):
    total = 0.0
    tie = 0
    for read, weight in _WEIGHTS.items():
        key = tuple(values[index][i] for i in read)
        size = buckets[read][key] + 1
        total += weight * size * size
        tie += sum(rarity.get(values[index][i], 0) for i in read)
    return total, tie


# Rebuild the baseline's first 45 rounds with the original algorithm.
buckets = {read: collections.Counter() for read in _READS}
chosen = []
for _round in range(45):
    best = None
    for index in range(len(candidates)):
        total, tie = score_of(index, buckets)
        if best is None or total < best[0]:
            best = (total, tie, index)
        elif total == best[0] and best[1] is not None and tie < best[1]:
            best = (total, tie, index)
    row = values[best[2]]
    for read in _READS:
        key = tuple(row[i] for i in read)
        buckets[read][key] += 1
    chosen.append(best[2])

a = by_id["google-pixel-5-landscape"]
b = by_id["samsung-galaxy-tab-s9-landscape"]
print("pixel-5-landscape :", score_of(a, buckets))
print("tab-s9-landscape   :", score_of(b, buckets))
print("baseline round 45 picked pixel-5-landscape")
