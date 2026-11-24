"""Check that a large fleet has no two profiles that a site would link.

The fleet is built the way the application builds it, one profile at a
time, and the check is the pessimistic one: two profiles count as
linked when they agree on *any* of the readings a script can group on,
not only when they agree on all of them.

The check is a sampling run rather than a full hundred million, because
a hundred million profiles take a long time to build and the
question the number answers is the same one a smaller fleet answers:
does the generator ever produce a pair that agrees?
"""

import argparse
import collections
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"),
)

from novaprofile.core.fleet import generate_fleet, machine_identity


AXES = (
    "gpu",
    "device_model",
    "build_id",
    "hardware_concurrency",
    "device_memory",
    "screen",
    "color_depth",
    "touch_points",
    "device_pixel_ratio",
)


def value_of(profile, axis):
    if axis == "gpu":
        return profile.webgl.unmasked_renderer
    if axis == "device_model":
        return profile.android_build.model
    if axis == "build_id":
        return profile.android_build.build_id
    if axis == "hardware_concurrency":
        return profile.navigator.hardware_concurrency
    if axis == "device_memory":
        return profile.navigator.device_memory
    if axis == "screen":
        return (profile.screen.width, profile.screen.height)
    if axis == "color_depth":
        return profile.screen.color_depth
    if axis == "touch_points":
        return profile.navigator.max_touch_points
    if axis == "device_pixel_ratio":
        return profile.screen.device_pixel_ratio
    raise KeyError(axis)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20000)
    parser.add_argument("--report", type=int, default=25)
    args = parser.parse_args()

    started = time.time()
    profiles = generate_fleet(args.count, name_prefix="scale")
    elapsed = time.time() - started
    print(
        f"built {len(profiles)} profiles in {elapsed:.1f}s "
        f"({elapsed / max(len(profiles), 1) * 1000:.1f} ms/profile)"
    )

    # The whole identity of a profile, as a site that collects
    # everything would compute it.
    whole = collections.Counter()
    for profile in profiles:
        digest = hashlib.sha256(repr(machine_identity(profile)).encode()).hexdigest()
        whole[digest] += 1

    print(f"distinct machine identities: {len(whole)} of {len(profiles)}")
    duplicates = sum(count - 1 for count in whole.values() if count > 1)
    print(f"profiles sharing a whole identity: {duplicates}")

    # The pessimistic measure: any single reading that is shared.
    worst = []
    for axis in AXES:
        buckets = collections.Counter(value_of(p, axis) for p in profiles)
        largest = max(buckets.values())
        pairs = sum(n * (n - 1) // 2 for n in buckets.values() if n > 1)
        worst.append((largest, pairs, axis, len(buckets)))
        print(
            f"  {axis:22} values={len(buckets):5} largest group={largest:6} "
            f"pairs={pairs:9}"
        )

    worst.sort(reverse=True)
    largest, pairs, axis, values = worst[0]
    print(f"\nlargest group overall: {largest} on '{axis}' ({values} distinct values)")

    # The seeds, the salts and the serials, which are the fields that
    # have to differ even when the machine does not.
    for name, getter in (
        ("serial", lambda p: p.android_build.serial),
        ("canvas salt", lambda p: p.canvas_2d.salt),
        ("audio salt", lambda p: p.audio.salt),
    ):
        seen = collections.Counter(getter(p) for p in profiles)
        biggest = max(seen.values())
        print(f"  {name:12} distinct={len(seen):6} largest group={biggest}")

    ok = duplicates == 0
    print("\nRESULT:", "no two profiles share a machine identity" if ok else "COLLISION")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
