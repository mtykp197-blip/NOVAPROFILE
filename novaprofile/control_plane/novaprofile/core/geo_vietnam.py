"""The places a Vietnamese profile can be.

Every profile in this fleet is in Vietnam: the clock is
``Asia/Ho_Chi_Minh``, the language is ``vi-VN`` and the currency is the
dong.  That is what a Vietnamese operator needs, and it is what a proxy
sitting in the country can actually back up.

The difficulty is the other half of the requirement: the profiles must
not look like one person.  A hundred million profiles that all report
``Asia/Ho_Chi_Minh`` and ``vi-VN`` are one person to any site that reads
the clock, whatever their canvas and their WebGL say -- and a site that
sees a hundred million profiles in one timezone with one locale knows
immediately that it is looking at a farm.

So the profiles are spread over the country.  Vietnam has sixty-three
provinces and cities, and the coordinates below are the real centres of
the ones a handset is likely to be in.  Two profiles that land in
different provinces are different people; two that land in the same one
are separated by the jitter the generator draws inside the province.

The list is deliberately *provincial* rather than municipal: a province
is the granularity at which the currency, the language and the network
really are the same, and it is the granularity at which a real operator's
exit nodes sit.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "VIETNAM_TIMEZONE",
    "VIETNAM_LOCALE",
    "VIETNAM_CURRENCY",
    "VIETNAM_COORDINATES",
    "VIETNAM_PROVINCES",
    "coordinates_for_province",
    "province_for_index",
]

#: Vietnam keeps a single clock for the whole country.
VIETNAM_TIMEZONE: Final[str] = "Asia/Ho_Chi_Minh"

#: The locale a Vietnamese handset reports.
VIETNAM_LOCALE: Final[str] = "vi-VN"

#: The currency a Vietnamese handset reports.
VIETNAM_CURRENCY: Final[str] = "VND"

#: The real centres of the provinces and cities, at about four decimal
#: places -- roughly eleven metres, which is what a GPS chip reports.
#:
#: The order is fixed and is the order the fleet walks: a fleet spreads
#: over the country in this order, so a fleet of ten thousand profiles
#: covers the first ten thousand provinces-and-cities evenly rather than
#: hammering Ho Chi Minh City.
VIETNAM_COORDINATES: Final[tuple[tuple[str, float, float], ...]] = (
    # -- the five municipalities directly under the government ----------
    ("Ha Noi", 21.0278, 105.8342),
    ("Ha Chi Minh", 10.8231, 106.6297),
    ("Da Nang", 16.0544, 108.2022),
    ("Hai Phong", 20.8449, 106.6881),
    ("Can Tho", 10.0452, 105.7469),
    # -- the north-east ---------------------------------------------------
    ("Cao Bang", 22.6731, 105.8103),
    ("Lang Son", 21.8512, 106.6904),
    ("Lao Cai", 22.4856, 103.9707),
    ("Bac Giang", 21.2907, 106.1910),
    ("Bac Ninh", 21.1861, 106.0709),
    ("Ha Nam", 20.5204, 105.9714),
    ("Hung Yen", 20.7584, 106.1467),
    ("Nam Dinh", 20.3527, 106.0721),
    ("Ninh Binh", 20.2506, 105.9745),
    ("Phu Tho", 21.3992, 105.2298),
    ("Quang Ninh", 21.2764, 107.2030),
    ("Thai Binh", 20.4464, 106.3475),
    ("Thanh Hoa", 19.8067, 105.7771),
    ("Tuyen Quang", 22.8243, 105.2473),
    ("Vinh Phuc", 21.3072, 105.7177),
    ("Yen Bai", 21.7170, 104.8986),
    # -- the north-west ---------------------------------------------------
    ("Dien Bien", 21.3856, 103.0170),
    ("Lai Chau", 22.3864, 102.6950),
    ("Son La", 21.3279, 103.9141),
    ("Hoa Binh", 20.8295, 105.6872),
    # -- the central coast ------------------------------------------------
    ("Nghe An", 19.2342, 104.9200),
    ("Ha Tinh", 18.3342, 105.6888),
    ("Quang Binh", 17.4756, 106.7234),
    ("Quang Tri", 16.7500, 107.0000),
    ("Thua Thien Hue", 16.4637, 107.5844),
    ("Quang Nam", 15.5528, 108.2371),
    ("Quang Ngai", 13.7565, 108.7570),
    ("Binh Dinh", 14.1665, 108.9020),
    ("Phu Yen", 13.0882, 109.0932),
    ("Khanh Hoa", 12.2394, 109.1961),
    ("Ninh Thuận", 11.3406, 108.9056),
    # -- the central highlands --------------------------------------------
    ("Gia Lai", 13.9833, 108.0000),
    ("Kon Tum", 14.3500, 108.0000),
    ("Dak Lak", 12.7100, 108.2378),
    ("Dak Nong", 12.0000, 107.7000),
    ("Lam Dong", 11.9404, 108.4581),
    # -- the south-east ---------------------------------------------------
    ("Dong Nai", 11.0700, 107.1660),
    ("Binh Thuan", 10.9340, 108.3867),
    ("Binh Duong", 11.3254, 106.4770),
    ("Binh Phuoc", 11.7512, 106.7234),
    ("Dong Thap", 10.4937, 105.6882),
    ("Tay Ninh", 11.3110, 106.0980),
    ("Long An", 10.6956, 106.2431),
    ("Tien Giang", 10.4494, 106.3421),
    ("Vinh Long", 10.2537, 105.9722),
    ("Ben Tre", 10.2415, 106.3755),
    ("Tra Vinh", 9.9347, 106.3450),
    ("Soc Trang", 9.6030, 105.9740),
    ("Bac Lieu", 9.2940, 105.7244),
    ("Ca Mau", 8.9880, 105.1500),
    # -- the south-west ---------------------------------------------------
    ("An Giang", 10.5216, 104.9138),
    ("Kien Giang", 10.0125, 105.0808),
    ("Hau Giang", 9.7845, 105.4701),
)

#: The distinct provinces, in the order the fleet walks them.
VIETNAM_PROVINCES: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(name for name, _lat, _lng in VIETNAM_COORDINATES)
)


def coordinates_for_province(index: int) -> tuple[str, float, float]:
    """The province at ``index``, wrapping around the list.

    The index is taken modulo the length of the list, so the fleet can
    walk the country as often as it needs to.
    """

    if not VIETNAM_COORDINATES:
        raise ValueError("the Vietnamese coordinate table is empty")
    return VIETNAM_COORDINATES[index % len(VIETNAM_COORDINATES)]


def province_for_index(index: int) -> tuple[str, float, float]:
    """Alias of :func:`coordinates_for_province`."""

    return coordinates_for_province(index)


#: The locales a Vietnamese handset reports.
#:
#: The country has one clock and one currency, so the *only* thing that
#: varies between two Vietnamese handsets is the set of languages
#: besides the primary one that the owner has switched on.  Android
#: ships those locales, and a Vietnamese owner who has been abroad, or
#: who works with a partner in one of the neighbouring countries, has
#: switched one on.
#:
#: These are the sets that the fleet draws from.  They are what
#: makes two handsets in the same province different handsets rather
#: than the same handset with a different serial number: the languages
#: are read straight off ``navigator.languages``.
#:
#: The list is read three ways, because ``navigator.languages`` is an
#: ordered list and the order is part of what it says.
#:
#: * The *set* of the languages.  The owner has switched on the
#:   languages of the neighbours -- Chinese, Japanese, Korean -- of the
#:   languages of the trade -- French, Russian, German -- and of the
#:   languages of the diaspora -- Thai, Khmer, Burmese, Indonesian.
#:
#: * The *head* of the list.  ``vi`` is the first entry for nearly
#:   every handset, because that is the language the system was sold
#:   in.  But a handset bought in the diaspora, or one whose owner
#:   works for a foreign company, reports another language first --
#:   and a list that always begins with ``vi`` is a list that gives
#:   the fleet away.
#:
#: * The *tail*.  The languages of the minorities of the country --
#:   the Cham, the Hmong, the Tay, the Khmer -- are shipped by
#:   Android and are switched on in the provinces that speak them,
#:   which is one more reading that separates two handsets in the same
#:   town.
VIETNAM_LOCALES: Final[tuple[tuple[str, ...], ...]] = (
    ("vi", "en"),
    ("vi", "en", "fr"),
    ("vi", "en", "zh"),
    ("vi", "en", "ja"),
    ("vi", "en", "ko"),
    ("vi", "en", "ru"),
    ("vi", "en", "de"),
    ("vi", "en", "es"),
    ("vi", "en", "th"),
    ("vi", "en", "km"),
    ("vi", "en", "my"),
    ("vi", "en", "id"),
    ("vi", "en", "hi"),
    ("vi", "en", "ar"),
    # -- the list with a different head --
    ("en", "vi"),
    ("en", "vi", "fr"),
    ("en", "vi", "zh"),
    ("fr", "vi", "en"),
    ("zh", "vi", "en"),
    ("ko", "vi", "en"),
    ("ja", "vi", "en"),
    # -- the languages of the minorities --
    ("vi", "en", "km", "khm"),
    ("vi", "en", "hmn"),
    ("vi", "en", "thr"),
    ("vi", "en", "kmr"),
    ("vi", "km", "en"),
    ("vi", "hmn", "en"),
    ("vi", "en", "kh", "km"),
    # -- the shorter lists, for a handset set up in one language only --
    ("vi",),
    ("vi", "en", "fr", "de"),
    ("vi", "en", "zh", "ja"),
    ("vi", "en", "ko", "ja"),
    ("vi", "en", "ru", "uk"),
    ("vi", "en", "th", "lo"),
    ("vi", "en", "id", "ms"),
    ("vi", "en", "tl", "fil"),
    ("vi", "en", "hi", "bn"),
    ("vi", "en", "ar", "fa"),
    ("vi", "en", "tr", "az"),
    ("vi", "en", "es", "pt"),
    # -- the regional sets that Android ships together --
    ("vi", "en", "id", "ms", "tl"),
    ("vi", "en", "zh", "yue"),
    ("vi", "en", "ko", "ja"),
    ("vi", "en", "ru", "uk", "be"),
    ("vi", "en", "km", "khm", "mfe"),
    ("vi", "en", "th", "lo", "my"),
    ("vi", "en", "fr", "de", "it"),
    ("vi", "en", "es", "pt", "gl"),
    ("vi", "en", "ar", "fa", "ur"),
    ("vi", "en", "hi", "bn", "gu"),
    ("vi", "en", "sr", "hr", "bs"),
    ("vi", "en", "sq", "mk", "bg"),
    ("vi", "en", "et", "lv", "lt"),
    ("vi", "en", "is", "fo", "mt"),
    ("vi", "en", "sw", "am", "zu"),
    ("vi", "en", "af", "zu", "xh"),
)
