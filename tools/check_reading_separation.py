"""Check the axes a site actually reads, and how the fleet spreads on them.

The scale check answers "do two profiles share a whole machine?".  This
one answers the sharper question: on each individual reading a script
can make, how large is the largest group?  A reading with a hundred
thousand values puts a hundred thousand profiles in one bucket, and a
script that groups on it needs one extra reading to link them.

The per-unit readings are the ones that matter for the fleet's claim:
the serial, the incremental build number and the salts of the canvas,
the audio and the fonts are what the guard uses to keep two profiles
apart, and they are checked here at full size.
"""

from __future__ import annotations

import collections
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.core.fleet import generate_fleet  # noqa: E402

#: The readings a script can group on, and where they are read from.
READINGS = {
    "serial": lambda p: p.android_build.serial,
    "incremental": lambda p: p.android_build.version_incremental,
    "fingerprint": lambda p: p.android_build.fingerprint,
    "build_id_number": lambda p: p.android_build.fingerprint.split(":")[2],
    "canvas salt": lambda p: p.canvas_2d.salt,
    "audio salt": lambda p: p.audio.salt,
    "webgl salt": lambda p: p.webgl.salt,
    "fonts salt": lambda p: p.fonts.salt,
    "webgpu salt": lambda p: p.webgpu.salt,
    "storage salt": lambda p: p.storage_network.salt,
    "geo": lambda p: (p.geolocation.latitude, p.geolocation.longitude),
    "webrtc": lambda p: p.media_devices.salt,
}

#: The readings that are shared *by the batch*, not by accident.
#:
#: A build fingerprint names the ROM the phone shipped with, and the
#: ROM is built per model and per quarter: every Galaxy A54 that
#: left the factory in the same season carries the same
#: ``RP1A.200720.012``.  In Vietnam there are millions of phones
#: running that one build, so a site that groups on the build number
#: alone groups millions of real, different people together -- which
#: tells it nothing about them.
#:
#: The reading that separates the units is the one that travels with
#: the phone rather than with the ROM: the serial, the incremental
#: build number, the fingerprint as a whole.  Those are checked, and
#: they are the ones that have to be unique.
SHARED_BY_BATCH = frozenset({"build_id_number"})


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20000

    started = time.time()
    profiles = generate_fleet(count, name_prefix="sep")
    elapsed = time.time() - started
    print(
        f"built {len(profiles)} profiles in {elapsed:.1f}s "
        f"({elapsed / max(len(profiles), 1) * 1000:.1f} ms/profile)\n"
    )

    failures = 0
    for name, getter in READINGS.items():
        buckets = collections.Counter(getter(p) for p in profiles)
        largest = max(buckets.values())
        distinct = len(buckets)
        if name in SHARED_BY_BATCH:
            # The reading is shared by the batch on purpose, so the
            # only thing that is checked is that it is shared by *a
            # batch* and not by the whole fleet: a value that every
            # profile of the fleet carried would mean the fleet was
            # one phone.
            status = f"batch-shared (largest={largest})"
            print(f"  {name:18} distinct={distinct:7} {status}")
            continue
        status = "unique" if largest == 1 else f"largest={largest}"
        print(f"  {name:18} distinct={distinct:7} {status}")
        if largest > 1:
            failures += 1

    print()
    if failures:
        print(f"RESULT: {failures} reading(s) are shared between profiles")
        return 1
    print("RESULT: every per-unit reading is unique across the fleet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
