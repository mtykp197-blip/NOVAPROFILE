"""The tests for the surface list and the comparison.

A test that makes its own input cannot fail, so the comparison is
tested against values that a real browser would answer, and the
profile side is taken from the generator rather than written by hand.
"""

from __future__ import annotations

import pytest

from novaprofile.core.surface_compare import FAIL, PASS, UNKNOWN, compare
from novaprofile.core.surfaces import (
    CATEGORIES,
    SURFACES,
    by_category,
    stable_surfaces,
    strong_surfaces,
)


def test_every_surface_is_described() -> None:
    """A surface without a category or a power is not described."""

    for surface in SURFACES:
        assert surface.name
        assert surface.api
        assert surface.category in CATEGORIES
        assert surface.power in {"none", "weak", "medium", "strong"}
        assert isinstance(surface.stable, bool)


def test_the_names_are_unique() -> None:
    """Two surfaces with the same name would shadow each other."""

    names = [surface.name for surface in SURFACES]
    assert len(names) == len(set(names))


def test_the_categories_are_all_used() -> None:
    """A category nobody falls into is a category that was imagined."""

    grouped = by_category()
    assert set(grouped) == set(CATEGORIES)


def test_the_typed_surfaces_are_stable_apart_from_the_password() -> None:
    """What the visitor types does not change between two visits.

    The password is the exception: it is not readable by a script at
    all, so it is neither stable nor useful.
    """

    typed = [surface for surface in SURFACES if surface.category == "typed"]
    assert typed
    for surface in typed:
        if surface.name == "password":
            assert not surface.stable
        else:
            assert surface.stable


def test_the_password_is_not_readable() -> None:
    """A script cannot read a password, so it is worth nothing."""

    password = next(surface for surface in SURFACES if surface.name == "password")
    assert password.power == "none"
    assert not password.stable


def test_there_are_strong_surfaces() -> None:
    """The list has to say which surfaces narrow the field down."""

    assert len(strong_surfaces()) >= 5
    assert len(stable_surfaces()) >= 20


def _profile() -> object:
    """A profile from the generator."""

    from novaprofile.core.devices import all_presets
    from novaprofile.core.generator import ProfileGenerator

    presets = [preset.preset_id for preset in all_presets()]
    return ProfileGenerator().generate(presets[0])


def test_a_matching_measurement_passes() -> None:
    """A measurement that agrees with the profile passes."""

    profile = _profile()
    measured = {
        "user_agent": profile.navigator.user_agent,
        "platform": profile.navigator.platform,
        "language": profile.navigator.language,
        "hardware_concurrency": profile.navigator.hardware_concurrency,
        "screen": {
            "width": profile.screen.width,
            "height": profile.screen.height,
            "avail_width": profile.screen.avail_width,
            "avail_height": profile.screen.avail_height,
            "color_depth": profile.screen.color_depth,
            "pixel_depth": profile.screen.pixel_depth,
        },
    }

    report = compare(profile, measured)

    assert report.failed == []
    assert report.score() > 0.9


def test_a_contradiction_fails() -> None:
    """A measurement that disagrees with the profile fails."""

    profile = _profile()
    measured = {"user_agent": "Mozilla/5.0 (something else entirely)"}

    report = compare(profile, measured)

    assert not report.consistent
    failed = [item for item in report.failed if item.name == "user_agent"]
    assert len(failed) == 1
    assert failed[0].verdict == FAIL


def test_a_surface_the_browser_refused_is_unknown() -> None:
    """A surface the browser did not answer is not a pass."""

    profile = _profile()
    report = compare(profile, {})

    unknown = [item for item in report.unknown if item.name == "user_agent"]
    assert len(unknown) == 1
    assert unknown[0].verdict == UNKNOWN
    assert "user_agent" in report.missing


def test_a_surface_the_profile_lacks_is_reported() -> None:
    """A surface the browser answers and the profile does not is worth knowing."""

    profile = _profile()
    measured = {"cookie": "session=abc123"}

    report = compare(profile, measured)

    entries = [item for item in report.comparisons if item.name == "cookie"]
    assert len(entries) == 1
    assert entries[0].verdict == UNKNOWN
    assert "does not claim it" in entries[0].reason


def test_a_surface_the_profile_carries_is_compared() -> None:
    """A surface the profile carries and the browser answers is compared."""

    profile = _profile()
    measured = {"battery": {"level": 0.8, "charging": False}}

    report = compare(profile, measured)

    entries = [item for item in report.comparisons if item.name == "battery"]
    assert len(entries) == 1
    assert entries[0].verdict in {PASS, FAIL, UNKNOWN}


def test_a_number_that_is_a_string_is_the_same_number() -> None:
    """A number that is a string in one place is the same number."""

    profile = _profile()
    measured = {"hardware_concurrency": str(profile.navigator.hardware_concurrency)}

    report = compare(profile, measured)

    entries = [item for item in report.comparisons if item.name == "hardware_concurrency"]
    assert entries[0].verdict == PASS


def test_the_report_groups_by_category() -> None:
    """The report is read by category."""

    profile = _profile()
    report = compare(profile, {"user_agent": profile.navigator.user_agent})

    grouped = report.by_category()
    assert "navigator" in grouped
    assert all(item.category == "navigator" for item in grouped["navigator"])


@pytest.mark.parametrize("power", ["none", "weak", "medium", "strong"])
def test_every_power_is_used(power: str) -> None:
    """The list says how much each surface narrows the field down."""

    assert any(surface.power == power for surface in SURFACES)


def test_the_verdicts_are_named() -> None:
    """The module agrees with itself about the names of the verdicts."""

    assert PASS == "pass"
    assert FAIL == "fail"
    assert UNKNOWN == "unknown"
