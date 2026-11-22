"""Cross-check the three new hardware rules over the whole fleet.

This script does not trust the validator: it walks the catalogue and the
generator directly and reports every preset that breaks one of the rules.

Rules checked:

1. every reported ``device_memory`` is one of 1, 2, 4, 8;
2. every reported ``hardware_concurrency`` is one of 4, 6, 8, 12;
3. no two presets share a ``soc_model`` while offering different
   ``cpu_options``;
4. every ``ram_options`` entry is a plausible physical amount, and the
   reported value is the Chromium approximation of it.

Run it with::

    python -m novaprofile.scripts.fleet_hardware_check
"""

from __future__ import annotations

import collections
import sys
from typing import Any

from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.hardware_approximation import (
    DEVICE_MEMORY_VALUES,
    HARDWARE_CONCURRENCY_VALUES,
    approximate_device_memory,
)

#: The physical RAM amounts a handset may be sold with.
PLAUSIBLE_RAM: frozenset[int] = frozenset({1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 24})

#: How many profiles to generate per preset.
PROFILES_PER_PRESET = 3


def check_catalogue(presets: list[Any]) -> list[str]:
    """Check the rules that live in the catalogue."""
    problems: list[str] = []

    # Rule 3: one core count per chipset.
    by_soc: dict[str, dict[str, Any]] = {}
    for preset in presets:
        entry = by_soc.setdefault(preset.abi.soc_model, {})
        for cores in preset.cpu_options:
            entry.setdefault(cores, []).append(preset.preset_id)
    for soc, buckets in by_soc.items():
        if len(buckets) > 1:
            detail = "; ".join(
                f"{cores} -> {ids[0]}" + (f" (+{len(ids) - 1} more)" if len(ids) > 1 else "")
                for cores, ids in sorted(buckets.items())
            )
            problems.append(f"[soc-core-count] {soc!r} has several core counts: {detail}")

    # Rule 4: the physical RAM amounts are plausible, and the options
    # are not empty.
    for preset in presets:
        if not preset.ram_options:
            problems.append(f"[ram-options] {preset.preset_id} offers no RAM amount")
            continue
        for amount in preset.ram_options:
            if amount not in PLAUSIBLE_RAM:
                problems.append(
                    f"[ram-options] {preset.preset_id} offers the implausible RAM {amount} GB"
                )

    # Rule 2 (catalogue side): the core counts themselves are legal.
    for preset in presets:
        for cores in preset.cpu_options:
            if cores not in HARDWARE_CONCURRENCY_VALUES:
                problems.append(
                    f"[cpu-options] {preset.preset_id} offers the illegal core count {cores}"
                )

    return problems


def check_generated(presets: list[Any]) -> list[str]:
    """Check the rules that live in the generated profile."""
    problems: list[str] = []
    generator = ProfileGenerator()

    for preset in presets:
        for index in range(PROFILES_PER_PRESET):
            profile = generator.generate(
                preset.preset_id, master_seed=f"{index:064x}"
            )
            memory = profile.navigator.device_memory
            cores = profile.navigator.hardware_concurrency

            if memory not in DEVICE_MEMORY_VALUES:
                problems.append(
                    f"[device-memory] {preset.preset_id} reported {memory}, "
                    f"which is not one of {DEVICE_MEMORY_VALUES}"
                )
            if cores not in HARDWARE_CONCURRENCY_VALUES:
                problems.append(
                    f"[hardware-concurrency] {preset.preset_id} reported {cores}, "
                    f"which is not one of {HARDWARE_CONCURRENCY_VALUES}"
                )
            expected = {
                approximate_device_memory(amount) for amount in preset.ram_options
            }
            if memory not in expected:
                problems.append(
                    f"[device-memory] {preset.preset_id} reported {memory}, "
                    f"which is not the approximation of {sorted(preset.ram_options)}"
                )
            if cores not in preset.cpu_options:
                problems.append(
                    f"[hardware-concurrency] {preset.preset_id} reported {cores}, "
                    f"which it does not offer ({sorted(preset.cpu_options)})"
                )

    return problems


def main() -> int:
    """Run every check and report the findings."""
    presets = list_presets()
    print(f"fleet: {len(presets)} presets")

    catalogue_problems = check_catalogue(presets)
    generated_problems = check_generated(presets)

    all_problems = catalogue_problems + generated_problems
    if not all_problems:
        print("OK: no violation of the three hardware rules")
        return 0

    counts = collections.Counter(problem.split("]")[0][1:] for problem in all_problems)
    print(f"FAIL: {len(all_problems)} violation(s)")
    for kind, count in sorted(counts.items()):
        print(f"  {kind}: {count}")
    print()
    for problem in all_problems[:200]:
        print(f"  {problem}")
    if len(all_problems) > 200:
        print(f"  ... and {len(all_problems) - 200} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
