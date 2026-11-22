"""The fleet has to report the memory and the core count Chromium reports.

Two properties of the fleet were broken at the same time, and both of
them were invisible until a profile was generated from a preset that
tripped them:

* the catalogue held RAM amounts that no browser can report -- three
  gigabytes, ten, eighteen, twenty-four -- and generating a profile from
  one of those presets raised a validation error, because the schema
  only admits the values Chromium produces;
* eighteen presets offered more than one core count for a chipset whose
  core count is a property of the chipset, so the same Snapdragon
  reported eight cores on one handset and twelve on another.

These tests hold the whole fleet to the rules rather than checking a
handful of presets, because the fleet is seven thousand devices wide and
a spot check does not reach it.
"""

from __future__ import annotations

import pytest

from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.hardware_approximation import (
    DEVICE_MEMORY_VALUES,
    approximate_device_memory,
)

#: The presets the fleet is sampled from.  The sample is fixed so that
#: the test is deterministic.
SAMPLE = (
    "redmi-note-12-android13",
    "galaxy-s24-ultra-android14",
    "samsung-galaxy-a14-android13",
    "pixel-8-pro-android14",
    "oneplus-11-android13",
    "vivo-x90-android14",
    "galaxy-a54-android13",
    "xiaomi-14-android14",
)


class TestTheCatalogueHoldsRealHardware:
    """The catalogue describes hardware, not what a page reads."""

    def test_a_chipset_has_one_core_count(self) -> None:
        """The core count belongs to the chipset, not to the unit.

        A site that reads ``navigator.hardwareConcurrency`` and compares
        it against the model sees a contradiction when the same chipset
        reports two different counts.
        """

        by_soc: dict[str, set[int]] = {}
        for preset in list_presets():
            by_soc.setdefault(preset.abi.soc_model, set()).update(preset.cpu_options)

        for soc, counts in by_soc.items():
            assert len(counts) == 1, f"{soc} is listed with core counts {sorted(counts)}"

    def test_every_preset_offers_at_least_one_core_count(self) -> None:
        for preset in list_presets():
            assert preset.cpu_options, f"{preset.preset_id} offers no core count"

    def test_the_physical_memory_is_a_real_amount(self) -> None:
        """The catalogue keeps the physical memory of the handset.

        The value a page reads is Chromium's approximation of it, and
        that approximation is applied by the generator.  The catalogue
        therefore keeps the amount the handset was sold with, which is
        not a power of two -- a 6 GB handset is a 6 GB handset.
        """

        for preset in list_presets():
            for amount in preset.ram_options:
                assert amount > 0, f"{preset.preset_id} offers {amount} GB"


class TestTheProfileReportsWhatChromiumReports:
    """The generated profile reports a value a browser can produce."""

    @pytest.mark.parametrize("preset_id", SAMPLE)
    def test_the_reported_memory_is_a_legal_value(self, preset_id: str) -> None:
        generator = ProfileGenerator()
        for index in range(8):
            config = generator.generate(
                preset_id, master_seed=f"{index:064x}"
            )
            assert config.navigator.device_memory in DEVICE_MEMORY_VALUES

    @pytest.mark.parametrize("preset_id", SAMPLE)
    def test_the_reported_memory_is_the_approximation_of_the_physical_one(
        self, preset_id: str
    ) -> None:
        """The profile reports the approximation, not the raw amount.

        This is the property that was broken: the profile reported the
        physical memory, so a handset with twelve gigabytes reported
        twelve -- a value Chromium never produces on Android.
        """

        preset = next(p for p in list_presets() if p.preset_id == preset_id)

        generator = ProfileGenerator()
        for index in range(16):
            config = generator.generate(
                preset_id, master_seed=f"{index:064x}"
            )
            reported = config.navigator.device_memory
            assert reported in {
                approximate_device_memory(amount) for amount in preset.ram_options
            }, f"{preset_id} reported {reported}, which is not a Chromium value"

    @pytest.mark.parametrize("preset_id", SAMPLE)
    def test_the_reported_core_count_is_one_the_model_offers(
        self, preset_id: str
    ) -> None:
        preset = next(p for p in list_presets() if p.preset_id == preset_id)

        generator = ProfileGenerator()
        for index in range(8):
            config = generator.generate(
                preset_id, master_seed=f"{index:064x}"
            )
            assert config.navigator.hardware_concurrency in preset.cpu_options
