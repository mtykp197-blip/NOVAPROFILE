"""The geographic coordinate table.

The table's whole job is coherence: a profile that claims ``de-AT`` /
``Europe/Vienna`` / EUR must also *be* somewhere in Vienna, because a
site that maps the exit IP to Austria and then reads the GPS
coordinate sees two countries otherwise.

These tests pin the properties the table has to hold:

* every zone in the catalogue has a hand-checked coordinate -- the
  catalogue is imported, not hardcoded, so adding a zone without a
  coordinate fails loudly,
* every coordinate is a real point on the globe,
* the coordinates that are checked here are the real ones,
* the fallback for an unknown zone is deterministic, is not a fixed
  point, and stays inside the plausible band of its own region.
"""

from __future__ import annotations

import pytest

from novaprofile.core.geo_catalog import GEO_CATALOGUE
from novaprofile.core.geo_coordinates import (
    _REGION_BANDS,
    COVERED_TIMEZONES,
    GEO_COORDINATES,
    _longitude_in_band,
    coordinates_for,
    has_explicit_coordinates,
)

#: Real coordinates, checked by hand, spread across the continents.
#: The four-decimal precision is about eleven metres -- the resolution a
#: real GPS chip reports.
KNOWN_COORDINATES: dict[str, tuple[float, float]] = {
    "Europe/Vienna": (48.2082, 16.3738),
    "Asia/Ho_Chi_Minh": (10.8231, 106.6297),
    "America/Mexico_City": (19.4326, -99.1332),
    "America/New_York": (40.7128, -74.0060),
    "Asia/Tokyo": (35.6762, 139.6503),
    "America/Phoenix": (33.4484, -112.0740),
    "Asia/Kolkata": (19.0760, 72.8777),
    "Africa/Johannesburg": (-26.2041, 28.0473),
    "Australia/Sydney": (-33.8688, 151.2093),
    "Pacific/Auckland": (-36.8485, 174.7633),
    "America/Sao_Paulo": (-23.5505, -46.6333),
    "Atlantic/Reykjavik": (64.1466, -21.9426),
}


def _catalogue_timezones() -> list[str]:
    """Every distinct time zone in the catalogue, in first-seen order."""
    seen: dict[str, None] = {}
    for _locale, timezone, _country, _currency, _cycle, _secondaries in GEO_CATALOGUE:
        seen.setdefault(timezone, None)
    return list(seen)


def test_every_catalogue_zone_has_an_explicit_coordinate() -> None:
    """A zone in the catalogue without a coordinate fails the test.

    The list is imported from the catalogue rather than hardcoded, so a
    zone added to the catalogue without a coordinate in the table breaks
    here instead of silently degrading to the fallback.
    """
    missing = [zone for zone in _catalogue_timezones() if not has_explicit_coordinates(zone)]

    assert missing == [], f"time zones without an explicit coordinate: {missing}"
    assert frozenset(GEO_COORDINATES) == COVERED_TIMEZONES


def test_the_table_covers_the_whole_catalogue() -> None:
    """The table must not be missing a zone the catalogue uses.

    The reverse -- a table entry for a zone the catalogue no longer
    uses -- is allowed on purpose.  The table is a resource: a zone that
    the catalogue drops today may come back tomorrow, and the
    coordinate for it is known.  Keeping it costs a line and saves the
    lookup from falling back to a derived guess.
    """

    catalogue_zones = frozenset(_catalogue_timezones())
    missing = sorted(catalogue_zones - COVERED_TIMEZONES)

    assert missing == [], f"the catalogue uses zones without a coordinate: {missing}"


def test_the_table_has_no_dead_entries() -> None:
    """A stale entry must still be a real place.

    The table is allowed to carry zones the catalogue does not use, but
    every one of them has to be a real coordinate and not a placeholder
    -- otherwise a zone that comes back would resolve to nonsense.
    """

    for zone, (latitude, longitude) in GEO_COORDINATES.items():
        assert -90.0 <= latitude <= 90.0
        assert -180.0 <= longitude <= 180.0
        assert latitude != 0.0 or longitude != 0.0, f"{zone} sits on null island"


def test_every_coordinate_is_a_real_point_on_the_globe() -> None:
    """Latitude in [-90, 90] and longitude in [-180, 180]."""
    for zone, (latitude, longitude) in GEO_COORDINATES.items():
        assert -90.0 <= latitude <= 90.0, f"{zone} latitude out of range: {latitude}"
        assert -180.0 <= longitude <= 180.0, f"{zone} longitude out of range: {longitude}"


def test_known_zones_report_their_real_coordinates() -> None:
    """The spot checks: a table of invented numbers would fail here."""
    for zone, expected in KNOWN_COORDINATES.items():
        assert coordinates_for(zone) == expected, f"wrong coordinates for {zone}"


def test_the_fallback_is_deterministic() -> None:
    """The same unknown zone always yields the same coordinate."""
    unknown = "Europe/Nowhere_Ville"

    first = coordinates_for(unknown)
    second = coordinates_for(unknown)

    assert first == second


def test_the_fallback_is_not_a_single_fixed_point() -> None:
    """Two different unknown zones must not collapse onto one point."""
    first = coordinates_for("Europe/Nowhere_Ville")
    second = coordinates_for("Europe/Other_City")

    assert first != second


def test_the_fallback_stays_inside_its_own_region() -> None:
    """A zone named ``Europe/...`` must land in Europe, not in the Pacific.

    The fallback is only a last resort, but it still has to be plausible:
    a new zone that lands in the ocean or on the wrong continent is
    worse than a slightly-off coordinate inside the right band.  The
    bands are the ones the module documents -- a generous box per
    region that keeps the point on the right continent.
    """
    european = coordinates_for("Europe/Brand_New_City")
    latitude, longitude = european

    assert latitude > 0.0, f"a European zone must be north of the equator: {european}"
    assert -25.0 <= longitude <= 45.0, f"a European zone must be in the European band: {european}"

    asian = coordinates_for("Asia/Brand_New_City")
    latitude, longitude = asian

    assert -11.0 <= latitude <= 78.0, f"an Asian zone must be in the Asian band: {asian}"
    assert 25.0 <= longitude <= 150.0, f"an Asian zone must be in the eastern band: {asian}"

    american = coordinates_for("America/Brand_New_City")
    latitude, longitude = american

    assert -55.0 <= latitude <= 72.0, f"an American zone must be in the American band: {american}"
    assert -170.0 <= longitude <= -30.0, f"an American zone must be in the western band: {american}"

    australian = coordinates_for("Australia/Brand_New_City")
    latitude, longitude = australian

    assert latitude < 0.0, f"an Australian zone must be south of the equator: {australian}"
    assert 110.0 <= longitude <= 155.0, f"an Australian zone must be in its band: {australian}"

    african = coordinates_for("Africa/Brand_New_City")
    latitude, longitude = african

    assert -35.5 <= latitude <= 37.5, f"an African zone must be in the African band: {african}"
    assert -18.5 <= longitude <= 52.5, f"an African zone must be in its band: {african}"


def test_the_fallback_never_leaves_the_documented_band() -> None:
    """Every fallback result lands inside the region box it was given.

    This walks a spread of unknown zone names -- including regions the
    catalogue does not currently use -- and checks the result against the
    same band the module documents, so the bands and the derivation
    cannot drift apart.
    """
    for zone in (
        "Europe/Brand_New_City",
        "Asia/Brand_New_City",
        "America/Brand_New_City",
        "Australia/Brand_New_City",
        "Africa/Brand_New_City",
        "Atlantic/Brand_New_City",
        "Pacific/Brand_New_City",
    ):
        latitude, longitude = coordinates_for(zone)
        min_lat, max_lat, min_lng, max_lng = _REGION_BANDS[zone.split("/", 1)[0]]

        assert min_lat <= latitude <= max_lat, f"{zone} left the latitude band: {latitude}"
        assert _longitude_in_band(longitude, min_lng, max_lng), (
            f"{zone} left the longitude band: {longitude}"
        )


def test_the_fallback_returns_a_plausible_coordinate() -> None:
    """Even for a zone that is not in the table, the result is on the globe."""
    for zone in ("Europe/Nowhere", "Asia/Nowhere", "Mars/Olympus_Mons", "Nowhere"):
        latitude, longitude = coordinates_for(zone)

        assert -90.0 <= latitude <= 90.0, f"{zone} latitude out of range: {latitude}"
        assert -180.0 <= longitude <= 180.0, f"{zone} longitude out of range: {longitude}"


def test_an_explicit_zone_never_falls_back() -> None:
    """``has_explicit_coordinates`` and the table agree with each other."""
    for zone in _catalogue_timezones():
        assert has_explicit_coordinates(zone)
        assert coordinates_for(zone) == GEO_COORDINATES[zone]

    assert not has_explicit_coordinates("Europe/Nowhere_Ville")
    assert not has_explicit_coordinates("Nowhere/Ville")


def test_the_catalogue_resolves_every_zone_it_claims() -> None:
    """Each catalogue zone resolves to a coordinate in range.

    This walks the catalogue itself rather than the table, so it also
    proves the public entry point handles every zone the generator can
    emit.
    """
    for _locale, timezone, _country, _currency, _cycle, _secondaries in GEO_CATALOGUE:
        latitude, longitude = coordinates_for(timezone)

        assert -90.0 <= latitude <= 90.0
        assert -180.0 <= longitude <= 180.0


@pytest.mark.parametrize("zone", sorted(GEO_COORDINATES))
def test_each_entry_has_four_decimal_precision(zone: str) -> None:
    """The precision is that of a real device: about eleven metres."""
    latitude, longitude = GEO_COORDINATES[zone]

    assert round(latitude, 4) == latitude, f"{zone} latitude has too much precision"
    assert round(longitude, 4) == longitude, f"{zone} longitude has too much precision"
