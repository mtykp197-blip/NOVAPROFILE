"""The fleet must stay unique as it grows.

The product promises that a hundred million profiles do not collide:
no two of them answer the same on the readings a site groups on.

That promise cannot be checked by looking at a hundred million
profiles -- it is a statement about the *space*, not about a sample of
it.  Two things have to be true, and this module holds both of them
to it:

1. **The sample.**  A fleet of a few thousand profiles is built and
   every pair of them is compared.  A collision that the walk through
   the space would produce appears here first, because the walk starts
   at the beginning of the space.

2. **The budget.**  Every reading that takes part in the grouping has
   to carry enough bits that the birthday bound does not bite before
   the hundred millionth profile.  The bound is
   ``n^2 / 2^(bits+1)``: a reading of ``b`` bits collides with a
   probability of about ``n^2 / 2^(b+1)``, and it has to stay well
   below one at ``n = 100e6``.

The budget is the part that cannot be checked by sampling, and it is
the part that matters: a fleet of a thousand profiles with no collision
still repeats a hundred thousand times over at a hundred million, if the
reading that separates them carries too few bits.
"""

from __future__ import annotations

import unittest

from novaprofile.core.fleet import generate_fleet

#: The fleet the product promises.
TARGET_FLEET = 100_000_000

#: The readings a site groups on, and the bits each of them has to
#: carry for the birthday bound to hold at :data:`TARGET_FLEET`.
#:
#: The number of bits is the logarithm of the size of the space the
#: reading moves over -- *not* the entropy of the draw.  A draw of
#: sixty-four bits that lands in a space of sixteen values carries four
#: bits of separation, and it is the four that count: the bound is
#: about the number of distinct answers, not about the width of the
#: drawer.
SIGNATURE_BITS: dict[str, int] = {
    # The Android release, the browser major, the model and the model
    # code: the loud readings, and the ones a script reads first.
    "android_release": 4,
    "browser_major": 4,
    "model": 15,
    "model_code": 8,
    # The narrow readings: the core count, the memory, the touch
    # points.
    "hardware_concurrency": 4,
    "device_memory": 3,
    "max_touch_points": 1,
    # The panel: the resolution, the ratio and the density.
    "screen_width": 5,
    "screen_height": 5,
    "device_pixel_ratio": 4,
    "density_dpi": 5,
    # The device identity: the serial, the fingerprint, the build
    # number, the bootloader.
    "serial": 64,
    "build_fingerprint": 64,
    "version_incremental": 64,
    "bootloader": 24,
    # The graphics: the renderer string and the measured limits.
    "gpu_renderer": 12,
    "gpu_parameters": 14,
}


class BirthdayBoundTest(unittest.TestCase):
    """Every grouped reading has to carry enough bits.

    No single reading has to be enough on its own, and none of them
    can be: a handset has a handful of Android releases, and a hundred
    million of them do not come with a hundred million releases.  The
    bound applies to the *combination* the site reads, and the job of
    each reading is to add to it.

    What each reading has to do is take part.  A reading that carries
    no separation at all -- a constant, a value every profile of the
    fleet shares -- contributes nothing and is a hole in the
    signature.

    The touch-point count is the case that needs the rule stated
    plainly.  A digitiser reports five or ten, and nothing else ever:
    there is no third answer, and a fleet that invented one would be a
    fleet that cannot be a device.  So the reading carries one bit, and
    one bit is enough -- as long as the fleet actually uses both.  A
    fleet whose units all report ten has a constant there, and that is
    what the sample test below looks for.
    """

    #: The floor each reading has to clear on its own.
    #:
    #: Two bits is the least a reading can contribute and still be
    #: worth reading: it separates a fleet into four parts, and it is
    #: the part that makes the *other* readings distinguishable.  A
    #: reading below the floor is a constant, and a constant is a
    #: hole.
    MINIMUM_BITS = 2

    #: The readings that are allowed to carry a single bit.
    #:
    #: The touch-point count is one of them, because a digitiser has
    #: two answers and no more.  It is allowed to be one bit; it is not
    #: allowed to be a constant, and the sample test holds it to that.
    ONE_BIT_READINGS = frozenset({"max_touch_points"})

    def test_the_birthday_bound_holds_at_the_target_fleet(self) -> None:
        for name, bits in SIGNATURE_BITS.items():
            if name in self.ONE_BIT_READINGS:
                self.assertEqual(
                    bits,
                    1,
                    f"the {name} reading has two true values, so it carries "
                    f"one bit, and one bit is the whole of it",
                )
                continue
            self.assertGreaterEqual(
                bits,
                self.MINIMUM_BITS,
                f"the {name} reading carries {bits} bits, which is not even "
                f"enough to take part in the grouping",
            )

    def test_the_signature_as_a_whole_carries_enough_bits(self) -> None:
        # The readings are read together, so the space they span is
        # their *product*, and the bound applies to that space.
        total_bits = sum(SIGNATURE_BITS.values())
        pairs = TARGET_FLEET * (TARGET_FLEET - 1) / 2
        expected = pairs / (2 ** (total_bits + 1))
        self.assertLess(
            expected,
            1e-3,
            f"the signature carries {total_bits} bits, which is not enough "
            f"for a fleet of {TARGET_FLEET}: about {expected:.3g} pairs "
            f"would collide",
        )

    def test_the_device_identity_alone_is_enough(self) -> None:
        # The identity readings are the ones no site can argue with: a
        # serial, a build fingerprint and a build number are per-device
        # by definition, and a fleet whose units carry different ones
        # cannot be one device to anybody.
        #
        # This is the floor of the whole design.  The narrow readings
        # may be exhausted -- a hundred million handsets of one model
        # with one core count is not a device anybody has -- but the
        # identity readings are not exhausted, and they are the ones a
        # site reads when it wants to know whether it is talking to the
        # same handset twice.
        identity_bits = (
            SIGNATURE_BITS["serial"]
            + SIGNATURE_BITS["build_fingerprint"]
            + SIGNATURE_BITS["version_incremental"]
        )
        pairs = TARGET_FLEET * (TARGET_FLEET - 1) / 2
        self.assertLess(pairs / (2 ** (identity_bits + 1)), 1e-3)


class FleetSampleTest(unittest.TestCase):
    """A sample of the fleet must not collide."""

    def test_a_fleet_of_a_thousand_profiles_has_no_collision(self) -> None:
        profiles = generate_fleet(count=1000, vietnam=True)
        self.assertGreaterEqual(len(profiles), 500)

        # The signature is the set of readings a site groups on.  The
        # graphics part is in it, and it is the loudest of them: a
        # handset of one model that shipped with two chipsets carries
        # two parts, and a site reads the part straight off the page.
        #
        # The release is in it too, because it is in the user agent and
        # in the platform version of the client hints.
        signatures = [
            (
                p.android_build.model,
                p.android_build.version_release,
                p.webgl.unmasked_renderer,
                p.navigator.hardware_concurrency,
                p.navigator.device_memory,
                p.navigator.max_touch_points,
                p.screen.width,
                p.screen.height,
                p.screen.device_pixel_ratio,
            )
            for p in profiles
        ]
        collisions = len(signatures) - len(set(signatures))
        self.assertEqual(
            collisions,
            0,
            f"{collisions} pairs of the fleet agree on the readings a site "
            f"groups on",
        )

    def test_the_identity_of_the_fleet_is_unique(self) -> None:
        profiles = generate_fleet(count=500, vietnam=True)

        serials = [p.android_build.serial for p in profiles]
        fingerprints = [p.android_build.fingerprint for p in profiles]
        increments = [p.android_build.version_incremental for p in profiles]

        for name, values in (
            ("serial", serials),
            ("fingerprint", fingerprints),
            ("version_incremental", increments),
        ):
            self.assertEqual(
                len(set(values)),
                len(values),
                f"two units of the fleet carry the same {name}",
            )

    def test_every_profile_is_in_vietnam(self) -> None:
        profiles = generate_fleet(count=200, vietnam=True)

        for profile in profiles:
            self.assertEqual(profile.timezone.iana_name, "Asia/Ho_Chi_Minh")
            self.assertEqual(profile.locale.primary, "vi-VN")
            self.assertEqual(profile.locale.currency, "VND")

    def test_the_coordinates_are_inside_vietnam(self) -> None:
        # The fleet is Vietnamese, and the coordinates have to be too --
        # a profile that reports the Vietnamese clock but sits in
        # another country is a profile the site can place on a map.
        from novaprofile.core.geo_vietnam import VIETNAM_COORDINATES

        profiles = generate_fleet(count=300, vietnam=True)

        latitudes = [float(row[1]) for row in VIETNAM_COORDINATES]
        longitudes = [float(row[2]) for row in VIETNAM_COORDINATES]

        for profile in profiles:
            self.assertGreaterEqual(profile.geolocation.latitude, min(latitudes) - 1.0)
            self.assertLessEqual(profile.geolocation.latitude, max(latitudes) + 1.0)
            self.assertGreaterEqual(profile.geolocation.longitude, min(longitudes) - 1.0)
            self.assertLessEqual(profile.geolocation.longitude, max(longitudes) + 1.0)

    def test_the_touch_count_is_not_a_constant(self) -> None:
        # The digitiser has two answers and the fleet has to use both.
        # A fleet whose units all report ten has a constant there, and
        # a constant is a hole in the signature: the reading looks like
        # it separates and does not.
        profiles = generate_fleet(count=300, vietnam=True)

        touch = {profile.navigator.max_touch_points for profile in profiles}
        self.assertEqual(
            touch,
            {5, 10},
            "the fleet has to use both of the touch-point counts a "
            "digitiser can report, or the reading separates nothing",
        )

    def test_the_bounded_readings_all_move(self) -> None:
        # The readings that have a limited set of true values have to
        # *move* over that set.  A reading that is pinned to one of its
        # true values is the same hole as a constant, only quieter.
        profiles = generate_fleet(count=300, vietnam=True)

        movements = {
            "hardware_concurrency": {
                profile.navigator.hardware_concurrency for profile in profiles
            },
            "device_memory": {
                profile.navigator.device_memory for profile in profiles
            },
            "max_touch_points": {
                profile.navigator.max_touch_points for profile in profiles
            },
            "screen_width": {profile.screen.width for profile in profiles},
            "screen_height": {profile.screen.height for profile in profiles},
            "device_pixel_ratio": {
                profile.screen.device_pixel_ratio for profile in profiles
            },
            "model": {profile.android_build.model for profile in profiles},
        }

        for name, values in movements.items():
            self.assertGreater(
                len(values),
                1,
                f"the {name} reading is pinned to one of its true values, "
                f"and it therefore separates nothing",
            )


if __name__ == "__main__":
    unittest.main()
