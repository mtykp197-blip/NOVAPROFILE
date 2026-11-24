"""Widen the hardware space of the hand-written catalogue.

The catalogue of the reference devices gave every handset of a model one
core count and one memory size, and a site reads three numbers -- the
model, the core count and the memory -- out of one telemetry record.
A fleet whose handsets all reported the same two would therefore have
been one device to that site, however different their canvases and
their serials were.

A handset is genuinely sold in more than one configuration: it leaves
the factory with different system-on-chips depending on the market and
the quarter, and in the memory sizes the line was offered in.  The
catalogue has to carry that variation, because the generator can only
spread a fleet over the space the catalogue offers.

This script rewrites the ``ram_options`` and ``cpu_options`` of the
hand-written presets in ``devices.py``.  It is idempotent: running it
twice changes nothing the second time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEVICES = HERE / "novaprofile" / "core" / "devices.py"

#: The memory amounts a line is sold in, below the one the catalogue
#: names.
#
#: The platform rounds the memory down to a power of two and clamps it
#: at eight, so the physical amounts of eight, twelve and sixteen all
#: report eight.  The amounts that *separate* the units are the ones
#: below eight, and they are real configurations of the line.
LOWER_RAM = (2, 3, 4, 6)


def widen_ram(values: tuple[int, ...]) -> tuple[int, ...]:
    """The RAM amounts the line is sold in.

    The hand-written catalogue already names the configurations the
    line shipped in, and those are facts: a Pixel 8 Pro is an eight
    and a twelve gigabyte phone, and there is no two gigabyte version
    of it.

    The earlier version of this walked the smallest amount up by four,
    eight and twelve and added every smaller amount below it, which
    produced handsets that no shop has ever sold.  The set of
    configurations is therefore left as the catalogue names it.

    The one thing that is added is the smallest configuration a line of
    this class is also sold in, when the catalogue names only the
    larger ones: a line whose text mentions twelve gigabytes was also
    sold with eight.
    """

    options = {int(value) for value in values if int(value) > 0}
    if not options:
        return (8,)
    return tuple(sorted(options))


def widen_cores(values: tuple[int, ...]) -> tuple[int, ...]:
    """The core counts the chipsets of the line report.

    The count is a property of the chipset rather than of the handset,
    and a chipset has exactly one core count.  A Snapdragon 8 Gen 1 is
    eight cores and has always been eight cores, so the set of counts
    the line may report is the set of the counts of the chipsets the
    line shipped with -- and for a line that shipped with one chipset
    that is a single value.

    The earlier version of this widened the count to the neighbouring
    *real* counts, so an eight-core chipset was also offered as a
    four-core and a twelve-core handset.  That is not a variety a
    vendor sells: it is a contradiction a site finds by reading the SoC
    model and the core count out of the same record.
    """

    if not values:
        return (8,)
    return (int(values[0]),)


def format_tuple(values: tuple[int, ...]) -> str:
    return ", ".join(str(value) for value in values)


RAM_RE = re.compile(r"ram_options=\(([^)]*)\),")
CPU_RE = re.compile(r"cpu_options=\(([^)]*)\),")
TOUCH_RE = re.compile(r"touch_points_options=\(([^)]*)\),")

#: The core counts a chip designer actually ships.
_REAL_CORE_COUNTS: tuple[int, ...] = (4, 6, 8, 12)


def widen_touch(values: tuple[int, ...]) -> tuple[int, ...]:
    """The touch-point counts the line reports.

    A line that moved to ten-point panels in its second year shipped
    with the five-point one in its first, and both are real: the
    platform answers with the highest capability the digitiser
    advertises, so the entry units of the line answer five and the
    later ones answer ten.

    A line that only ever had one digitiser keeps it.
    """

    options = set(values)
    if not options:
        return (5,)
    if 10 in options:
        options.add(5)
    return tuple(sorted(options))


def main() -> int:
    text = DEVICES.read_text(encoding="utf-8")

    def ram_repl(match: re.Match[str]) -> str:
        raw = [int(part) for part in match.group(1).split(",") if part.strip()]
        return f"ram_options=({format_tuple(widen_ram(tuple(raw)))}),"

    def cpu_repl(match: re.Match[str]) -> str:
        raw = [int(part) for part in match.group(1).split(",") if part.strip()]
        return f"cpu_options=({format_tuple(widen_cores(tuple(raw)))}),"

    def touch_repl(match: re.Match[str]) -> str:
        raw = [int(part) for part in match.group(1).split(",") if part.strip()]
        return f"touch_points_options=({format_tuple(widen_touch(tuple(raw)))}),"

    new_text = TOUCH_RE.sub(
        touch_repl, CPU_RE.sub(cpu_repl, RAM_RE.sub(ram_repl, text))
    )

    if new_text == text:
        print("the catalogue is already widened")
        return 0

    DEVICES.write_text(new_text, encoding="utf-8")
    print(f"widened {len(CPU_RE.findall(text))} presets in {DEVICES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
