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
closer.  A machine with 28 GB is exactly halfway between 16 and 32, and
the tie goes to the lower bound, so it reports 16.  A machine with 30 GB
reports 32.  Rounding up unconditionally would report 32 for the 28 GB
machine, which is a value no Chromium build produces.

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

    A handset with 3 GB therefore reports 4, one with 6 GB reports 8,
    and one with 12 GB reports 8 -- which is the whole reason a page
    cannot tell a 6 GB phone from an 8 GB one.
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
    """

    if logical_cores <= 0:
        return 1
    return logical_cores


__all__ = [
    "DEVICE_MEMORY_VALUES",
    "HARDWARE_CONCURRENCY_VALUES",
    "MAX_DEVICE_MEMORY",
    "MIN_DEVICE_MEMORY",
    "approximate_device_memory",
    "approximate_hardware_concurrency",
]
