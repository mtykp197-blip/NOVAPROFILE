"""The font catalogue has to follow the device, not only the locale.

A site that compares a device with a device it saw yesterday compares
the font list.  If two profiles of the same country report the same
fonts no matter what they are, the fleet collapses into one bucket;
and if a budget phone reports the font set of a flagship, the profile
is not physically possible.

The rules the tests hold:

* the mandatory families are on every device, whatever its tier,
* the script family of the locale survives every tier, because a
  Japanese user's phone always has a CJK font,
* a budget device carries strictly fewer families than a flagship,
* the RAM a profile reports is one the model was actually sold with.
"""

from __future__ import annotations

import pytest

from novaprofile.core.devices import get_preset
from novaprofile.core.font_catalog import (
    BUDGET_TIER_DROPPED,
    LOW_TIER_DROPPED,
    MANDATORY_FONTS,
    SCRIPT_FONTS,
    device_tier,
    fonts_for_device,
)
from novaprofile.core.generator import ANDROID_FONT_PRESET, ProfileGenerator
from novaprofile.core.hardware_approximation import approximate_device_memory

#: The locales that the catalogue has a script set for.
SCRIPT_LOCALES = ("ja-JP", "ko-KR", "zh-CN", "he-IL", "ar-EG", "hi-IN", "th-TH")

#: The three device tiers, as (tier, ram, year) triples.
TIER_SAMPLES = (
    ("budget", 2, 2021),
    ("mid", 6, 2023),
    ("flagship", 12, 2024),
)


class TestTierRules:
    """The tier follows the RAM and the model year."""

    @pytest.mark.parametrize(
        ("ram", "year", "expected"),
        [
            (2, 2021, "budget"),
            (3, 2022, "budget"),
            (4, 2023, "budget"),
            (6, 2023, "mid"),
            (8, 2024, "flagship"),
            (8, 2021, "mid"),
            (12, 2024, "flagship"),
            (16, 2025, "flagship"),
        ],
    )
    def test_the_tier_follows_the_ram_and_the_year(
        self, ram: int, year: int, expected: str
    ) -> None:
        assert device_tier(ram, year) == expected

    def test_an_old_flagship_is_not_a_new_flagship(self) -> None:
        """A 2019 phone that still runs Android 11 is not a 2024 phone."""
        assert device_tier(8, 2019) != "flagship"


class TestFontSetShape:
    """The set itself has the right shape."""

    @pytest.mark.parametrize("locale", SCRIPT_LOCALES)
    @pytest.mark.parametrize(("tier", "ram", "year"), TIER_SAMPLES)
    def test_the_mandatory_families_are_always_present(
        self, locale: str, tier: str, ram: int, year: int
    ) -> None:
        fonts = fonts_for_device(locale, ANDROID_FONT_PRESET, ram, year, "14")

        assert MANDATORY_FONTS.issubset(set(fonts))

    @pytest.mark.parametrize("locale", SCRIPT_LOCALES)
    def test_the_script_family_survives_every_tier(self, locale: str) -> None:
        """A Japanese user's phone has a CJK font even if it is a budget one."""
        primary = locale.split("-")[0].lower()
        expected = SCRIPT_FONTS.get(primary, ())
        if not expected:
            pytest.skip(f"no script family for {primary}")

        for tier, ram, year in TIER_SAMPLES:
            fonts = fonts_for_device(locale, ANDROID_FONT_PRESET, ram, year, "14")
            for name in expected:
                assert name in fonts, f"{name} missing from the {tier} set"

    def test_a_budget_device_carries_fewer_families(self) -> None:
        budget = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 2, 2021, "12")
        mid = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 6, 2023, "13")
        flagship = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 12, 2024, "14")

        assert len(budget) < len(mid) <= len(flagship)

    def test_the_budget_set_drops_the_decorative_families(self) -> None:
        budget = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 2, 2021, "12")

        for name in ("Dancing Script", "Coming Soon", "Carrois Gothic SC"):
            assert name not in budget

    def test_the_flagship_set_keeps_the_decorative_families(self) -> None:
        flagship = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 12, 2024, "14")

        for name in ("Dancing Script", "Coming Soon", "Carrois Gothic SC"):
            assert name in flagship

    def test_the_dropped_families_are_actually_drops(self) -> None:
        """Every name in the drop lists must be a name the catalogue knows.

        A drop list that names a family the catalogue never had is a
        no-op, and it hides a typo in the list.  The set to check
        against is the *full* set -- the Latin core plus every script
        family the catalogue can add -- and not the Latin core alone,
        because most of the drop list names a script family.
        """
        full = set(ANDROID_FONT_PRESET)
        for names in SCRIPT_FONTS.values():
            full.update(names)

        for name in LOW_TIER_DROPPED:
            assert name in full, f"{name} is dropped but was never in the catalogue"

        for name in BUDGET_TIER_DROPPED:
            assert name in full, f"{name} is dropped but was never in the catalogue"

    def test_the_dropped_families_do_not_shrink_to_nothing(self) -> None:
        """Trimming must not empty the set."""
        budget = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 2, 2021, "10")

        assert len(budget) >= 5

    def test_a_hebrew_device_has_no_cjk_font(self) -> None:
        fonts = fonts_for_device("he-IL", ANDROID_FONT_PRESET, 12, 2024, "14")

        assert "Noto Sans CJK JP" not in fonts
        assert "Noto Sans Hebrew" in fonts


class TestRamFollowsTheModel:
    """The RAM a profile reports must be one the model was sold with."""

    @pytest.mark.parametrize(
        "preset_id",
        [
            "redmi-note-12-android13",
            "galaxy-s24-ultra-android14",
            "samsung-galaxy-a14-android13",
            "pixel-8-pro-android14",
            "oneplus-11-android13",
            "vivo-x90-android14",
        ],
    )
    def test_the_reported_ram_is_one_of_the_model_options(self, preset_id: str) -> None:
        """The reported memory is the approximation of a real option.

        The catalogue holds the *physical* memory of the handset -- the
        amounts the model was sold with -- and the profile reports
        Chromium's approximation of it.  So the reported value is the
        approximation of one of the options, not the option itself.
        """

        preset = get_preset(preset_id)
        assert preset is not None

        generator = ProfileGenerator()
        config = generator.generate(preset_id, master_seed="a" * 64)

        approximations = {
            approximate_device_memory(amount) for amount in preset.ram_options
        }
        assert config.navigator.device_memory in approximations

    def test_a_budget_phone_never_reports_sixteen_gigabytes(self) -> None:
        """The 4 GB Redmi Note cannot report 16 GB."""
        preset = get_preset("samsung-galaxy-a14-android13")
        assert preset is not None
        assert 16 not in preset.ram_options

        generator = ProfileGenerator()
        for index in range(12):
            config = generator.generate(
                "samsung-galaxy-a14-android13",
                master_seed=f"{index:064x}",
            )
            approximations = {
                approximate_device_memory(amount) for amount in preset.ram_options
            }
            assert config.navigator.device_memory in approximations
            assert config.navigator.device_memory != 16

    def test_the_cores_follow_the_model_options(self) -> None:
        preset = get_preset("redmi-note-12-android13")
        assert preset is not None

        generator = ProfileGenerator()
        for index in range(8):
            config = generator.generate(
                "redmi-note-12-android13",
                master_seed=f"{index:064x}",
            )
            assert config.navigator.hardware_concurrency in preset.cpu_options


class TestFontTierMatchesRam:
    """The font set and the RAM have to agree with each other."""

    def test_a_4_gigabyte_device_carries_the_budget_font_set(self) -> None:
        preset = get_preset("redmi-note-12-android13")
        assert preset is not None
        assert 4 in preset.ram_options

        generator = ProfileGenerator()
        config = generator.generate(
            "redmi-note-12-android13",
            master_seed="b" * 64,
        )
        if config.navigator.device_memory != 4:
            pytest.skip("this seed drew a different RAM option")

        fonts = set(config.fonts.installed)
        assert "Dancing Script" not in fonts

    def test_the_font_set_differs_between_the_tiers_of_one_model(self) -> None:
        """Two RAM options of one model give two font sets."""
        budget = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 4, 2023, "13")
        flagship = fonts_for_device("vi-VN", ANDROID_FONT_PRESET, 12, 2024, "14")

        assert budget != flagship
        assert len(budget) < len(flagship)
