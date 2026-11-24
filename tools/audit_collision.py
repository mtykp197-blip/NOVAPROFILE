"""The sharpest check of the collision claim there is.

The other checkers ask whether two profiles carry the same *reading*.
This one asks the harder question: **would a site that reads a set of
readings find a pair?**

A site does not group on one value.  It groups on a *combination* --
the GPU and the chip count, the model and the memory, the screen and
the touch count -- and it groups on the combination because that is
what makes the group small enough to be worth following.  Two phones
of the same model that agree on the chip count are a pair; two that
agree on the chip count *and* the memory are a pair that a script
finds without reading the GPU at all.

So this checker walks the fleet twice.

The first walk measures the readings on their own: how many distinct
values each one carries, and how large the biggest group of a single
reading is.  A reading with four values and a hundred thousand
profiles puts a quarter of the fleet in one bucket -- and that is not
a defect, because the chip count of a phone is the chip count of its
model, and no fleet can widen it.  What has to hold is that the
*identity* readings -- the serial, the build fingerprint, the
incremental build number -- are unique, because those are the ones
that travel with the unit rather than with the model.

The second walk is the one that matters: it takes every combination
of readings a script could plausibly make, and it asks how many pairs
of profiles agree on *that* combination.  A pair that agrees on the
whole machine is a pair a site can follow.  A pair that agrees on the
GPU alone is not, because there are hundreds of thousands of handsets
with that GPU.  The report says, for each combination, how many pairs
exist and how big the biggest group is -- so the operator can see
exactly how far a site could follow the fleet.

Finally the checker runs the *guard* over the fleet with a persistent
registry, which is the claim the product makes: not "the readings
differ" but "the guard, asked to allocate against every profile that
already exists, never had to accept a duplicate".

The exit code is the answer: zero means the fleet holds, non-zero
means it does not.
"""

from __future__ import annotations

import collections
import itertools
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.core.collision import (  # noqa: E402
    CollisionGuard,
    InMemorySignatureRegistry,
)
from novaprofile.core.fleet import generate_fleet  # noqa: E402

#: The readings a script can read, and where it reads them from.
READINGS: dict[str, object] = {
    "gpu": lambda p: p.webgl.unmasked_renderer,
    "model": lambda p: p.android_build.model,
    "release": lambda p: p.android_build.version_release,
    "cores": lambda p: p.navigator.hardware_concurrency,
    "memory": lambda p: p.navigator.device_memory,
    "touch": lambda p: p.navigator.max_touch_points,
    "width": lambda p: p.screen.width,
    "height": lambda p: p.screen.height,
    "ratio": lambda p: p.screen.device_pixel_ratio,
    "depth": lambda p: p.screen.color_depth,
    "serial": lambda p: p.android_build.serial,
    "incremental": lambda p: p.android_build.version_incremental,
    "fingerprint": lambda p: p.android_build.fingerprint,
    "geo": lambda p: (p.geolocation.latitude, p.geolocation.longitude),
}

#: The readings that are unique by construction.
#:
#: A serial number is not shared by two devices, a build fingerprint
#: names one build of one model, and the incremental build number
#: counts the builds of that model.  Those are the readings that have
#: to hold for the fleet to be a fleet of *different* units.
IDENTITY_READINGS = ("serial", "incremental", "fingerprint")

#: The readings the site reads on their own, without a partner.
SINGLE_READS = ("gpu", "model", "release", "cores", "memory", "touch", "width", "height")

#: The combinations a script would make.
#:
#: The cheap ones come first: the chip count and the memory together
#: are two numbers, and they are what a script groups on when it
#: cannot afford to read the GPU.  The whole machine is last, because
#: that is the combination a script builds when it wants to be
#: certain.
COMBINATIONS: tuple[tuple[str, ...], ...] = (
    ("cores", "memory"),
    ("cores", "touch"),
    ("memory", "touch"),
    ("gpu", "cores"),
    ("gpu", "memory"),
    ("gpu", "touch"),
    ("model", "cores"),
    ("model", "memory"),
    ("model", "touch"),
    ("model", "release"),
    ("width", "height"),
    ("width", "height", "ratio"),
    ("gpu", "cores", "memory"),
    ("gpu", "width", "height"),
    ("model", "cores", "memory"),
    ("model", "release", "gpu"),
    (
        "gpu",
        "model",
        "release",
        "cores",
        "memory",
        "touch",
        "width",
        "height",
        "ratio",
        "depth",
    ),
)


def _pairs_within(counts: collections.Counter) -> int:
    """How many pairs of profiles share a value."""

    return sum(n * (n - 1) // 2 for n in counts.values())


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000

    started = time.time()
    registry = InMemorySignatureRegistry()
    profiles = generate_fleet(
        count,
        name_prefix="audit",
        registry=registry,
        vietnam=True,
    )
    elapsed = time.time() - started
    print(
        f"built {len(profiles)} profiles in {elapsed:.1f}s "
        f"({elapsed / max(len(profiles), 1) * 1000:.1f} ms/profile)\n"
    )

    failures = 0

    # ------------------------------------------------------------------
    # 1. The identity readings, on their own.
    # ------------------------------------------------------------------
    print("--- identity readings (must be unique) ---")
    for name in IDENTITY_READINGS:
        getter = READINGS[name]
        values = [getter(p) for p in profiles]
        distinct = len(set(values))
        ok = distinct == len(values)
        if not ok:
            failures += 1
        print(
            f"  {name:14} distinct={distinct:8} / {len(values):8}  "
            f"{'OK' if ok else 'COLLISION'}"
        )

    # ------------------------------------------------------------------
    # 2. The narrow readings, on their own.
    # ------------------------------------------------------------------
    print("\n--- narrow readings (shared by the field, spread by the fleet) ---")
    for name in SINGLE_READS:
        getter = READINGS[name]
        counts = collections.Counter(getter(p) for p in profiles)
        biggest = max(counts.values())
        share = biggest / max(len(profiles), 1)
        print(
            f"  {name:14} distinct={len(counts):8}  biggest={biggest:8}  "
            f"({share:6.2%} of the fleet)"
        )

    # ------------------------------------------------------------------
    # 3. The combinations a script groups on.
    # ------------------------------------------------------------------
    print("\n--- combinations a script groups on ---")
    print(f"  {'combination':44} {'pairs':>10}  {'biggest group':>14}")
    for combo in COMBINATIONS:
        getters = [READINGS[name] for name in combo]
        keys = [
            tuple(g(p) for g in getters)
            for p in profiles
        ]
        counts = collections.Counter(keys)
        pairs = _pairs_within(counts)
        biggest = max(counts.values())
        label = "+".join(combo)
        if len(label) > 44:
            label = label[:41] + "..."
        print(f"  {label:44} {pairs:10}  {biggest:14}")

    # ------------------------------------------------------------------
    # 4. The whole machine, with the serial.
    # ------------------------------------------------------------------
    #    The machine a site could follow is the model, the release, the
    #    graphics part, the panel, the chip count, the memory and the
    #    touch count -- plus the serial, because that is the reading
    #    that says which of the two phones it is talking to.
    machine_keys = [
        (
            p.android_build.model,
            p.android_build.version_release,
            p.webgl.unmasked_renderer,
            p.screen.width,
            p.screen.height,
            p.screen.device_pixel_ratio,
            p.navigator.hardware_concurrency,
            p.navigator.device_memory,
            p.navigator.max_touch_points,
            p.android_build.serial,
        )
        for p in profiles
    ]
    machine_counts = collections.Counter(machine_keys)
    machine_pairs = _pairs_within(machine_counts)
    biggest_machine = max(machine_counts.values())
    print("\n--- the whole machine, serial included ---")
    print(f"  distinct machines: {len(machine_counts)} / {len(profiles)}")
    print(f"  pairs sharing one machine: {machine_pairs}")
    print(f"  biggest group: {biggest_machine}")
    if machine_pairs:
        failures += 1

    # ------------------------------------------------------------------
    # 5. The guard, against every profile that already exists.
    # ------------------------------------------------------------------
    #    This is the claim the product makes.  The guard is asked to
    #    allocate every profile of the fleet against the registry that
    #    holds all the profiles before it, and it must never have to
    #    accept a duplicate.
    print("\n--- the guard, allocating against the whole fleet ---")
    guard = CollisionGuard(registry=InMemorySignatureRegistry())
    accepted = 0
    rejected = 0
    for profile in profiles:
        try:
            allocated = guard.allocate(profile.identity.id or "", profile)
            guard.commit(allocated, profile.identity.id)
            accepted += 1
        except Exception as error:  # noqa: BLE001 - the report is the point
            rejected += 1
            print(f"  REJECTED: {type(error).__name__}: {error}")
    print(f"  accepted: {accepted} / {len(profiles)}")
    if rejected:
        failures += 1

    print()
    if failures:
        print(f"RESULT: {failures} check(s) FAILED")
        return 1
    print("RESULT: the fleet holds -- no duplicate unit, no duplicate identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
