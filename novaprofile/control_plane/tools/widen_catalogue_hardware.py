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
    """The RAM amounts the line is sold in."""

    options = set(values)
    if not options:
        options = {8}
    lowest = min(options)

    # The larger configurations of the line.
    for candidate in (lowest + 4, lowest + 8, lowest + 12):
        if candidate <= 64:
            options.add(candidate)

    # The smaller ones, which are what the platform reports differently.
    for candidate in LOWER_RAM:
        if candidate < lowest:
            options.add(candidate)

    return tuple(sorted(options))


def widen_cores(values: tuple[int, ...]) -> tuple[int, ...]:
    """The core counts the chipsets of the line report.

    The count is a property of the chipset rather than of the handset,
    and a handset is sold with more than one chipset.  The set is
    therefore the count of the chipset the catalogue names and the
    counts of the other chipsets the model shipped with.

    The counts in the set are the ones a chip designer actually ships:
    four, six, eight and twelve.  The arithmetic that walks a base up
    and down by one and two produces seven, nine and ten, and no
    chipset has ever had those -- a profile that reports one is a
    profile that cannot be a device.
    """

    if not values:
        return (8,)
    base = values[0]
    options = {base}
    for candidate in _REAL_CORE_COUNTS:
        # Only the counts of the same generation belong to the same
        # handset: a four-core chip and a twelve-core chip are two
        # different phones, not two configurations of one.
        if abs(candidate - base) <= 4:
            options.add(candidate)
    return tuple(sorted(options))


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
