"""Reproduce the internal scoring of the new algorithm for comparison."""

import collections
import heapq
import sys

sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\control_plane")

from novaprofile.core.devices import all_presets
from novaprofile.core.fleet import (
    _READS,
    _WEIGHTS,
    _core_count,
    _distinct_machines,
    _memory_value,
    _scaled_weight,
    _touch_points,
)

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

scored = []
for row in values:
    entries = []
    for read, weight in _WEIGHTS.items():
        key = tuple(row[i] for i in read)
        tie = sum(rarity.get(row[i], 0) for i in read)
        entries.append((read, weight, key, tie))
    scored.append(tuple(entries))

scores = []
for entries in scored:
    total = 0
    for _read, weight, _key, _tie in entries:
        total += _scaled_weight(weight)
    scores.append(total)

sharing = {read: collections.defaultdict(list) for read in _READS}
for index, entries in enumerate(scored):
    for read, _w, key, _t in entries:
        sharing[read][key].append(index)

buckets = {read: collections.Counter() for read in _READS}
heap = []


def tie_of(index):
    return sum(entry[3] for entry in scored[index])


for index in range(len(candidates)):
    heapq.heappush(heap, (scores[index], tie_of(index), index))

chosen = []
for round_index in range(50):
    best_index = -1
    while heap:
        score, tie, index = heapq.heappop(heap)
        if score != scores[index]:
            continue
        best_index = index
        break
    if round_index == 45:
        a = by_id["google-pixel-5-landscape"]
        b = by_id["samsung-galaxy-tab-s9-landscape"]
        print("internal score pixel-5 :", scores[a], "x4 =", scores[a] / 4)
        print("internal score tab-s9   :", scores[b], "x4 =", scores[b] / 4)
        print("picked:", candidates[best_index].preset_id)
        break
    row = values[best_index]
    for read in _READS:
        key = tuple(row[i] for i in read)
        counter = buckets[read]
        size = counter[key]
        counter[key] = size + 1
        delta = (size + 1) * (size + 1) - size * size
        weight = _scaled_weight(_WEIGHTS[read])
        for other in sharing[read][key]:
            scores[other] += weight * delta
            heapq.heappush(heap, (scores[other], tie_of(other), other))
    chosen.append(best_index)
