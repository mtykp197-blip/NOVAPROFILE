"""Find where the time goes when a profile is generated.

The engine has to make a hundred million profiles.  At the rate it
manages now that takes months, so the question is which part of the
work is the expensive one.
"""

from __future__ import annotations

import cProfile
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.core.fleet import generate_fleet  # noqa: E402


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    profiler = cProfile.Profile()
    profiler.enable()
    generate_fleet(count=count, name_prefix="prof")
    profiler.disable()

    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")
    stats.print_stats(28)
    return 0


if __name__ == "__main__":
    sys.exit(main())
