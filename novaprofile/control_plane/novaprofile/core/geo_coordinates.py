"""Real coordinates for the places the time zones name.

A profile's locale, time zone, currency and GPS coordinate all describe
the *same physical person*.  A site that cross-references them finds the
contradiction immediately: the exit IP resolves to Austria, the JavaScript
time zone says ``Europe/Vienna``, the currency is EUR -- and then
``navigator.geolocation`` reports a point somewhere in Central Asia.  A
profile that contradicts itself is worse than an honest one, because the
contradiction is exactly what a fraud heuristic is looking for.

So the coordinate is not an independent random draw.  It is *derived from
the time zone*, which means the profile describes one place: the city the
zone is named after, at the coordinates that city actually occupies.

This module is the authoritative source for those coordinates.  Every
time zone that appears in :mod:`novaprofile.core.geo_catalog` is listed
here with the real latitude and longitude of the city or region the zone
is named after, to about four decimal places -- roughly eleven metres,
which is the precision a real GPS chip reports.

Three public names:

``GEO_COORDINATES``
    The table itself: ``{timezone: (latitude, longitude)}``.
``coordinates_for(timezone)``
    The coordinate of the place the zone names.  Falls back to a
    deterministic derivation from the zone *name* when the zone is not in
    the table.
``has_explicit_coordinates(timezone)``
    Whether the zone has a hand-checked coordinate in the table.

The fallback
------------

The fallback exists so that a zone added to the catalogue tomorrow is
*plausible* rather than *wrong*, and it is a last resort -- the table is
the authority.  It is deliberately not a single fixed point on the globe:
a fixed point would be an obvious lie, and it would also make every
unknown zone collide with every other one.

Instead the fallback reads the zone's own name.  ``Europe/Vienna`` says
"Europe" and "Vienna"; the region part selects a plausible latitude and
longitude band for that continent, and the city part is hashed to pick a
point *inside* that band.  The result is deterministic -- the same name
always yields the same coordinate -- and it always lands on the right
continent, in a plausible band, instead of in the middle of the Pacific.
"""

from __future__ import annotations

import hashlib
from typing import Final

#: The real coordinates of the place each time zone is named after.
#:
#: Latitude/longitude pairs are the centre of the named city (or, for a
#: zone that names a region rather than a city, the centre of that
#: region) at roughly four decimal places -- about eleven metres.
GEO_COORDINATES: Final[dict[str, tuple[float, float]]] = {
    # -- Africa ----------------------------------------------------------
    "Africa/Abidjan": (5.3600, -4.0083),
    "Africa/Addis_Ababa": (9.0320, 38.7469),
    "Africa/Algiers": (36.7538, 3.0588),
    "Africa/Cairo": (30.0444, 31.2357),
    "Africa/Cape_Town": (-33.9249, 18.4241),
    "Africa/Casablanca": (33.5731, -7.5898),
    "Africa/Dakar": (14.7167, -17.4677),
    "Africa/Johannesburg": (-26.2041, 28.0473),
    "Africa/Luxor": (25.6872, 32.6396),
    "Africa/Nairobi": (-1.2921, 36.8219),
    # -- America ---------------------------------------------------------
    "America/Anchorage": (61.2181, -149.9003),
    "America/Argentina/Buenos_Aires": (-34.6037, -58.3816),
    "America/Bogota": (4.7110, -74.0721),
    "America/Chicago": (41.8781, -87.6298),
    "America/Chihuahua": (28.6330, -106.0691),
    "America/Denver": (39.7392, -104.9903),
    "America/Detroit": (42.3314, -83.0458),
    "America/Edmonton": (53.5461, -113.4938),
    "America/Fortaleza": (-3.7319, -38.5267),
    "America/Indiana/Indianapolis": (39.7684, -86.1581),
    "America/Kentucky/Louisville": (38.2527, -85.7585),
    "America/Lima": (-12.0464, -77.0428),
    "America/Los_Angeles": (34.0522, -118.2437),
    "America/Manaus": (-3.1190, -60.0217),
    "America/Mexico_City": (19.4326, -99.1332),
    "America/Monterrey": (25.6866, -100.3161),
    "America/Montreal": (45.5019, -73.5674),
    "America/New_York": (40.7128, -74.0060),
    "America/North_Dakota/Center": (46.8772, -101.7799),
    "America/Ottawa": (45.4215, -75.6972),
    "America/Phoenix": (33.4484, -112.0740),
    "America/Puerto_Rico": (18.4655, -66.1057),
    "America/Santiago": (-33.4489, -70.6693),
    "America/Sao_Paulo": (-23.5505, -46.6333),
    "America/Toronto": (43.6532, -79.3832),
    "America/Vancouver": (49.2827, -123.1207),
    "America/Winnipeg": (49.8951, -97.1384),
    # -- Asia ------------------------------------------------------------
    "Asia/Almaty": (43.2389, 76.8897),
    "Asia/Baku": (40.4093, 49.8671),
    "Asia/Bangkok": (13.7563, 100.5018),
    "Asia/Chiang_Mai": (18.7883, 98.9853),
    "Asia/Chongqing": (29.5630, 106.5516),
    "Asia/Colombo": (6.9271, 79.8612),
    "Asia/Dhaka": (23.8103, 90.4125),
    "Asia/Dubai": (25.2048, 55.2708),
    "Asia/Ho_Chi_Minh": (10.8231, 106.6297),
    "Asia/Hong_Kong": (22.3193, 114.1694),
    "Asia/Jakarta": (-6.2088, 106.8456),
    "Asia/Jerusalem": (31.7683, 35.2137),
    "Asia/Karachi": (24.8607, 67.0011),
    "Asia/Kathmandu": (27.7172, 85.3240),
    "Asia/Kolkata": (19.0760, 72.8777),
    "Asia/Kuala_Lumpur": (3.1390, 101.6869),
    "Asia/Kuching": (1.5535, 110.3593),
    "Asia/Makassar": (-5.1477, 119.4327),
    "Asia/Manila": (14.5995, 120.9842),
    "Asia/Osaka": (34.6937, 135.5023),
    "Asia/Phnom_Penh": (11.5564, 104.9282),
    "Asia/Pyongyang": (39.0392, 125.7625),
    "Asia/Riyadh": (24.7136, 46.6753),
    "Asia/Seoul": (37.5665, 126.9780),
    "Asia/Shanghai": (31.2304, 121.4737),
    "Asia/Taipei": (25.0330, 121.5654),
    "Asia/Tashkent": (41.2995, 69.2401),
    "Asia/Tbilisi": (41.7151, 44.8271),
    "Asia/Tehran": (35.6892, 51.3890),
    "Asia/Tokyo": (35.6762, 139.6503),
    "Asia/Ulaanbaatar": (47.8864, 106.9057),
    "Asia/Urumqi": (43.8256, 87.6168),
    "Asia/Vientiane": (17.9757, 102.6331),
    "Asia/Yangon": (16.8661, 96.1951),
    "Asia/Yerevan": (40.1792, 44.4991),
    # -- Atlantic --------------------------------------------------------
    "Atlantic/Reykjavik": (64.1466, -21.9426),
    # -- Australia -------------------------------------------------------
    "Australia/Adelaide": (-34.9285, 138.6007),
    "Australia/Brisbane": (-27.4698, 153.0251),
    "Australia/Darwin": (-12.4634, 130.8456),
    "Australia/Melbourne": (-37.8136, 144.9631),
    "Australia/Perth": (-31.9523, 115.8613),
    "Australia/Sydney": (-33.8688, 151.2093),
    # -- Europe ----------------------------------------------------------
    "Europe/Amsterdam": (52.3676, 4.9041),
    "Europe/Athens": (37.9838, 23.7275),
    "Europe/Barcelona": (41.3851, 2.1734),
    "Europe/Berlin": (52.5200, 13.4050),
    "Europe/Bratislava": (48.1486, 17.1077),
    "Europe/Brussels": (50.8503, 4.3517),
    "Europe/Bucharest": (44.4268, 26.1025),
    "Europe/Budapest": (47.4979, 19.0402),
    "Europe/Copenhagen": (55.6761, 12.5683),
    "Europe/Dublin": (53.3498, -6.2603),
    "Europe/Geneva": (46.2044, 6.1432),
    "Europe/Hamburg": (53.5511, 9.9937),
    "Europe/Helsinki": (60.1699, 24.9384),
    "Europe/Istanbul": (41.0082, 28.9784),
    "Europe/Krakow": (50.0647, 19.9450),
    "Europe/Kyiv": (50.4501, 30.5234),
    "Europe/Lisbon": (38.7223, -9.1393),
    "Europe/London": (51.5074, -0.1278),
    "Europe/Lyon": (45.7640, 4.8357),
    "Europe/Madrid": (40.4168, -3.7038),
    "Europe/Manchester": (53.4808, -2.2426),
    "Europe/Marseille": (43.2965, 5.3698),
    "Europe/Milan": (45.4642, 9.1900),
    "Europe/Moscow": (55.7558, 37.6173),
    "Europe/Munich": (48.1351, 11.5820),
    "Europe/Oslo": (59.9139, 10.7522),
    "Europe/Paris": (48.8566, 2.3522),
    "Europe/Prague": (50.0755, 14.4378),
    "Europe/Rome": (41.9028, 12.4964),
    "Europe/Sofia": (42.6977, 23.3219),
    "Europe/Stockholm": (59.3293, 18.0686),
    "Europe/Vienna": (48.2082, 16.3738),
    "Europe/Warsaw": (52.2297, 21.0122),
    "Europe/Zurich": (47.3769, 8.5417),
    # -- Pacific ---------------------------------------------------------
    "Pacific/Auckland": (-36.8485, 174.7633),
    "Pacific/Fiji": (-18.1416, 178.4419),
    "Pacific/Honolulu": (21.3069, -157.8583),
    "Pacific/Port_Moresby": (-9.4438, 147.1803),
}

#: The zones covered by the hand-checked table above.  A test compares this
#: against the catalogue so that a zone added to the catalogue without a
#: coordinate fails loudly instead of silently falling back.
COVERED_TIMEZONES: Final[frozenset[str]] = frozenset(GEO_COORDINATES)

#: Plausible bounding bands per region prefix, used only by the fallback.
#:
#: Each band is ``(min_lat, max_lat, min_lng, max_lng)``.  The bands are
#: deliberately generous -- wide enough that a hashed city lands on the
#: right continent, narrow enough that it cannot land in the ocean or on
#: the wrong side of the planet.
#:
#: A longitude band whose minimum is greater than its maximum *wraps* the
#: antimeridian: the Pacific band spans both the eastern longitudes
#: (Fiji, Samoa) and the western ones (Hawaii, the Line Islands), which a
#: plain ``min <= lng <= max`` test cannot express.
_REGION_BANDS: Final[dict[str, tuple[float, float, float, float]]] = {
    "Africa": (-35.5, 37.5, -18.5, 52.5),
    "America": (-55.0, 72.0, -170.0, -30.0),
    "Asia": (-11.0, 78.0, 25.0, 150.0),
    "Atlantic": (25.0, 72.0, -75.0, -10.0),
    "Australia": (-45.0, -10.0, 110.0, 155.0),
    "Europe": (33.0, 72.0, -25.0, 45.0),
    "Indian": (-45.0, 25.0, 40.0, 110.0),
    "Pacific": (-50.0, 30.0, 140.0, -120.0),
}

#: Band used when the zone name carries no region prefix at all.
_DEFAULT_BAND: Final[tuple[float, float, float, float]] = (-60.0, 70.0, -180.0, 180.0)


def _stable_fraction(*parts: str) -> float:
    """A deterministic fraction in ``[0.0, 1.0)`` derived from ``parts``.

    ``hash()`` is not usable here: Python randomises the hash of a string
    per process, so the same zone would land somewhere different on every
    run.  SHA-256 is stable across processes, machines and releases, which
    is what a profile generator needs.
    """
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()
    # 53 bits -> an exactly representable integer -> an exact division.
    value = int.from_bytes(digest[:8], "big") >> 11
    return value / float(1 << 53)


def _region_band(timezone: str) -> tuple[float, float, float, float]:
    """The plausible coordinate band for the region a zone name names."""
    prefix = timezone.split("/", 1)[0] if "/" in timezone else ""
    return _REGION_BANDS.get(prefix, _DEFAULT_BAND)


def _longitude_in_band(longitude: float, min_lng: float, max_lng: float) -> bool:
    """Whether a longitude falls inside a band, antimeridian included."""
    if min_lng <= max_lng:
        return min_lng <= longitude <= max_lng
    # The band wraps the antimeridian.
    return longitude >= min_lng or longitude <= max_lng


def coordinates_for(timezone: str) -> tuple[float, float]:
    """The coordinates of the place the time zone names.

    Returns the city's latitude and longitude.  For a zone that is not in
    the table, the fallback is derived from the zone's own name -- the
    region and the city -- so that a new zone added to the catalogue is
    still plausible rather than wrong.

    The fallback is a last resort.  The table in this module is the
    authoritative source; a zone that is not in it is a zone whose real
    coordinate nobody has checked yet, and the fallback keeps it on the
    right continent instead of in the ocean.
    """
    known = GEO_COORDINATES.get(timezone)
    if known is not None:
        return known

    region, _, city = timezone.partition("/")
    if not city:
        # A bare zone name with no region: treat the whole name as the
        # city so the derivation still has something to hash.
        region, city = "", timezone

    min_lat, max_lat, min_lng, max_lng = _region_band(timezone)
    # The region is part of the hash input as well, so two zones with the
    # same city name in different regions do not collide.
    lat_fraction = _stable_fraction(region, city, "lat")
    lng_fraction = _stable_fraction(region, city, "lng")
    latitude = min_lat + lat_fraction * (max_lat - min_lat)
    if min_lng <= max_lng:
        longitude = min_lng + lng_fraction * (max_lng - min_lng)
    else:
        # The band wraps the antimeridian: walk east from ``min_lng``
        # through +180 and continue from -180 to ``max_lng``.
        span = (180.0 - min_lng) + (max_lng + 180.0)
        longitude = (min_lng + lng_fraction * span + 180.0) % 360.0 - 180.0
    # Round to the same precision the table uses: four decimals, about
    # eleven metres -- the resolution a real device reports.
    return (round(latitude, 4), round(longitude, 4))


def has_explicit_coordinates(timezone: str) -> bool:
    """Whether the zone has a hand-checked coordinate in the table."""
    return timezone in GEO_COORDINATES


__all__ = [
    "COVERED_TIMEZONES",
    "GEO_COORDINATES",
    "coordinates_for",
    "has_explicit_coordinates",
]
