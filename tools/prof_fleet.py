"""Profile the fleet generator to find the hot spots."""

import cProfile
import pstats
import sys

sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\control_plane")

from novaprofile.core.fleet import generate_fleet


def run() -> None:
    generate_fleet(200, name_prefix="prof")


profiler = cProfile.Profile()
profiler.enable()
run()
profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats("cumulative").print_stats(30)
