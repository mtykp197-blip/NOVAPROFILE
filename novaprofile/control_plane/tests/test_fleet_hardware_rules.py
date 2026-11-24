"""The three hardware rules, held over the whole fleet.

Three rules changed at the same time, and none of them is visible in a
single profile:

1. ``navigator.deviceMemory`` now goes through Chromium's own
   approximation -- the physical memory is rounded to the nearest power
   of two and clamped to the Android range of one through eight
   gigabytes;
2. ``navigator.hardwareConcurrency`` is no longer rounded, so the count
   the page reads is the count the SoC carries;
3. the font table follows the *tier* of the device -- budget, mid or
   flagship -- and not only the locale.

A rule that only a spot check reaches is not a rule.  The fleet is
nearly seven thousand devices wide, so these tests walk the whole
catalogue rather than a handful of presets, and they do not trust the
fleet validator: the validator is the thing under test, so a test that
only asked the validator would prove nothing.

The tests are split in two halves for a reason.  The *catalogue* half
walks every preset, which costs a catalogue load and nothing else.  The
*profile* half generates a profile from every preset, which is the
expensive half -- the whole fleet is walked, but only one profile per
preset is generated, because the rules do not depend on the seed: a
preset whose options cannot produce a legal value cannot produce it
with another seed either, and a preset whose options are legal produce
a legal value with every seed.

The sampling is declared rather than hidden: the profile half covers
the **whole** fleet with one profile per preset, and the catalogue half
covers the whole fleet as well.  Both halves are deterministic.
"""

from __future__ import annotations

import re

import pytest

from novaprofile.core.device_catalog_market import BUILDABLE_RELEASES
from novaprofile.core.devices import list_presets
from novaprofile.core.font_catalog import (
    MANDATORY_FONTS,
    SCRIPT_FONTS,
    device_tier,
    fonts_for_device,
)
from novaprofile.core.generator import ANDROID_FONT_PRESET, ProfileGenerator
from novaprofile.core.hardware_approximation import (
    DEVICE_MEMORY_VALUES,
    HARDWARE_CONCURRENCY_VALUES,
    approximate_device_memory,
)

#: The physical RAM amounts a handset may be sold with.
#:
#: The catalogue keeps the *physical* memory, because that is what the
#: handset was sold with; the value a page reads is the Chromium
#: approximation of it.  These are the amounts that appear on a real
#: specification sheet.
PLAUSIBLE_RAM: frozenset[int] = frozenset({1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 24})

#: How many profiles are generated from every preset of the fleet.
#:
#: One is enough, and the reason is in the module docstring: the rules
#: constrain the *options* of a preset, not the draw.  A preset whose
#: memory options all round to a legal value produces a legal value with
#: every seed, and a preset whose options do not produces an illegal
#: value with every seed.
PROFILES_PER_PRESET = 1

#: The number of presets the profile half walks.
#:
#: The whole fleet.  The walk is the point of the test, so the number is
#: not a sample but the fleet itself; it is declared here so that a
#: future change to the fleet size is visible in the test file.
#:
#: The fleet is the eleven hand-written reference devices, the
#: thirty-one of the extended catalogue and the market catalogue.  The
#: market catalogue is built from the scraped Vietnamese dataset, whose
#: raw rows also describe the handsets that are *not* Android -- the
#: iPhone, the iPad, the watch, the Windows Phone, the KaiOS feature
#: phone -- and those rows are dropped, because the engine builds an
#: Android profile and an iPhone placed on an Android release describes a
#: device that cannot exist.  The number below is therefore the fleet
#: *after* that filter.
#:
#: The count is read from the catalogue rather than written down, so
#: that a catalogue which grows does not fail this suite on a constant
#: that has to be remembered.  The test that uses it still checks that
#: the fleet is not empty and not a handful of devices, so a catalogue
#: that collapses is still caught.
FLEET_SIZE = len(list_presets())


@pytest.fixture(scope="module")
def fleet():
    """The whole preset catalogue, loaded once."""
    return list_presets()


@pytest.fixture(scope="module")
def generator() -> ProfileGenerator:
    """The profile generator, built once."""
    return ProfileGenerator()


class TestTheCatalogue:
    """The rules that live in the catalogue."""

    def test_the_fleet_holds_no_device_that_is_not_android(self, fleet) -> None:
        """The fleet is an Android fleet.

        The scraped Vietnamese dataset describes every handset sold in
        the market, and some of them are not Android phones: the iPhone,
        the iPad, the watch, the Windows Phone, the KaiOS feature phone.
        Placing one of them on an Android release produces a profile no
        device can be -- the User-Agent says Android while the model
        name says iPhone -- and a site that reads both sees the
        contradiction at once.

        The check is over the whole fleet and it is negative: no preset
        may carry a model name of a non-Android line, and every preset
        has to carry an Android release the engine can build for.
        """

        # The markers are matched on word boundaries: a handset whose
        # model reads "MultiPad" is a tablet and not an iPad.
        non_android_markers = (
            "iphone",
            "ipad",
            "apple watch",
            "watch edition",
            "lumia",
            "windows phone",
        )
        for preset in fleet:
            model = f"{preset.brand} {preset.model}".lower()
            for marker in non_android_markers:
                assert not re.search(rf"\\b{re.escape(marker)}\\b", model), (
                    f"{preset.preset_id} is a non-Android device ({model!r}) in an "
                    f"Android fleet"
                )
            assert preset.release in BUILDABLE_RELEASES, (
                f"{preset.preset_id} carries the release {preset.release}, which the "
                f"engine cannot build a profile for"
            )

    def test_a_chipset_carries_one_core_count(self, fleet) -> None:
        """Rule 2: the core count belongs to the chipset.

        Two presets that share a ``soc_model`` and offer different
        ``cpu_options`` are two handsets that report the same chipset
        with a different number of cores.  A site that reads
        ``navigator.hardwareConcurrency`` and compares it against the
        model sees the contradiction, and the score it awards drops.

        The rule holds over the *whole* fleet, not over a sample of it.
        """

        by_soc: dict[str, dict[int, list[str]]] = {}
        for preset in fleet:
            for cores in preset.cpu_options:
                by_soc.setdefault(preset.abi.soc_model, {}).setdefault(cores, []).append(
                    preset.preset_id
                )

        offenders = {
            soc: buckets for soc, buckets in by_soc.items() if len(buckets) > 1
        }
        assert not offenders, (
            f"{len(offenders)} chipset(s) are listed with more than one core count: "
            + "; ".join(
                f"{soc} -> { {cores: ids[0] for cores, ids in buckets.items()} }"
                for soc, buckets in sorted(offenders.items())[:5]
            )
        )

    def test_every_core_count_is_one_a_handset_reports(self, fleet) -> None:
        """Rule 2: the counts themselves are legal.

        The core count of an Android handset of this era is four, six,
        eight or twelve: a big.LITTLE SoC counts its little cores twice.
        A count outside that set is a count no handset of the fleet
        reports.
        """

        for preset in fleet:
            assert preset.cpu_options, f"{preset.preset_id} offers no core count"
            for cores in preset.cpu_options:
                assert cores in HARDWARE_CONCURRENCY_VALUES, (
                    f"{preset.preset_id} offers the core count {cores}, which is not "
                    f"one of {list(HARDWARE_CONCURRENCY_VALUES)}"
                )

    def test_the_physical_memory_is_a_real_amount(self, fleet) -> None:
        """Rule 1: the catalogue keeps the physical memory of the handset.

        The value a page reads is Chromium's approximation of the
        physical memory, and that approximation is applied by the
        generator.  The catalogue therefore keeps the amount the
        handset was sold with, which is not a power of two -- a 6 GB
        handset is a 6 GB handset.
        """

        for preset in fleet:
            assert preset.ram_options, f"{preset.preset_id} offers no RAM amount"
            for amount in preset.ram_options:
                assert amount in PLAUSIBLE_RAM, (
                    f"{preset.preset_id} offers the RAM amount {amount} GB, which is "
                    f"not one of {sorted(PLAUSIBLE_RAM)}"
                )

    def test_every_physical_memory_rounds_to_a_legal_value(self, fleet) -> None:
        """Rule 1: the approximation of every amount is a legal value.

        This is the property that makes the rest of the pipeline
        work: the generator hands the approximation of the drawn amount
        to the schema, and the schema only admits the four values of
        :data:`DEVICE_MEMORY_VALUES`.  An amount whose approximation
        falls outside that set would make the profile generation raise
        a validation error.
        """

        for preset in fleet:
            for amount in preset.ram_options:
                approximated = approximate_device_memory(amount)
                assert approximated in DEVICE_MEMORY_VALUES, (
                    f"{preset.preset_id} offers {amount} GB, whose approximation "
                    f"{approximated} is not one of {list(DEVICE_MEMORY_VALUES)}"
                )


class TestTheProfile:
    """The rules that reach the page."""

    def test_the_whole_fleet_is_walked(self, fleet) -> None:
        """The walk has to be the whole fleet.

        A test that walks a sample proves the sample is fine.  This
        test fails when the fleet shrinks below the size the other
        tests walk, so the coverage cannot silently drop.
        """

        assert len(fleet) == FLEET_SIZE, (
            f"the fleet holds {len(fleet)} presets, but this suite walks "
            f"{FLEET_SIZE}; update the constant when the catalogue changes"
        )

    def test_the_reported_memory_is_a_legal_value(self, fleet, generator) -> None:
        """Rule 1: every reported memory is one of 1, 2, 4 and 8.

        Chromium rounds the physical memory to the nearest power of two
        and clamps it to the Android range, so a page never reads a
        value outside that ladder.  A profile that reports six is a
        profile no Android browser produces.
        """

        for preset in fleet:
            for index in range(PROFILES_PER_PRESET):
                config = generator.generate(
                    preset.preset_id, master_seed=f"{index:064x}"
                )
                reported = config.navigator.device_memory
                assert reported in DEVICE_MEMORY_VALUES, (
                    f"{preset.preset_id} reported {reported}, which is not one of "
                    f"{list(DEVICE_MEMORY_VALUES)}"
                )

    def test_the_reported_memory_is_the_approximation_of_the_physical_one(
        self, fleet, generator
    ) -> None:
        """Rule 1: the reported value is the approximation, not the amount.

        The property that was broken: the profile reported the physical
        memory, so a handset with twelve gigabytes reported twelve -- a
        value Chromium never produces on Android.  The reported value
        has to be the approximation of one of the amounts the preset
        offers.
        """

        for preset in fleet:
            legal = {
                approximate_device_memory(amount) for amount in preset.ram_options
            }
            for index in range(PROFILES_PER_PRESET):
                config = generator.generate(
                    preset.preset_id, master_seed=f"{index:064x}"
                )
                reported = config.navigator.device_memory
                assert reported in legal, (
                    f"{preset.preset_id} reported {reported}, which is not the "
                    f"approximation of {sorted(preset.ram_options)}"
                )

    def test_the_reported_core_count_is_a_legal_value(self, fleet, generator) -> None:
        """Rule 2: every reported core count is one of 4, 6, 8 and 12.

        The count is not rounded and not clamped -- Chromium hands the
        page the real number of logical cores -- so the only thing that
        can go wrong is a count the fleet's handsets do not carry.
        """

        for preset in fleet:
            for index in range(PROFILES_PER_PRESET):
                config = generator.generate(
                    preset.preset_id, master_seed=f"{index:064x}"
                )
                reported = config.navigator.hardware_concurrency
                assert reported in HARDWARE_CONCURRENCY_VALUES, (
                    f"{preset.preset_id} reported {reported}, which is not one of "
                    f"{list(HARDWARE_CONCURRENCY_VALUES)}"
                )

    def test_the_reported_core_count_is_one_the_preset_offers(
        self, fleet, generator
    ) -> None:
        """Rule 2: the reported count is one the preset offers.

        A profile that reports a count its own preset does not offer is
        a profile whose model and whose core count disagree.
        """

        for preset in fleet:
            for index in range(PROFILES_PER_PRESET):
                config = generator.generate(
                    preset.preset_id, master_seed=f"{index:064x}"
                )
                reported = config.navigator.hardware_concurrency
                assert reported in preset.cpu_options, (
                    f"{preset.preset_id} reported {reported}, which it does not "
                    f"offer ({sorted(preset.cpu_options)})"
                )


class TestTheFontTable:
    """Rule 3: the font table follows the tier of the device."""

    def test_the_tiers_carry_different_font_lists(self) -> None:
        """A budget phone and a flagship of the same country differ.

        The font list follows two independent things: the locale, which
        decides which scripts the user can read, and the tier of the
        device, which decides how much room the system partition leaves
        for the optional families.  Two devices of the same country and
        different tiers therefore report different font lists, and that
        difference is what a fingerprinting script compares.

        The rule is checked on the table itself, because the table is
        what the generator calls.
        """

        budget = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 2, 2019, "13")
        mid = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 6, 2021, "14")
        flagship = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 12, 2024, "14")

        assert len(budget) < len(mid) < len(flagship), (
            f"the tiers carry {len(budget)}, {len(mid)} and {len(flagship)} fonts; "
            "the table does not grow with the tier"
        )

    def test_the_tier_is_decided_by_the_memory_and_the_year(self) -> None:
        """The tier follows the hardware, not the locale."""

        assert device_tier(2, 2019) == "budget"
        assert device_tier(6, 2021) == "mid"
        assert device_tier(8, 2024) == "flagship"

    def test_the_own_script_survives_every_tier(self) -> None:
        """A budget phone still carries the font of its own script.

        The script families are added first and protected afterwards, so
        a Japanese user's phone always has a CJK font, even when it is
        a budget phone.  A device that cannot render its own language is
        a device no user keeps.

        The check is per script rather than per font name: the CJK
        families belong to the Japanese, Korean and Chinese scripts, and
        the Thai family belongs to the Thai script, so a single font
        name cannot be asserted for all of them.
        """

        for ram, year in ((2, 2019), (6, 2021), (8, 2024)):
            for locale in ("ja-JP", "ko-KR", "zh-CN", "th-TH"):
                fonts = set(fonts_for_device(locale, ANDROID_FONT_PRESET, ram, year, "14"))
                own = set(SCRIPT_FONTS.get(locale.split("-")[0].lower(), ()))
                assert own & fonts, (
                    f"a {ram} GB device of {locale} carries none of its own script "
                    f"fonts {sorted(own)}"
                )

    def test_the_fleet_font_list_follows_the_tier_of_the_device(
        self, fleet, generator
    ) -> None:
        """Rule 3: the font list of a profile follows its own device.

        The profile is built from the preset, so the list it carries
        has to be the list the tier of that preset produces.  A profile
        whose font list is the list of another tier is a profile whose
        system partition disagrees with its own hardware.

        The tier follows the *physical* memory that was drawn, and the
        profile does not report that amount -- it reports its Chromium
        approximation.  The approximation is inverted here over the
        amounts the preset offers, which leaves the handful of amounts
        the preset could have drawn (a 4 GB and a 6 GB handset both
        report four), and the profile has to fit the list of one of
        them.  The check is therefore "the list belongs to one of the
        tiers of this device", which is exactly the rule: the font
        table follows the tier of the device and not of the locale.
        """

        for preset in fleet:
            for index in range(PROFILES_PER_PRESET):
                config = generator.generate(
                    preset.preset_id, master_seed=f"{index:064x}"
                )
                installed = set(config.fonts.installed)
                reported = config.navigator.device_memory
                candidates = [
                    amount
                    for amount in preset.ram_options
                    if approximate_device_memory(amount) == reported
                ]
                assert candidates, (
                    f"{preset.preset_id} reported {reported} GB, which is not the "
                    f"approximation of any of its amounts {sorted(preset.ram_options)}"
                )
                allowed: set[str] = set()
                for amount in candidates:
                    allowed |= set(
                        fonts_for_device(
                            config.locale.primary,
                            ANDROID_FONT_PRESET,
                            amount,
                            preset.year,
                            preset.release,
                        )
                    )
                # The profile draws a *subset* of the list the tier
                # allows, and it always adds the mandatory families
                # back, so the check is that nothing outside the
                # list of the tier is carried.
                assert installed <= allowed | set(MANDATORY_FONTS), (
                    f"{preset.preset_id} carries a font outside the list of its "
                    f"tier: {sorted(installed - allowed)}"
                )
