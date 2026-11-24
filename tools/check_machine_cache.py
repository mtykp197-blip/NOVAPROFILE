"""Check how the machine count is cached.

The fleet planner asks for the capacity of every model, over and over.
The count is remembered, so the question is whether the remembering
actually works -- whether the models are keyed apart from each other.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.core.fleet import _distinct_machines, all_presets  # noqa: E402
from novaprofile.core.machine import count_machines  # noqa: E402


def main() -> int:
    candidates = _distinct_machines(all_presets())
    print(f"distinct machines: {len(candidates)}")

    ids = Counter(preset.preset_id for preset in candidates)
    shared = {key: value for key, value in ids.items() if value > 1}
    print(f"preset_ids used by more than one machine: {len(shared)}")
    for key, value in list(shared.items())[:5]:
        print(f"  {key}: {value}")

    started = time.time()
    first = [count_machines(preset) for preset in candidates]
    cold = time.time() - started

    started = time.time()
    second = [count_machines(preset) for preset in candidates]
    warm = time.time() - started

    print(f"cold pass: {cold:.2f}s")
    print(f"warm pass: {warm:.4f}s")
    print(f"total capacity: {sum(first):,}")
    print(f"second pass agrees: {first == second}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
