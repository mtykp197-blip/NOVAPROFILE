"""The values Chromium reports for the memory and the core count.

A profile that reports a value no browser can produce is worse than one
that reports an unusual value: an impossible value is a strong bot
signal.  These tests hold the approximation to the algorithm in the
Chromium tree this project patches.

The rules under test are read off
``third_party/blink/common/device_memory/approximated_device_memory.cc``
in Chromium 160:

* the physical memory is rounded to the *nearest* power of two, and a
  tie goes to the lower bound;
* the result is clamped to [1 GB, 8 GB] on Android, because Chromium
  lowers the ceiling to eight for Android and lowers the floor to one;
* the core count is neither rounded nor clamped.
"""

from __future__ import annotations

import pytest

from novaprofile.core.hardware_approximation import (
    DEVICE_MEMORY_VALUES,
    HEAP_CEILING_BY_DEVICE_MEMORY,
    MAX_DEVICE_MEMORY,
    MIN_DEVICE_MEMORY,
    approximate_device_memory,
    approximate_hardware_concurrency,
    heap_ceiling_for,
)


class TestDeviceMemory:
    """The rounding of the physical memory."""

    @pytest.mark.parametrize(
        ("physical", "expected"),
        [
            (1, 1),
            (2, 2),
            # 3 GB is exactly between 2 and 4.  Chromium compares with
            # ``<=``, so the tie goes to the lower bound.
            (3, 2),
            (4, 4),
            (5, 4),
            (6, 4),
            (7, 8),
            (8, 8),
            # The Android ceiling of eight: a handset with twelve or
            # sixteen gigabytes reports eight.
            (12, 8),
            (16, 8),
            (24, 8),
            (64, 8),
        ],
    )
    def test_the_rounding_matches_chromium(
        self, physical: int, expected: int
    ) -> None:
        assert approximate_device_memory(physical) == expected

    def test_every_value_is_a_power_of_two(self) -> None:
        """Chromium rounds to a power of two, and the WPT asserts it.

        The web platform test ``device-memory.https.any.js`` asserts that
        ``navigator.deviceMemory`` is a power of two, so a value that is
        not one is a value no browser produces.
        """

        for physical in range(1, 65):
            value = approximate_device_memory(physical)
            assert value & (value - 1) == 0, f"{physical} GB produced {value}"

    def test_the_value_is_always_in_the_declared_ladder(self) -> None:
        for physical in range(1, 65):
            assert approximate_device_memory(physical) in DEVICE_MEMORY_VALUES

    def test_the_android_clamp_holds(self) -> None:
        """Android is clamped to one through eight gigabytes.

        The desktop clamp of thirty-two is what a desktop Chrome
        reports; the product of this project is an Android browser, and
        the Chromium source lowers the ceiling to eight for Android.
        """

        for physical in range(1, 65):
            value = approximate_device_memory(physical)
            assert MIN_DEVICE_MEMORY <= value <= MAX_DEVICE_MEMORY
        assert MAX_DEVICE_MEMORY == 8
        assert MIN_DEVICE_MEMORY == 1

    def test_a_zero_or_negative_amount_reports_the_floor(self) -> None:
        assert approximate_device_memory(0) == MIN_DEVICE_MEMORY
        assert approximate_device_memory(-4) == MIN_DEVICE_MEMORY

    def test_a_bigger_handset_never_reports_more_than_a_smaller_one(self) -> None:
        """The approximation is monotone.

        A handset with more memory never reports less of it, because the
        page sees a value that grows with the hardware.
        """

        previous = 0
        for physical in range(1, 65):
            value = approximate_device_memory(physical)
            assert value >= previous
            previous = value


class TestHardwareConcurrency:
    """The core count is not rounded and not clamped."""

    @pytest.mark.parametrize("cores", [4, 6, 8, 12])
    def test_the_count_is_reported_as_it_is(self, cores: int) -> None:
        assert approximate_hardware_concurrency(cores) == cores

    def test_a_workstation_count_is_not_rounded(self) -> None:
        """Chromium reads the real count and hands it to the page.

        ``NavigatorConcurrentHardware::hardwareConcurrency`` returns
        ``base::SysInfo::NumberOfProcessors()`` without a rounding step,
        so an eighteen-thread workstation reports eighteen.
        """

        assert approximate_hardware_concurrency(18) == 18
        assert approximate_hardware_concurrency(32) == 32

    def test_a_count_below_the_floor_reports_two(self) -> None:
        """Android hands a process at least two CPUs.

        ``base::SysInfo::NumberOfProcessors()`` answers two on a
        handset whose chip has one core in it, so a page that reads
        one core is reading a number no Android device ever reports.
        """

        assert approximate_hardware_concurrency(0) == 2
        assert approximate_hardware_concurrency(1) == 2
        assert approximate_hardware_concurrency(2) == 2
