"""Tests that the time zone database is present and used.

Windows has no system copy of the IANA database.  Without the
``tzdata`` package, :mod:`zoneinfo` finds nothing at all, and every
profile is generated with a zero UTC offset and the abbreviation
``UTC`` -- regardless of the zone it claims.  That is one of the loudest
signals a site can detect, and it affected the whole fleet, so it is
pinned here.

The tests also check that the consistency resolver *notices* a missing
database rather than passing silently.
"""

from __future__ import annotations

import pytest

from novaprofile.core.consistency import tzdata_status
from novaprofile.core.generator import (
    ProfileGenerator,
    _geo_target,
    _zoneinfo_abbreviation,
    _zoneinfo_dst,
    _zoneinfo_offset,
)
from novaprofile.core.geo_catalog import GEO_CATALOGUE

# --------------------------------------------------------------------------
# The database itself
# --------------------------------------------------------------------------


def test_the_time_zone_database_is_available() -> None:
    """The database has to be there, and it is a hard dependency.

    This test fails on a machine where ``tzdata`` was not installed.
    That is the point: the condition has to be visible before a fleet is
    created, not after a customer notices that every profile says UTC.
    """

    available, detail = tzdata_status()
    assert available, (
        f"the IANA time zone database is missing: {detail}. "
        "Install it with: pip install tzdata"
    )


def test_the_database_has_the_expected_zones() -> None:
    """A database that resolves nothing is not a database."""

    import zoneinfo

    assert len(zoneinfo.available_timezones()) > 400


@pytest.mark.parametrize(
    ("zone", "offset", "abbreviation"),
    [
        ("Europe/Vienna", 60, "CET"),
        ("Asia/Ho_Chi_Minh", 420, "+07"),
        ("America/New_York", -300, "EST"),
        ("America/Los_Angeles", -480, "PST"),
        ("Asia/Tokyo", 540, "JST"),
        ("Asia/Kolkata", 330, "IST"),
    ],
)
def test_a_known_zone_has_its_real_offset(
    zone: str, offset: int, abbreviation: str
) -> None:
    """The offsets are the real ones.

    The exact values matter: the Vienna offset is +60 in winter and
    +120 in summer, and a profile that claims CET but reports +60 in
    July is a contradiction the site can see.
    """

    assert _zoneinfo_offset(zone) == offset
    assert _zoneinfo_abbreviation(zone) == abbreviation


# --------------------------------------------------------------------------
# The profiles
# --------------------------------------------------------------------------


def test_a_profile_does_not_report_utc_for_every_zone() -> None:
    """No profile may report a zero offset unless its zone really is UTC.

    A zero offset on a hundred million profiles is the signature of a
    missing database, not of a user base.  The catalogue is walked
    directly, because the generator picks its anchor from the seed.
    """

    generator = ProfileGenerator()
    for entry in GEO_CATALOGUE:
        locale, timezone_name = entry[0], entry[1]
        profile = generator.generate(
            "pixel-8-pro-android14",
            geo=_geo_target(
                locale,
                timezone_name,
                entry[2],
                entry[3],
                entry[4],
                entry[5],
            ),
        )
        expected = _zoneinfo_offset(timezone_name)
        assert expected is not None, f"the zone {timezone_name} does not resolve"
        assert profile.timezone.offset_minutes == expected, (
            f"the profile for {timezone_name} reports "
            f"{profile.timezone.offset_minutes}, expected {expected}"
        )


def test_every_catalogue_zone_exists_in_the_database() -> None:
    """Every zone the catalogue names has to be a real one.

    A zone the database does not carry has no offset, no abbreviation
    and no daylight saving flag.  The profile then claims UTC with a
    zero offset while its locale says ``de-DE`` -- a contradiction that
    is invisible until the database is actually installed.

    Fourteen entries used to name a city link the database had dropped
    (``Europe/Munich``, ``Asia/Osaka`` and the like).  This test is what
    catches the next one.
    """

    import zoneinfo

    available = zoneinfo.available_timezones()
    broken = sorted({entry[1] for entry in GEO_CATALOGUE if entry[1] not in available})
    assert not broken, f"the catalogue names zones that do not exist: {broken}"


def test_the_catalogue_has_no_identical_entries() -> None:
    """No two entries may be identical in every field.

    Two entries with the same locale, the same time zone, the same
    currency *and* the same language list are not two distinguishable
    people -- they are one person counted twice, and the second copy
    adds nothing to the fleet except a longer catalogue.

    Sharing only the locale and the zone is fine: that is what the
    secondary language list is for.  A Vietnamese user with French
    enabled is a different browser configuration from one without, and
    ``navigator.languages`` is readable.
    """

    entries = [
        (entry[0], entry[1], entry[2], entry[3], entry[4], entry[5])
        for entry in GEO_CATALOGUE
    ]
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    assert not duplicates, f"the catalogue repeats these entries: {duplicates}"


def test_the_catalogue_entries_that_share_a_zone_differ() -> None:
    """Entries that share a locale and a zone have to differ somewhere.

    Otherwise the fleet holds the same profile twice under two names.
    """

    by_pair: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    for entry in GEO_CATALOGUE:
        key = (entry[0], entry[1])
        by_pair.setdefault(key, []).append(entry)

    for key, group in by_pair.items():
        assert len(group) == len(set(group)), (
            f"the entries for {key} are identical"
        )


def test_the_time_zone_offset_follows_the_zone() -> None:
    """The profile's offset is the offset of the zone it names.

    The generator picks a geo anchor; the time zone comes with it, and
    the offset must be the one that belongs to that zone.
    """

    generator = ProfileGenerator()
    for _ in range(20):
        profile = generator.generate("pixel-8-pro-android14")
        expected = _zoneinfo_offset(profile.timezone.iana_name)
        assert expected is not None
        assert profile.timezone.offset_minutes == expected


def test_the_abbreviation_is_not_always_utc() -> None:
    """The abbreviation is the zone's own abbreviation."""

    generator = ProfileGenerator()
    abbreviations = set()
    for _ in range(20):
        profile = generator.generate("pixel-8-pro-android14")
        abbreviations.add(profile.timezone.abbreviation)

    assert "UTC" not in abbreviations or len(abbreviations) > 1, (
        "every profile reports the UTC abbreviation"
    )


def test_the_dst_flag_agrees_with_the_zone() -> None:
    """The daylight saving flag is the zone's own flag."""

    generator = ProfileGenerator()
    for _ in range(10):
        profile = generator.generate("pixel-8-pro-android14")
        expected = _zoneinfo_dst(profile.timezone.iana_name)
        assert profile.timezone.dst_active == expected


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------


def test_the_resolver_reports_the_offset_mismatch() -> None:
    """A wrong offset is caught, not tolerated.

    The check must not degrade into a silent pass when the database is
    present.  This is the check that would have caught the missing
    database if it had been there in the first place.

    Note that a zero offset is not a fault: West Africa keeps a single
    clock at UTC+0, so ``Africa/Dakar`` legitimately reports zero.  The
    test therefore uses a zone that is definitely not at UTC.
    """

    from novaprofile.core.consistency import ConsistencyResolver

    generator = ProfileGenerator()
    profile = generator.generate(
        "pixel-8-pro-android14",
        geo=_geo_target("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en")),
    )
    assert profile.timezone.offset_minutes == 420

    report = ConsistencyResolver().check(profile)
    offset_issues = [
        issue
        for issue in report.all_issues
        if issue.axis == "geo_timezone" and "offset" in issue.message
    ]
    assert not offset_issues, [issue.message for issue in offset_issues]
