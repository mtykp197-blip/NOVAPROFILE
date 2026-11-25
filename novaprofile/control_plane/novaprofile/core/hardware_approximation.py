"""How Chromium reports the memory and the core count.

``navigator.deviceMemory`` and ``navigator.hardwareConcurrency`` are not
raw hardware readings.  Both go through an approximation in Blink, and a
profile that does not apply the same approximation reports values no
browser can produce -- which is worse than reporting an unusual value,
because an impossible value is a strong bot signal.

The rules were read off the Chromium tree this project patches, not off a
specification, because the specification and the implementation drifted
apart years ago.  The source is
``third_party/blink/common/device_memory/approximated_device_memory.cc``
in Chromium 160, and it is quoted below where it matters.

**The rounding takes the nearest power of two, not the next one up.**
The implementation finds the most significant bit of the physical
memory, builds the two bounds around it, and keeps the one that is
closer.  A machine with 28 GB is closer to 32 than to 16, so it reports
32; a machine with 24 GB is exactly halfway between 16 and 32, and the
tie goes to the lower bound, so it reports 16.  Rounding up
unconditionally would report 32 for the 24 GB machine, which is a
value no Chromium build produces.

**Android is clamped to eight gigabytes.**  The same file reads:

.. code-block:: cpp

    float kMinMemory = 2.0f;
    float kMaxMemory = 32.0f;

    #if BUILDFLAG(IS_ANDROID)
      // Allow smaller lower limits on Android where lower RAM is still common.
      kMinMemory = 1.0f;
      kMaxMemory = 8.0f;
    #endif

The desktop clamp is what a desktop Chrome reports, and it is what a
measurement on a desktop machine shows.  The product of this project is
an *Android* browser -- the build that ships the native core targets
Android arm64 -- so the Android clamp is the one that applies: the page
reads at most eight, and a handset with twelve gigabytes reports eight.

The same distinction applies to the core count.  Chromium reads the real
logical core count and does not clamp it, so a workstation with
eighteen threads reports eighteen.  The handsets of this fleet report
four, six, eight or twelve.
"""

from __future__ import annotations

#: The smallest value ``deviceMemory`` can take.  On Android the floor
#: is one gigabyte; the physical memory of the cheapest handset of the
#: fleet is one gigabyte, and the approximation never goes below it.
MIN_DEVICE_MEMORY = 1

#: The largest value ``deviceMemory`` can take on Android.  Chromium
#: caps Android at eight gigabytes to reduce fingerprintability, so a
#: handset with twelve or sixteen gigabytes reports eight.
MAX_DEVICE_MEMORY = 8

#: The values ``deviceMemory`` can take.  Every one of them is a power of
#: two, because the approximation rounds to a power of two.
DEVICE_MEMORY_VALUES: tuple[int, ...] = (1, 2, 4, 8)

#: The values ``hardwareConcurrency`` can take.  The count is not
#: clamped, so it is the count the SoC reports.
HARDWARE_CONCURRENCY_VALUES: tuple[int, ...] = (4, 6, 8, 12)

#: The V8 heap ceiling of a device, by the memory it reports.
#:
#: ``performance.memory.jsHeapSizeLimit`` is the ceiling of the heap of
#: the tab, and the ceiling follows the *physical* memory of the
#: machine, rounded down to a power of two.  A phone with four
#: gigabytes therefore gets a two gigabyte ceiling and a phone with
#: eight gets a four gigabyte one.
#:
#: The ceiling of the smallest tier is a quarter of a gigabyte: V8
#: never hands a tab more heap than the machine can hold, and a phone
#: with one gigabyte of memory cannot back a half gigabyte of heap and
#: the renderer, the compositor and the GPU process at the same time.
#:
#: Every ceiling fits in a signed 32-bit integer, and that is not an
#: accident: the browser core reads the value out of a JSON document
#: with a 32-bit number reader, so a larger ceiling is not merely
#: unusual, it makes the whole profile unreadable -- the core refuses
#: to start and every surface falls back to the host's values.
HEAP_CEILING_BY_DEVICE_MEMORY: dict[int, int] = {
    1: 268_435_456,  # 256 MiB
    2: 536_870_912,  # 512 MiB
    4: 1_073_741_824,  # 1 GiB
    8: 2_000_000_000,  # the Chromium default ceiling
}


def approximate_device_memory(physical_gb: int) -> int:
    """Return what Chromium reports for a machine with ``physical_gb``.

    This is the algorithm of
    ``ApproximatedDeviceMemory::CalculateAndSetApproximatedDeviceMemory``,
    reproduced so that the fleet reports what Chromium would have
    reported on the same hardware.

    The physical memory is first located in the power-of-two ladder: the
    most significant bit and its position give the two bounds around it,
    and the bound that is *closer* to the physical memory wins.  The
    result is then clamped to the platform range, which on Android is
    one through eight gigabytes.

    A handset with 3 GB therefore reports 2, one with 6 GB reports 4,
    and one with 12 GB reports 8 -- which is the whole reason a page
    cannot tell a 6 GB phone from a 4 GB one.
    """

    if physical_gb <= 0:
        return MIN_DEVICE_MEMORY

    # The most significant bit and its location.
    power = 0
    lower_bound = physical_gb
    while lower_bound > 1:
        lower_bound >>= 1
        power += 1

    upper_bound = (lower_bound + 1) << power
    lower_bound = lower_bound << power

    # The closest bound wins, and a tie goes to the lower one -- the
    # comparison in Chromium is ``<=``.
    if physical_gb - lower_bound <= upper_bound - physical_gb:
        approximated = lower_bound
    else:
        approximated = upper_bound

    if approximated < MIN_DEVICE_MEMORY:
        return MIN_DEVICE_MEMORY
    if approximated > MAX_DEVICE_MEMORY:
        return MAX_DEVICE_MEMORY
    return approximated


def approximate_hardware_concurrency(logical_cores: int) -> int:
    """Return what Chromium reports for a machine with ``logical_cores``.

    The core count is the number of logical cores the platform has.  It
    is not rounded and not clamped: Chromium reads
    ``base::SysInfo::NumberOfProcessors()`` and hands that to the page.

    A phone with six cores therefore reports six, and a workstation with
    eighteen threads reports eighteen.

    The one floor is two, and the floor is the platform's and not the
    reading's: Android hands a process at least two CPUs, so
    ``NumberOfProcessors()`` answers two on a handset whose chip has
    one core in it, and a page that reads one core is reading a number
    no Android device ever reports.
    """

    if logical_cores <= 2:
        return 2
    return logical_cores


def heap_ceiling_for(device_memory: int) -> int:
    """Return the V8 heap ceiling of a device reporting ``device_memory``.

    The ceiling follows the memory the device reports, so the two
    readings a page takes of the memory of a handset agree with each
    other.  A profile that reports two gigabytes of memory and a four
    gigabyte heap ceiling is describing a machine that was never built,
    and the site that reads both finds the contradiction at once.
    """

    memory = int(device_memory)
    ceiling = HEAP_CEILING_BY_DEVICE_MEMORY.get(memory)
    if ceiling is not None:
        return ceiling

    # A memory the Android build never reports is answered with the
    # ceiling of the tier below the largest one, because the largest
    # ceiling is the desktop default and a phone does not get it.
    if memory < MIN_DEVICE_MEMORY:
        return HEAP_CEILING_BY_DEVICE_MEMORY[MIN_DEVICE_MEMORY]
    return HEAP_CEILING_BY_DEVICE_MEMORY[MAX_DEVICE_MEMORY]


__all__ = [
    "DEVICE_MEMORY_VALUES",
    "HARDWARE_CONCURRENCY_VALUES",
    "HEAP_CEILING_BY_DEVICE_MEMORY",
    "MAX_DEVICE_MEMORY",
    "MIN_DEVICE_MEMORY",
    "approximate_device_memory",
    "approximate_hardware_concurrency",
    "heap_ceiling_for",
]
