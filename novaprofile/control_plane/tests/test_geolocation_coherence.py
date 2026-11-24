"""Tests that the geography of a profile is one place.

The profile carries a locale, a time zone, a currency, a country and a
GPS coordinate.  A site that reads two of them and compares them finds
the contradiction: a profile that claims ``de-AT`` / ``Europe/Vienna``
but reports a coordinate in Central Asia is not a person, it is a
soup of unrelated values.

These tests pin that the coordinate belongs to the time zone.
"""

from __future__ import annotations

import math

import pytest

from novaprofile.core.generator import ProfileGenerator, _geo_target
from novaprofile.core.geo_catalog import GEO_CATALOGUE
from novaprofile.core.geo_coordinates import coordinates_for
from novaprofile.core.geo_vietnam import (
    VIETNAM_COORDINATES,
    VIETNAM_TIMEZONE,
)

#: How far from the reference point of the city a profile may sit.
#: The jitter is a few kilometres; anything beyond this is not the city.
MAX_OFFSET_KM = 25.0

#: The reference points, for the tests that check the magnitude.
KNOWN_POINTS = {
    "Europe/Vienna": (48.2082, 16.3738),
    "Asia/Ho_Chi_Minh": (10.8231, 106.6297),
    "America/New_York": (40.7128, -74.0060),
    "Asia/Tokyo": (35.6762, 139.6503),
    "America/Sao_Paulo": (-23.5505, -46.6333),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """The distance between two points on the globe, in kilometres."""

    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def _profile_with_geo(locale: str, timezone: str, country: str, currency: str):
    """A profile anchored at one place."""

    return ProfileGenerator().generate(
        "pixel-8-pro-android14",
        geo=_geo_target(locale, timezone, country, currency, "h23", (locale.split("-")[0], "en")),
    )


#: The Vietnamese provinces and the distance from the reference point of
#: the country that a province may sit at.
#
#: A Vietnamese profile is placed in a *province*, not in the middle of
#: the reference point of Ho Chi Minh City: a fleet of a hundred million
#: profiles cannot all live in one province, and a site that sees them
#: all in one province knows it is looking at a farm.  So the province is
#: drawn from the table, and the jitter inside the province separates
#: two profiles that landed in the same one.
VIETNAM_MAX_PROVINCE_OFFSET_KM = 60.0


def _vietnam_provinces() -> tuple[tuple[str, float, float], ...]:
    """The Vietnamese provinces with their centres."""

    return VIETNAM_COORDINATES


def _nearest_vietnam_province(latitude: float, longitude: float):
    """The province whose centre is closest to the given point."""

    best = None
    best_distance = None
    for name, lat, lng in _vietnam_provinces():
        distance = _haversine_km(lat, lng, latitude, longitude)
        if best_distance is None or distance < best_distance:
            best = (name, lat, lng)
            best_distance = distance
    assert best is not None
    return best, float(best_distance or 0.0)


# --------------------------------------------------------------------------
# The coordinate belongs to the place
# --------------------------------------------------------------------------


@pytest.mark.parametrize("timezone", sorted(KNOWN_POINTS))
def test_the_coordinate_is_inside_the_city(timezone: str) -> None:
    """A profile's position is within the city its time zone names.

    The old implementation drew the coordinate uniformly from the whole
    globe, so this test failed for every zone -- a profile claiming
    Vienna was somewhere in Central Asia.

    Vietnam is the one place where the rule is different, and the
    difference is deliberate: the country keeps a single clock, so the
    time zone names the country rather than a city, and the profile is
    placed in a province inside it.
    """

    expected_lat, expected_lng = KNOWN_POINTS[timezone]
    found = None
    for _ in range(40):
        profile = _profile_with_geo(
            "en-US", timezone, "US", "USD"
        )
        if profile.geolocation.mode == "allow_custom":
            found = profile.geolocation
            break

    assert found is not None, "the profile never produced a coordinate"

    if timezone == VIETNAM_TIMEZONE:
        (_, province_lat, province_lng), distance = _nearest_vietnam_province(
            found.latitude, found.longitude
        )
        assert distance <= VIETNAM_MAX_PROVINCE_OFFSET_KM, (
            f"the Vietnamese profile is {distance:.1f} km from its province"
        )
        return

    distance = _haversine_km(
        expected_lat, expected_lng, found.latitude, found.longitude
    )
    assert distance <= MAX_OFFSET_KM, (
        f"the profile for {timezone} is {distance:.1f} km from the city"
    )


def test_every_catalogue_zone_produces_a_local_coordinate() -> None:
    """Every zone in the catalogue produces a coordinate inside itself.

    The whole catalogue is walked, because a single working zone does
    not prove the rest.

    Vietnam is checked against the province it lands in rather than
    against the reference point of the country: the country keeps a
    single clock, so the time zone names the country, and the profile
    is placed in a province inside it.
    """

    generator = ProfileGenerator()
    for entry in GEO_CATALOGUE:
        locale, timezone, country, currency = entry[0], entry[1], entry[2], entry[3]
        base_lat, base_lng = coordinates_for(timezone)

        found = None
        for _ in range(30):
            profile = generator.generate(
                "pixel-8-pro-android14",
                geo=_geo_target(
                    locale, timezone, country, currency, entry[4], entry[5]
                ),
            )
            if profile.geolocation.mode == "allow_custom":
                found = profile.geolocation
                break

        assert found is not None, f"no coordinate for {timezone}"

        if timezone == VIETNAM_TIMEZONE:
            (_, province_lat, province_lng), distance = _nearest_vietnam_province(
                found.latitude, found.longitude
            )
            assert distance <= VIETNAM_MAX_PROVINCE_OFFSET_KM, (
                f"{timezone}: the position is {distance:.1f} km from its province"
            )
            continue

        distance = _haversine_km(
            base_lat, base_lng, found.latitude, found.longitude
        )
        assert distance <= MAX_OFFSET_KM, (
            f"the profile for {timezone} is {distance:.1f} km from its reference point"
        )


# --------------------------------------------------------------------------
# The accuracy has to cover the position
# --------------------------------------------------------------------------


def test_the_accuracy_covers_the_jitter() -> None:
    """The reported radius has to contain the reported position.

    A fix that is more precise than the accuracy it claims is a
    contradiction that a site can check by comparing the two numbers.
    """

    generator = ProfileGenerator()
    for entry in GEO_CATALOGUE[:20]:
        locale, timezone, country, currency = entry[0], entry[1], entry[2], entry[3]
        base_lat, base_lng = coordinates_for(timezone)
        for _ in range(20):
            profile = generator.generate(
                "pixel-8-pro-android14",
                geo=_geo_target(
                    locale, timezone, country, currency, entry[4], entry[5]
                ),
            )
            geolocation = profile.geolocation
            if geolocation.mode != "allow_custom":
                continue
            distance = _haversine_km(
                base_lat, base_lng, geolocation.latitude, geolocation.longitude
            )
            assert geolocation.accuracy >= distance, (
                f"{timezone}: the position is {distance:.0f} m away but the "
                f"accuracy is only {geolocation.accuracy:.0f} m"
            )


# --------------------------------------------------------------------------
# The coordinate is inside the country
# --------------------------------------------------------------------------


def test_the_coordinate_is_on_the_right_continent() -> None:
    """The longitude band follows the region of the time zone.

    A coarse check that catches the gross failures -- a coordinate in
    the Pacific for a European zone -- without pretending to know
    every border.
    """

    generator = ProfileGenerator()
    for entry in GEO_CATALOGUE:
        locale, timezone, country, currency = entry[0], entry[1], entry[2], entry[3]
        base_lat, base_lng = coordinates_for(timezone)
        for _ in range(20):
            profile = generator.generate(
                "pixel-8-pro-android14",
                geo=_geo_target(
                    locale, timezone, country, currency, entry[4], entry[5]
                ),
            )
            geolocation = profile.geolocation
            if geolocation.mode != "allow_custom":
                continue
            if timezone == VIETNAM_TIMEZONE:
                _, distance = _nearest_vietnam_province(
                    geolocation.latitude, geolocation.longitude
                )
                assert distance <= VIETNAM_MAX_PROVINCE_OFFSET_KM
                break
            distance = _haversine_km(
                base_lat, base_lng, geolocation.latitude, geolocation.longitude
            )
            assert distance <= MAX_OFFSET_KM
            break
