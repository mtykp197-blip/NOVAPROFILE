"""Geographic anchor catalogue.

A profile's locale, timezone, currency and language list are the strongest
"same person" signal a site can read: two profiles that agree on all four
look like the same user to any aggregation.

The old catalogue had one entry per country, which capped the whole fleet at
28 distinguishable people.  This module enumerates the *real* timezones of
each country, so a country with many zones yields many distinguishable
anchors while staying geographically coherent -- the timezone a profile
reports still belongs to the country its IP address belongs to, which is what
keeps the profile from looking like a spoof.

Each entry is ``(locale, timezone, country, currency, hour_cycle,
secondary_locales)``.  The secondary locales are the languages a user of
that country commonly has enabled after the primary one, which is what makes
``navigator.languages`` vary between two users of the same country.
"""

from __future__ import annotations

#: ``(locale, timezone, country, currency, hour_cycle, secondary_locales)``
GeoEntry = tuple[str, str, str, str, str, tuple[str, ...]]

#: The catalogue.  Ordered by continent so the generated fleet is spread the
#: same way a real user base is.
GEO_CATALOGUE: tuple[GeoEntry, ...] = (
    # -- United States: 30+ real zones ---------------------------------
    ("en-US", "America/New_York", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Detroit", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Chicago", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Denver", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Phoenix", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Los_Angeles", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Anchorage", "US", "USD", "h12", ("en", "es")),
    ("en-US", "Pacific/Honolulu", "US", "USD", "h12", ("en", "es")),
    ("es-US", "America/New_York", "US", "USD", "h12", ("en", "es")),
    ("es-US", "America/Chicago", "US", "USD", "h12", ("en", "es")),
    ("es-US", "America/Los_Angeles", "US", "USD", "h12", ("en", "es")),
    ("en-US", "America/Indiana/Indianapolis", "US", "USD", "h12", ("en",)),
    ("en-US", "America/Kentucky/Louisville", "US", "USD", "h12", ("en",)),
    ("en-US", "America/North_Dakota/Center", "US", "USD", "h12", ("en",)),
    # -- Canada ----------------------------------------------------------
    ("en-CA", "America/Toronto", "CA", "CAD", "h12", ("en", "fr")),
    ("en-CA", "America/Montreal", "CA", "CAD", "h12", ("en", "fr")),
    ("en-CA", "America/Winnipeg", "CA", "CAD", "h12", ("en",)),
    ("en-CA", "America/Edmonton", "CA", "CAD", "h12", ("en",)),
    ("en-CA", "America/Vancouver", "CA", "CAD", "h12", ("en",)),
    ("fr-CA", "America/Montreal", "CA", "CAD", "h12", ("en", "fr")),
    ("fr-CA", "America/Toronto", "CA", "CAD", "h12", ("en", "fr")),
    # -- Latin America ----------------------------------------------------
    ("pt-BR", "America/Sao_Paulo", "BR", "BRL", "h23", ("pt", "en")),
    ("pt-BR", "America/Manaus", "BR", "BRL", "h23", ("pt",)),
    ("pt-BR", "America/Fortaleza", "BR", "BRL", "h23", ("pt", "en")),
    ("es-MX", "America/Mexico_City", "MX", "MXN", "h12", ("es", "en")),
    ("es-MX", "America/Monterrey", "MX", "MXN", "h12", ("es", "en")),
    ("es-MX", "America/Chihuahua", "MX", "MXN", "h12", ("es",)),
    ("es-AR", "America/Argentina/Buenos_Aires", "AR", "ARS", "h23", ("es", "en")),
    ("es-CL", "America/Santiago", "CL", "CLP", "h23", ("es", "en")),
    ("es-PE", "America/Lima", "PE", "PEN", "h23", ("es", "en")),
    # Puerto Rico is a US territory, but it is not the United States: a
    ("es-PR", "America/Puerto_Rico", "PR", "USD", "h12", ("es", "en")),
    # -- Europe -----------------------------------------------------------
    ("en-GB", "Europe/London", "GB", "GBP", "h23", ("en",)),
    ("de-DE", "Europe/Berlin", "DE", "EUR", "h23", ("de", "en")),
    ("de-CH", "Europe/Zurich", "CH", "CHF", "h23", ("de", "fr", "it")),
    ("fr-FR", "Europe/Paris", "FR", "EUR", "h23", ("fr", "en")),
    ("fr-BE", "Europe/Brussels", "BE", "EUR", "h23", ("fr", "nl")),
    ("fr-CH", "Europe/Zurich", "CH", "CHF", "h23", ("fr", "de", "it")),
    ("es-ES", "Europe/Madrid", "ES", "EUR", "h23", ("es", "en")),
    ("it-IT", "Europe/Rome", "IT", "EUR", "h23", ("it", "en")),
    ("nl-NL", "Europe/Amsterdam", "NL", "EUR", "h23", ("nl", "en")),
    ("nl-BE", "Europe/Brussels", "BE", "EUR", "h23", ("nl", "fr")),
    ("pt-PT", "Europe/Lisbon", "PT", "EUR", "h23", ("pt", "en")),
    ("pl-PL", "Europe/Warsaw", "PL", "PLN", "h23", ("pl", "en")),
    ("cs-CZ", "Europe/Prague", "CZ", "CZK", "h23", ("cs", "en")),
    ("sk-SK", "Europe/Bratislava", "SK", "EUR", "h23", ("sk", "en")),
    ("hu-HU", "Europe/Budapest", "HU", "HUF", "h23", ("hu", "en")),
    ("ro-RO", "Europe/Bucharest", "RO", "RON", "h23", ("ro", "en")),
    ("bg-BG", "Europe/Sofia", "BG", "BGN", "h23", ("bg", "en")),
    ("el-GR", "Europe/Athens", "GR", "EUR", "h23", ("el", "en")),
    ("tr-TR", "Europe/Istanbul", "TR", "TRY", "h23", ("tr", "en")),
    ("uk-UA", "Europe/Kyiv", "UA", "UAH", "h23", ("uk", "ru", "en")),
    ("da-DK", "Europe/Copenhagen", "DK", "DKK", "h23", ("da", "en")),
    ("sv-SE", "Europe/Stockholm", "SE", "SEK", "h23", ("sv", "en")),
    ("nb-NO", "Europe/Oslo", "NO", "NOK", "h23", ("nb", "en")),
    ("fi-FI", "Europe/Helsinki", "FI", "EUR", "h23", ("fi", "en")),
    ("is-IS", "Atlantic/Reykjavik", "IS", "ISK", "h23", ("is", "en")),
    # -- Middle East & Africa ---------------------------------------------
    ("ar-EG", "Africa/Cairo", "EG", "EGP", "h12", ("ar", "en")),
    ("ar-SA", "Asia/Riyadh", "SA", "SAR", "h12", ("ar", "en")),
    ("ar-AE", "Asia/Dubai", "AE", "AED", "h12", ("ar", "en")),
    ("ar-IL", "Asia/Jerusalem", "IL", "ILS", "h23", ("he", "en")),
    ("he-IL", "Asia/Jerusalem", "IL", "ILS", "h23", ("he", "en", "ar")),
    ("fa-IR", "Asia/Tehran", "IR", "IRR", "h23", ("fa", "en")),
    ("tr-TR", "Europe/Istanbul", "TR", "TRY", "h23", ("tr", "en", "ar")),
    ("en-ZA", "Africa/Johannesburg", "ZA", "ZAR", "h23", ("en",)),
    ("sw-KE", "Africa/Nairobi", "KE", "KES", "h23", ("sw", "en")),
    ("am-ET", "Africa/Addis_Ababa", "ET", "ETB", "h23", ("am", "en")),
    ("fr-DZ", "Africa/Algiers", "DZ", "DZD", "h23", ("fr", "ar")),
    ("fr-MA", "Africa/Casablanca", "MA", "MAD", "h23", ("fr", "ar")),
    ("fr-SN", "Africa/Dakar", "SN", "XOF", "h23", ("fr", "en")),
    ("fr-CI", "Africa/Abidjan", "CI", "XOF", "h23", ("fr", "en")),
    # -- Asia --------------------------------------------------------------
    ("ja-JP", "Asia/Tokyo", "JP", "JPY", "h23", ("ja", "en")),
    ("ko-KR", "Asia/Seoul", "KR", "KRW", "h12", ("ko", "en")),
    ("zh-CN", "Asia/Shanghai", "CN", "CNY", "h23", ("zh", "en")),
    ("zh-CN", "Asia/Chongqing", "CN", "CNY", "h23", ("zh", "en")),
    ("zh-CN", "Asia/Urumqi", "CN", "CNY", "h23", ("zh", "en")),
    ("zh-TW", "Asia/Taipei", "TW", "TWD", "h23", ("zh", "en")),
    ("ko-KP", "Asia/Pyongyang", "KP", "KPW", "h23", ("ko", "en")),
    ("hi-IN", "Asia/Kolkata", "IN", "INR", "h23", ("hi", "en")),
    ("hi-IN", "Asia/Kolkata", "IN", "INR", "h23", ("hi", "en", "ta")),
    ("ta-IN", "Asia/Kolkata", "IN", "INR", "h23", ("ta", "hi", "en")),
    ("bn-IN", "Asia/Kolkata", "IN", "INR", "h23", ("bn", "hi", "en")),
    ("te-IN", "Asia/Kolkata", "IN", "INR", "h23", ("te", "hi", "en")),
    ("mr-IN", "Asia/Kolkata", "IN", "INR", "h23", ("mr", "hi", "en")),
    ("th-TH", "Asia/Bangkok", "TH", "THB", "h23", ("th", "en")),
    # Vietnam observes a single clock -- Asia/Ho_Chi_Minh -- for the whole
    # country, so the diversity here is in the *languages* the device
    # carries rather than in the timezone.  A Vietnamese phone with a
    # Chinese, Japanese or Korean secondary locale is ordinary: the
    # region trades heavily and Android ships those locales.
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "fr")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "zh")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "ja")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "ko")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "ru")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "de")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "es")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "th")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "km")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "my")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "id")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "hi")),
    ("vi-VN", "Asia/Ho_Chi_Minh", "VN", "VND", "h23", ("vi", "en", "ar")),
    ("id-ID", "Asia/Jakarta", "ID", "IDR", "h23", ("id", "en")),
    ("id-ID", "Asia/Makassar", "ID", "IDR", "h23", ("id", "en")),
    ("ms-MY", "Asia/Kuala_Lumpur", "MY", "MYR", "h23", ("ms", "en")),
    ("ms-MY", "Asia/Kuching", "MY", "MYR", "h23", ("ms", "en")),
    ("fil-PH", "Asia/Manila", "PH", "PHP", "h23", ("fil", "en")),
    ("km-KH", "Asia/Phnom_Penh", "KH", "KHR", "h23", ("km", "en")),
    ("lo-LA", "Asia/Vientiane", "LA", "LAK", "h23", ("lo", "en")),
    ("bn-BD", "Asia/Dhaka", "BD", "BDT", "h23", ("bn", "en")),
    ("ur-PK", "Asia/Karachi", "PK", "PKR", "h23", ("ur", "en")),
    ("ur-PK", "Asia/Karachi", "PK", "PKR", "h23", ("ur", "en", "fa")),
    ("ne-NP", "Asia/Kathmandu", "NP", "NPR", "h23", ("ne", "en")),
    ("si-LK", "Asia/Colombo", "LK", "LKR", "h23", ("si", "en")),
    ("my-MM", "Asia/Yangon", "MM", "MMK", "h23", ("my", "en")),
)

#: Every distinct timezone in the catalogue, used to sanity check that no
#: entry claims a zone outside its country.
ALL_TIMEZONES: frozenset[str] = frozenset(entry[1] for entry in GEO_CATALOGUE)

#: Every distinct country in the catalogue.
ALL_COUNTRIES: tuple[str, ...] = tuple(sorted({entry[2] for entry in GEO_CATALOGUE}))


def countries_with_multiple_zones() -> dict[str, int]:
    """How many distinct timezones each country contributes.

    Used by the tests to prove the catalogue really does separate profiles
    within a country, and not only across countries.
    """
    counts: dict[str, set[str]] = {}
    for _locale, timezone, country, _currency, _cycle, _secondaries in GEO_CATALOGUE:
        counts.setdefault(country, set()).add(timezone)
    return {country: len(zones) for country, zones in counts.items()}


__all__ = [
    "ALL_COUNTRIES",
    "ALL_TIMEZONES",
    "GEO_CATALOGUE",
    "GeoEntry",
    "countries_with_multiple_zones",
]
