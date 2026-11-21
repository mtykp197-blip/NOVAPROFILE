"""Exit-IP geo intelligence.

The service maps the public IP address of a proxy exit node onto a
country / region / city / ASN / ISP tuple, and derives the timezone, locale
and geolocation that are *consistent* with that exit node.

Design notes
------------
* All HTTP traffic goes through a single overridable hook, :meth:`GeoService.
  _fetch_json`.  Tests monkeypatch that method (or inject a fake
  ``httpx.AsyncClient``) so no test ever touches the network.
* Several provider shapes are supported out of the box:

  ``ip-api`` style
      ``{"status":"success","query":"1.2.3.4","countryCode":"DE", ...,
      "regionName":"Hesse","city":"Frankfurt","zip":"60311","lat":..,"lon":..,
      "isp":"..","org":"..","as":"AS24940","mobile":false,"proxy":false,
      "hosting":true}``

  ``ipinfo`` style
      ``{"ip":"1.2.3.4","city":"Frankfurt","region":"Hesse","country":"DE",
      "loc":"50.1,8.6","org":"AS24940 Hetzner Online GmbH","postal":"60311"}``

* Providers are tried in order; the first successful parse wins.  The provider
  list can be supplied programmatically or through the ``NOVA_GEO_PROVIDERS``
  environment variable (comma separated ``name=url`` pairs, or bare URLs).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from novaprofile.schema.network import GeoIntelligence

__all__ = [
    "CITY_ACCURACY_METERS",
    "CITY_GEOCENTERS",
    "CITY_TIMEZONE_HINTS",
    "COUNTRY_LOCALES",
    "COUNTRY_TIMEZONES",
    "DATACENTER_ASNS",
    "DATACENTER_ORG_KEYWORDS",
    "MOBILE_CARRIER_COUNTRIES",
    "GeoEstimate",
    "GeoIntelligence",
    "GeoLookupError",
    "GeoProvider",
    "GeoService",
    "ReverseGeoResult",
    "default_providers",
    "is_datacenter_asn",
    "is_datacenter_org",
]


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: ASNs that unambiguously belong to a cloud / hosting provider.
DATACENTER_ASNS: Final[frozenset[str]] = frozenset(
    {
        # Amazon AWS
        "AS16509",
        "AS14618",
        "AS8987",
        "AS38895",
        "AS58588",
        "AS10124",
        "AS136907",
        # Google / Google Cloud
        "AS15169",
        "AS396982",
        "AS208211",
        "AS36459",
        # Microsoft Azure
        "AS8075",
        "AS8068",
        "AS36114",
        "AS13335",
        # Oracle Cloud
        "AS31898",
        "AS31607",
        # DigitalOcean
        "AS14061",
        "AS20940",
        # Hetzner
        "AS24940",
        "AS213230",
        # Linode / Akamai Connected Cloud
        "AS63949",
        # Vultr / Choopa
        "AS20473",
        "AS20404",
        # OVH
        "AS16276",
        # Cloudflare
        "AS19893",
        # Alibaba
        "AS37963",
        "AS45102",
        # Scaleway
        "AS12876",
        # Leaseweb
        "AS60781",
        # Contabo
        "AS51167",
    }
)

#: Substrings that mark an organisation as a hosting / cloud operator.
DATACENTER_ORG_KEYWORDS: Final[tuple[str, ...]] = (
    "amazon",
    "aws",
    "google",
    "googleusercontent",
    "microsoft",
    "azure",
    "digitalocean",
    "digital ocean",
    "hetzner",
    "linode",
    "akamai connected cloud",
    "vultr",
    "choopa",
    "ovh",
    "oracle cloud",
    "alibaba",
    "alicloud",
    "scaleway",
    "contabo",
    "leaseweb",
    "cloudflare",
    "ibm cloud",
    "softlayer",
    "datacenter",
    "data center",
    "hosting",
    "hosting solution",
    "cloud server",
)

#: City-level geolocation is accurate to roughly this radius (metres).
CITY_ACCURACY_METERS: Final[int] = 20_000

#: Fallback country -> IANA timezone mapping (single zone per country).
COUNTRY_TIMEZONES: Final[dict[str, str]] = {
    "AE": "Asia/Dubai",
    "AR": "America/Argentina/Buenos_Aires",
    "AT": "Europe/Vienna",
    "AU": "Australia/Sydney",
    "BD": "Asia/Dhaka",
    "BE": "Europe/Brussels",
    "BR": "America/Sao_Paulo",
    "CA": "America/Toronto",
    "CH": "Europe/Zurich",
    "CL": "America/Santiago",
    "CN": "Asia/Shanghai",
    "CO": "America/Bogota",
    "CZ": "Europe/Prague",
    "DE": "Europe/Berlin",
    "DK": "Europe/Copenhagen",
    "EE": "Europe/Tallinn",
    "EG": "Africa/Cairo",
    "ES": "Europe/Madrid",
    "FI": "Europe/Helsinki",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "GR": "Europe/Athens",
    "HK": "Asia/Hong_Kong",
    "HU": "Europe/Budapest",
    "ID": "Asia/Jakarta",
    "IE": "Europe/Dublin",
    "IL": "Asia/Jerusalem",
    "IN": "Asia/Kolkata",
    "IT": "Europe/Rome",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "LT": "Europe/Vilnius",
    "LU": "Europe/Luxembourg",
    "LV": "Europe/Riga",
    "MX": "America/Mexico_City",
    "MY": "Asia/Kuala_Lumpur",
    "NG": "Africa/Lagos",
    "NL": "Europe/Amsterdam",
    "NO": "Europe/Oslo",
    "NZ": "Pacific/Auckland",
    "PH": "Asia/Manila",
    "PL": "Europe/Warsaw",
    "PT": "Europe/Lisbon",
    "RO": "Europe/Bucharest",
    "RS": "Europe/Belgrade",
    "RU": "Europe/Moscow",
    "SE": "Europe/Stockholm",
    "SG": "Asia/Singapore",
    "SK": "Europe/Bratislava",
    "TH": "Asia/Bangkok",
    "TR": "Europe/Istanbul",
    "TW": "Asia/Taipei",
    "UA": "Europe/Kyiv",
    "US": "America/New_York",
    "VN": "Asia/Ho_Chi_Minh",
    "ZA": "Africa/Johannesburg",
}

#: Fallback country -> (primary locale, currency) mapping.
COUNTRY_LOCALES: Final[dict[str, tuple[str, str]]] = {
    "AT": ("de-AT", "EUR"),
    "AU": ("en-AU", "AUD"),
    "BE": ("nl-BE", "EUR"),
    "BR": ("pt-BR", "BRL"),
    "CA": ("en-CA", "CAD"),
    "CH": ("de-CH", "CHF"),
    "CN": ("zh-CN", "CNY"),
    "DE": ("de-DE", "EUR"),
    "DK": ("da-DK", "DKK"),
    "EE": ("et-EE", "EUR"),
    "ES": ("es-ES", "EUR"),
    "FI": ("fi-FI", "EUR"),
    "FR": ("fr-FR", "EUR"),
    "GB": ("en-GB", "GBP"),
    "GR": ("el-GR", "EUR"),
    "HK": ("zh-HK", "HKD"),
    "HU": ("hu-HU", "HUF"),
    "ID": ("id-ID", "IDR"),
    "IE": ("en-IE", "EUR"),
    "IN": ("en-IN", "INR"),
    "IT": ("it-IT", "EUR"),
    "JP": ("ja-JP", "JPY"),
    "KR": ("ko-KR", "KRW"),
    "LT": ("lt-LT", "EUR"),
    "LU": ("fr-LU", "EUR"),
    "LV": ("lv-LV", "EUR"),
    "MX": ("es-MX", "MXN"),
    "MY": ("ms-MY", "MYR"),
    "NL": ("nl-NL", "EUR"),
    "NO": ("nb-NO", "NOK"),
    "NZ": ("en-NZ", "NZD"),
    "PH": ("en-PH", "PHP"),
    "PL": ("pl-PL", "PLN"),
    "PT": ("pt-PT", "EUR"),
    "RO": ("ro-RO", "RON"),
    "RS": ("sr-RS", "RSD"),
    "RU": ("ru-RU", "RUB"),
    "SE": ("sv-SE", "SEK"),
    "SG": ("en-SG", "SGD"),
    "SK": ("sk-SK", "EUR"),
    "TH": ("th-TH", "THB"),
    "TR": ("tr-TR", "TRY"),
    "TW": ("zh-TW", "TWD"),
    "UA": ("uk-UA", "UAH"),
    "US": ("en-US", "USD"),
    "VN": ("vi-VN", "VND"),
    "ZA": ("en-ZA", "ZAR"),
}

#: Small curated table of city geocentres.  Keys are lower-cased city names.
CITY_GEOCENTERS: Final[dict[str, tuple[float, float]]] = {
    "amsterdam": (52.3676, 4.9041),
    "athens": (37.9838, 23.7275),
    "atlanta": (33.7490, -84.3880),
    "austin": (30.2672, -97.7431),
    "bangkok": (13.7563, 100.5018),
    "barcelona": (41.3874, 2.1686),
    "beijing": (39.9042, 116.4074),
    "berlin": (52.5200, 13.4050),
    "bogota": (4.7110, -74.0721),
    "brussels": (50.8503, 4.3517),
    "buenos aires": (-34.6037, -58.3816),
    "budapest": (47.4979, 19.0402),
    "cape town": (-33.9249, 18.4241),
    "chengdu": (30.5728, 104.0668),
    "chicago": (41.8781, -87.6298),
    "copenhagen": (55.6761, 12.5683),
    "dallas": (32.7767, -96.7970),
    "denver": (39.7392, -104.9903),
    "dubai": (25.2048, 55.2708),
    "dublin": (53.3498, -6.2603),
    "frankfurt": (50.1109, 8.6821),
    "frankfurt am main": (50.1109, 8.6821),
    "helsinki": (60.1699, 24.9384),
    "hong kong": (22.3193, 114.1694),
    "istanbul": (41.0082, 28.9784),
    "jakarta": (-6.2088, 106.8456),
    "johannesburg": (-26.2041, 28.0473),
    "kyiv": (50.4501, 30.5234),
    "lagos": (6.5244, 3.3792),
    "lima": (-12.0464, -77.0428),
    "lisbon": (38.7223, -9.1393),
    "london": (51.5074, -0.1278),
    "los angeles": (34.0522, -118.2437),
    "madrid": (40.4168, -3.7038),
    "melbourne": (-37.8136, 144.9631),
    "mexico city": (19.4326, -99.1332),
    "miami": (25.7617, -80.1918),
    "milan": (45.4642, 9.1900),
    "montreal": (45.5019, -73.5674),
    "mumbai": (19.0760, 72.8777),
    "munich": (48.1351, 11.5820),
    "nairobi": (-1.2921, 36.8219),
    "new york": (40.7128, -74.0060),
    "oslo": (59.9139, 10.7522),
    "paris": (48.8566, 2.3522),
    "prague": (50.0755, 14.4378),
    "rio de janeiro": (-22.9068, -43.1729),
    "rome": (41.9028, 12.4964),
    "san francisco": (37.7749, -122.4194),
    "santiago": (-33.4489, -70.6693),
    "sao paulo": (-23.5505, -46.6333),
    "seattle": (47.6062, -122.3321),
    "seoul": (37.5665, 126.9780),
    "shanghai": (31.2304, 121.4737),
    "singapore": (1.3521, 103.8198),
    "stockholm": (59.3293, 18.0686),
    "sydney": (-33.8688, 151.2093),
    "taipei": (25.0330, 121.5654),
    "tallinn": (59.4370, 24.7536),
    "tel aviv": (32.0853, 34.7818),
    "tokyo": (35.6762, 139.6503),
    "toronto": (43.6532, -79.3832),
    "vancouver": (49.2827, -123.1207),
    "vienna": (48.2082, 16.3738),
    "vilnius": (54.6872, 25.2797),
    "zurich": (47.3769, 8.5417),
}

#: Curated city -> timezone hints for cities whose timezone differs from the
#: country default.
CITY_TIMEZONE_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "amsterdam": ("Europe/Amsterdam",),
    "athens": ("Europe/Athens",),
    "barcelona": ("Europe/Madrid",),
    "buenos aires": ("America/Argentina/Buenos_Aires",),
    "chicago": ("America/Chicago",),
    "copenhagen": ("Europe/Copenhagen",),
    "frankfurt": ("Europe/Berlin",),
    "frankfurt am main": ("Europe/Berlin",),
    "dubai": ("Asia/Dubai",),
    "helsinki": ("Europe/Helsinki",),
    "hong kong": ("Asia/Hong_Kong",),
    "istanbul": ("Europe/Istanbul",),
    "johannesburg": ("Africa/Johannesburg",),
    "lagos": ("Africa/Lagos",),
    "lisbon": ("Europe/Lisbon",),
    "london": ("Europe/London",),
    "los angeles": ("America/Los_Angeles",),
    "mexico city": ("America/Mexico_City",),
    "miami": ("America/New_York",),
    "milan": ("Europe/Rome",),
    "montreal": ("America/Toronto",),
    "mumbai": ("Asia/Kolkata",),
    "munich": ("Europe/Berlin",),
    "nairobi": ("Africa/Nairobi",),
    "new york": ("America/New_York",),
    "oslo": ("Europe/Oslo",),
    "paris": ("Europe/Paris",),
    "rome": ("Europe/Rome",),
    "san francisco": ("America/Los_Angeles",),
    "santiago": ("America/Santiago",),
    "sao paulo": ("America/Sao_Paulo",),
    "seattle": ("America/Los_Angeles",),
    "seoul": ("Asia/Seoul",),
    "shanghai": ("Asia/Shanghai",),
    "singapore": ("Asia/Singapore",),
    "stockholm": ("Europe/Stockholm",),
    "sydney": ("Australia/Sydney",),
    "taipei": ("Asia/Taipei",),
    "tokyo": ("Asia/Tokyo",),
    "toronto": ("America/Toronto",),
    "vancouver": ("America/Vancouver",),
    "vienna": ("Europe/Vienna",),
    "warsaw": ("Europe/Warsaw",),
    "zurich": ("Europe/Zurich",),
}

#: Countries where a substantial share of exit IPs belong to mobile carriers.
MOBILE_CARRIER_COUNTRIES: Final[frozenset[str]] = frozenset(
    {
        "AE",
        "BR",
        "DZ",
        "EG",
        "GH",
        "IN",
        "ID",
        "IR",
        "IQ",
        "JO",
        "KE",
        "KW",
        "LB",
        "MA",
        "MX",
        "MY",
        "NG",
        "OM",
        "PK",
        "PH",
        "QA",
        "SA",
        "SG",
        "TH",
        "TN",
        "TR",
        "UG",
        "VN",
        "YE",
        "ZA",
    }
)


class GeoLookupError(RuntimeError):
    """Raised when no configured provider could resolve the supplied IP."""


# --------------------------------------------------------------------------
# Provider description
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoProvider:
    """A single geo-intelligence HTTP endpoint.

    ``name`` selects the parser strategy; ``url`` may contain the ``{ip}``
    placeholder which is replaced by the address being looked up.
    """

    name: str
    url: str
    timeout: float = 10.0
    headers: dict[str, str] = field(default_factory=dict)

    def url_for(self, ip: str) -> str:
        return self.url.replace("{ip}", ip)


def default_providers() -> list[GeoProvider]:
    """Return the built-in provider chain (ip-api first, ipinfo as fallback)."""
    return [
        GeoProvider(
            name="ip-api",
            url=(
                "http://ip-api.com/json/{ip}"
                "?fields=status,message,query,country,countryCode,region,regionName,"
                "city,zip,lat,lon,timezone,isp,org,as,mobile,proxy,hosting"
            ),
            timeout=10.0,
        ),
        GeoProvider(
            name="ipinfo",
            url="https://ipinfo.io/{ip}/json",
            timeout=10.0,
            headers={"Accept": "application/json"},
        ),
    ]


def _providers_from_env() -> list[GeoProvider]:
    """Parse the ``NOVA_GEO_PROVIDERS`` environment variable.

    Format: comma separated entries of the form ``name=url`` or ``url``.
    A bare URL is assigned the provider name ``custom``.
    """
    raw = os.environ.get("NOVA_GEO_PROVIDERS", "").strip()
    if not raw:
        return []
    providers: list[GeoProvider] = []
    for index, chunk in enumerate(raw.split(",")):
        entry = chunk.strip()
        if not entry:
            continue
        if "=" in entry:
            name, _, url = entry.partition("=")
            name = name.strip() or f"custom{index}"
            url = url.strip()
        else:
            name = f"custom{index}"
            url = entry
        if not url:
            continue
        providers.append(GeoProvider(name=name, url=url, timeout=10.0))
    return providers


# --------------------------------------------------------------------------
# Reverse lookup / geolocation estimation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReverseGeoResult:
    """Result of a coordinate -> city reverse lookup."""

    city: str | None
    country_code: str | None
    country_name: str | None
    latitude: float
    longitude: float
    accuracy_meters: int
    provider: str | None = None


@dataclass(frozen=True)
class GeoEstimate:
    """Estimated coordinates for a country / city pair."""

    latitude: float
    longitude: float
    accuracy_meters: int
    source: str


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


class GeoService:
    """Resolve exit-IP geolocation and derive consistent locale data."""

    def __init__(
        self,
        providers: Sequence[GeoProvider] | None = None,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        reverse_providers: Sequence[GeoProvider] | None = None,
    ) -> None:
        resolved: list[GeoProvider] = []
        if providers is not None:
            resolved.extend(providers)
        else:
            resolved.extend(_providers_from_env())
            if not resolved:
                resolved.extend(default_providers())
        if not resolved:
            raise ValueError("GeoService requires at least one provider")
        self._providers: list[GeoProvider] = resolved
        self._timeout: float = timeout
        self._client: httpx.AsyncClient | None = client
        self._reverse_providers: list[GeoProvider] = list(reverse_providers or [])
        self._owns_client: bool = False

    # -- properties -------------------------------------------------------

    @property
    def providers(self) -> list[GeoProvider]:
        return list(self._providers)

    @property
    def timeout(self) -> float:
        return self._timeout

    # -- public API --------------------------------------------------------

    async def lookup(self, ip: str) -> GeoIntelligence:
        """Resolve ``ip`` into a :class:`GeoIntelligence` snapshot.

        Providers are tried in order; the first provider that returns a
        parseable payload wins.  Raises :class:`GeoLookupError` when every
        provider fails.
        """
        if not ip:
            raise GeoLookupError("empty IP address")

        errors: list[str] = []
        for provider in self._providers:
            url = provider.url_for(ip)
            try:
                payload = await self._fetch_json(url, timeout=provider.timeout or self._timeout)
            except Exception as exc:  # noqa: BLE001 - provider errors are reported
                errors.append(f"{provider.name}: {exc}")
                continue
            try:
                parsed = self.parse_payload(payload, provider_name=provider.name)
            except Exception as exc:  # noqa: BLE001 - malformed payloads are reported
                errors.append(f"{provider.name}: parse failed: {exc}")
                continue
            return self._finalize(parsed, ip=ip)

        detail = ("; " + "; ".join(errors)) if errors else ""
        raise GeoLookupError(f"no geo provider could resolve {ip}{detail}")

    def lookup_sync(self, ip: str) -> GeoIntelligence:
        """Synchronous wrapper around :meth:`lookup` (runs its own event loop)."""
        return asyncio.run(self.lookup(ip))

    async def reverse_lookup(self, lat: float, lng: float) -> ReverseGeoResult:
        """Best-effort coordinate -> city lookup.

        Falls back to the built-in city table when no reverse provider is
        configured or all of them fail.
        """
        for provider in self._reverse_providers:
            url = provider.url_for(f"{lat},{lng}")
            try:
                payload = await self._fetch_json(url, timeout=provider.timeout or self._timeout)
            except Exception:  # noqa: BLE001 - best effort only
                continue
            result = self.parse_reverse_payload(payload)
            if result is not None:
                return result
        return self._offline_reverse(lat, lng)

    def estimate_geolocation(
        self, country_code: str | None, city: str | None = None
    ) -> GeoEstimate:
        """Estimate coordinates for a country / city pair.

        City-level results carry an accuracy radius of
        :data:`CITY_ACCURACY_METERS` (20 km).  Country-level fallbacks use a
        250 km radius centred on the country's reference point.
        """
        if city:
            center = CITY_GEOCENTERS.get(city.strip().lower())
            if center is not None:
                return GeoEstimate(
                    latitude=center[0],
                    longitude=center[1],
                    accuracy_meters=CITY_ACCURACY_METERS,
                    source="city_table",
                )
        cc = (country_code or "").strip().upper()
        if cc:
            country_centroid = _country_centroid(cc)
            if country_centroid is not None:
                return GeoEstimate(
                    latitude=country_centroid[0],
                    longitude=country_centroid[1],
                    accuracy_meters=250_000,
                    source="country_fallback",
                )
        return GeoEstimate(
            latitude=0.0,
            longitude=0.0,
            accuracy_meters=1_000_000,
            source="unknown",
        )

    def timezone_for(self, country_code: str | None, city: str | None = None) -> str | None:
        """Derive an IANA timezone matching the exit node."""
        if city:
            hints = CITY_TIMEZONE_HINTS.get(city.strip().lower())
            if hints:
                return hints[0]
        if country_code:
            return COUNTRY_TIMEZONES.get(country_code.strip().upper())
        return None

    def locale_for(self, country_code: str | None) -> tuple[str | None, str | None]:
        """Derive ``(locale, currency)`` matching the exit node."""
        if not country_code:
            return (None, None)
        return COUNTRY_LOCALES.get(country_code.strip().upper(), (None, None))

    # -- payload parsing ---------------------------------------------------

    def parse_payload(self, payload: dict[str, Any], *, provider_name: str = "") -> dict[str, Any]:
        """Normalise a provider payload into flat keyword arguments.

        Two response shapes are understood:

        * ``ip-api`` style -- ``countryCode`` / ``regionName`` / ``zip`` / ``as``.
        * ``ipinfo`` style -- ``country`` / ``region`` / ``postal`` / ``org``.
        """
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")

        status = str(payload.get("status", "")).lower()
        if status and status not in ("success", "ok"):
            message = payload.get("message") or payload.get("error") or "provider error"
            raise ValueError(str(message))

        country_code = _first_str(payload, "countryCode", "country_code")
        country_name = _first_str(payload, "countryName", "country_name")
        if country_code is None:
            raw_country = payload.get("country")
            if isinstance(raw_country, str) and len(raw_country) == 2:
                country_code = raw_country
            elif isinstance(raw_country, str):
                country_name = country_name or raw_country
        if country_code:
            country_code = country_code.upper()

        # ``region`` is a *name* for some providers (ipinfo) and a *code* for
        # others (ip-api).  ``regionCode`` / ``regionName`` are unambiguous,
        # so they win; a bare ``region`` fills whatever is still missing.
        region_code = _first_str(payload, "regionCode", "region_code")
        region_name = _first_str(payload, "regionName", "region_name")
        loose_region = _first_str(payload, "region", "state")
        if loose_region is not None:
            if region_code is None and region_name is None:
                region_code = loose_region
                region_name = loose_region
            else:
                if region_code is None:
                    region_code = loose_region
                if region_name is None:
                    region_name = loose_region
        city = _first_str(payload, "city", "town")
        postal_code = _first_str(payload, "zip", "postal", "postalCode", "postal_code")
        isp = _first_str(payload, "isp", "isp_name")
        org = _first_str(payload, "org", "organisation", "organization")

        raw_asn = _first_str(payload, "as", "asn", "asNumber")
        asn = _normalize_asn(raw_asn, org)
        asn_number = _asn_to_int(asn)

        is_mobile = bool(payload.get("mobile", payload.get("is_mobile", False)))
        if not is_mobile and country_code:
            is_mobile = country_code in MOBILE_CARRIER_COUNTRIES

        is_dc = bool(payload.get("proxy", False)) or bool(payload.get("is_datacenter", False))
        hosting_flag = payload.get("hosting")
        if hosting_flag is not None:
            is_dc = is_dc or bool(hosting_flag)
        is_dc = is_dc or is_datacenter_asn(asn) or is_datacenter_org(org)

        exit_ip = _first_str(payload, "query", "ip", "ipAddress", "exit_ip")
        lat, lon = _parse_loc(payload)

        result: dict[str, Any] = {
            "country_code": country_code,
            "country_name": country_name,
            "region_code": region_code,
            "region_name": region_name,
            "city": city,
            "postal_code": postal_code,
            "asn": asn_number,
            "isp": isp,
            "org": org,
            "is_mobile_carrier": is_mobile,
            "is_datacenter": is_dc,
        }
        if exit_ip:
            result["exit_ip"] = exit_ip
        if lat is not None and lon is not None:
            result["latitude"] = lat
            result["longitude"] = lon
        timezone = _first_str(payload, "timezone", "timeZone", "tz")
        if timezone:
            result["timezone"] = timezone
        result["provider"] = provider_name
        return result

    def parse_reverse_payload(self, payload: dict[str, Any]) -> ReverseGeoResult | None:
        """Parse a reverse-geocoding payload, or return ``None`` when unusable."""
        if not isinstance(payload, dict):
            return None
        city = _first_str(payload, "city", "town", "locality")
        country_code = _first_str(payload, "countryCode", "country_code")
        country_name = _first_str(payload, "countryName", "country_name")
        lat = payload.get("lat", payload.get("latitude"))
        lon = payload.get("lon", payload.get("longitude", payload.get("lng")))
        try:
            latitude = float(lat)
            longitude = float(lon)
        except (TypeError, ValueError):
            return None
        raw_accuracy = payload.get("accuracy", CITY_ACCURACY_METERS)
        try:
            accuracy = int(raw_accuracy)
        except (TypeError, ValueError):
            accuracy = CITY_ACCURACY_METERS
        return ReverseGeoResult(
            city=city,
            country_code=country_code.upper() if country_code else None,
            country_name=country_name,
            latitude=latitude,
            longitude=longitude,
            accuracy_meters=accuracy,
        )

    # -- HTTP --------------------------------------------------------------

    async def _fetch_json(self, url: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Perform a GET request and decode the JSON body.

        This is the single seam through which every HTTP call travels; tests
        monkeypatch this method instead of mocking the transport layer.
        """
        effective_timeout = timeout if timeout is not None else self._timeout
        client = self._client
        if client is not None:
            response = await client.get(url, timeout=effective_timeout)
            response.raise_for_status()
            return _as_json_object(response)

        async with httpx.AsyncClient(timeout=effective_timeout) as owned:
            self._client = owned
            self._owns_client = True
            try:
                response = await owned.get(url, timeout=effective_timeout)
                response.raise_for_status()
                return _as_json_object(response)
            finally:
                self._client = None
                self._owns_client = False

    async def aclose(self) -> None:
        """Close an injected HTTP client, if this service owns one."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    # -- internals ---------------------------------------------------------

    def _finalize(self, parsed: dict[str, Any], *, ip: str) -> GeoIntelligence:
        """Build the schema model from parsed provider data.

        The canonical ``GeoIntelligence`` model uses empty strings for absent
        text fields and ``0`` for absent numbers, so the normalised values are
        coerced accordingly.
        """
        exit_ip = parsed.get("exit_ip") or ip
        exit_v4, exit_v6 = _split_ip_versions(exit_ip)
        values: dict[str, Any] = {
            "exit_ip_v4": exit_v4 or "",
            "exit_ip_v6": exit_v6 or "",
            "country_code": parsed.get("country_code") or "",
            "country_name": parsed.get("country_name") or "",
            "region_code": parsed.get("region_code") or "",
            "region_name": parsed.get("region_name") or "",
            "city": parsed.get("city") or "",
            "postal_code": parsed.get("postal_code") or "",
            "asn": int(parsed.get("asn") or 0),
            "isp": parsed.get("isp") or "",
            "org": parsed.get("org") or "",
            "is_mobile_carrier": bool(parsed.get("is_mobile_carrier", False)),
            "is_datacenter": bool(parsed.get("is_datacenter", False)),
            "latency_ms": int(parsed.get("latency_ms") or 0),
            "checked_at": parsed.get("checked_at") or datetime.now(UTC),
        }
        return _build_geo_model(values)

    def _offline_reverse(self, lat: float, lng: float) -> ReverseGeoResult:
        """Nearest known city from the built-in table (great-circle distance)."""
        best_name: str | None = None
        best_distance = float("inf")
        for name, (city_lat, city_lon) in CITY_GEOCENTERS.items():
            distance = _haversine(lat, lng, city_lat, city_lon)
            if distance < best_distance:
                best_distance = distance
                best_name = name
        return ReverseGeoResult(
            city=best_name.title() if best_name else None,
            country_code=None,
            country_name=None,
            latitude=lat,
            longitude=lng,
            accuracy_meters=int(min(best_distance, 1_000_000)),
            provider="offline_table",
        )


# --------------------------------------------------------------------------
# Module level helpers
# --------------------------------------------------------------------------


def is_datacenter_asn(asn: str | None) -> bool:
    """Return ``True`` when ``asn`` belongs to a known cloud/hosting provider."""
    if not asn:
        return False
    normalized = _normalize_asn(asn, None)
    return normalized is not None and normalized in DATACENTER_ASNS


def is_datacenter_org(org: str | None) -> bool:
    """Return ``True`` when the organisation string looks like a datacentre."""
    if not org:
        return False
    lowered = org.lower()
    return any(keyword in lowered for keyword in DATACENTER_ORG_KEYWORDS)


def _country_centroid(country_code: str) -> tuple[float, float] | None:
    """Reference point for a country (largest known city, else None)."""
    preferred = {
        "AU": "sydney",
        "BR": "sao paulo",
        "CA": "toronto",
        "CH": "zurich",
        "CN": "shanghai",
        "DE": "berlin",
        "ES": "madrid",
        "FR": "paris",
        "GB": "london",
        "IN": "mumbai",
        "IT": "rome",
        "JP": "tokyo",
        "KR": "seoul",
        "MX": "mexico city",
        "NL": "amsterdam",
        "NZ": "sydney",
        "RU": "moscow",
        "SE": "stockholm",
        "TR": "istanbul",
        "TW": "taipei",
        "US": "new york",
        "VN": "ho chi minh",
        "ZA": "johannesburg",
    }
    city = preferred.get(country_code)
    if city is None:
        return None
    return CITY_GEOCENTERS.get(city)


def _normalize_asn(raw_asn: str | None, org: str | None) -> str | None:
    """Extract a bare ``AS####`` token from provider strings.

    ``ipinfo`` returns values like ``"AS24940 Hetzner Online GmbH"``; the ASN
    itself is the leading token.
    """
    for candidate in (raw_asn, org):
        if not candidate:
            continue
        token = candidate.strip().split(" ")[0].strip()
        if token.upper().startswith("AS") and token[2:].isdigit():
            return token.upper()
        if token.isdigit():
            return f"AS{token}"
    return None


def _asn_to_int(asn: str | None) -> int:
    """Convert an ``AS1234`` token into its numeric form (``0`` when absent)."""
    if not asn:
        return 0
    digits = asn[2:] if asn.upper().startswith("AS") else asn
    try:
        return int(digits)
    except ValueError:
        return 0


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_loc(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    loc = payload.get("loc")
    if isinstance(loc, str) and "," in loc:
        left, _, right = loc.partition(",")
        try:
            return float(left), float(right)
        except ValueError:
            return None, None
    lat = payload.get("lat", payload.get("latitude"))
    lon = payload.get("lon", payload.get("longitude", payload.get("lng")))
    try:
        if lat is None or lon is None:
            return None, None
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def _split_ip_versions(ip: str) -> tuple[str | None, str | None]:
    try:
        parsed = ipaddress.ip_address(ip.strip())
    except ValueError:
        return (None, None)
    if parsed.version == 4:
        return (str(parsed), None)
    return (None, str(parsed))


def _as_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"provider returned non-JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("provider returned a non-object JSON body")
    return data


def _build_geo_model(values: dict[str, Any]) -> GeoIntelligence:
    """Instantiate the schema model, tolerating small field-set differences."""
    allowed = set(GeoIntelligence.model_fields.keys())
    kwargs = {key: value for key, value in values.items() if key in allowed}
    try:
        return GeoIntelligence(**kwargs)
    except Exception:  # noqa: BLE001 - fall back to a minimal construction
        minimal_allowed = {"exit_ip_v4", "exit_ip_v6", "country_code", "city"}
        minimal = {key: value for key, value in values.items() if key in minimal_allowed}
        return GeoIntelligence(**minimal)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(a)))
