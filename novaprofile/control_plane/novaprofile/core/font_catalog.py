"""Font catalogue per script *and* per device tier.

``document.fonts`` (and the width of a rendered glyph) tells a site which
scripts the user's device can draw.  A German user whose device reports only
CJK fonts, or a Japanese user whose device reports no CJK font at all, is
either a bot or a very unusual person.

But the script alone is not enough, and it is not what a real device does.
A real device's font set is the result of two independent decisions:

* which *scripts* the user can read, which follows the locale, and
* how much room the *device* has for fonts, which follows its tier.

A 2 GB budget phone running Android 13 carries a trimmed ``fonts.xml`` —
the optional script families are absent, and the system falls back to the
generic sans.  A flagship with 12 GB carries the full set.  Two profiles
with the same locale but different tiers therefore report *different*
font lists, and that difference is exactly what a fingerprinting script
compares when it asks "is this the same device as the one that registered
an account yesterday?".

The base set in :mod:`novaprofile.core.generator` is the Latin part of the
*full* set.  This module adds the script-specific families that a device in
the given locale would actually carry, and trims the set to what the
device's tier can hold.
"""

from __future__ import annotations

#: Fonts that a device using the given script normally has installed.
#: The key is the primary language subtag (``ja``, ``ko``, ``zh``, ...).
SCRIPT_FONTS: dict[str, tuple[str, ...]] = {
    "ja": ("Noto Sans CJK JP", "Noto Serif CJK JP"),
    "ko": ("Noto Sans CJK KR", "Noto Serif CJK KR"),
    "zh": ("Noto Sans CJK JP", "Noto Serif CJK JP"),
    "he": ("Noto Sans Hebrew", "Noto Serif Hebrew"),
    "ar": ("Noto Sans Arabic", "Noto Naskh Arabic"),
    "fa": ("Noto Sans Arabic", "Noto Naskh Arabic"),
    "hi": ("Noto Sans Devanagari",),
    "bn": ("Noto Sans Bengali",),
    "ta": ("Noto Sans Tamil",),
    "te": ("Noto Sans Telugu",),
    "mr": ("Noto Sans Devanagari",),
    "th": ("Noto Sans Thai", "Noto Serif Thai"),
    "lo": ("Noto Sans Lao",),
    "km": ("Noto Sans Khmer",),
    "my": ("Noto Sans Myanmar",),
    "ne": ("Noto Sans Devanagari",),
    "si": ("Noto Sans Sinhala",),
    "ka": ("Noto Sans Georgian",),
    "hy": ("Noto Sans Armenian",),
    "am": ("Noto Ethiopic",),
    "ur": ("Noto Nastaliq Urdu",),
    "kk": ("Noto Sans Kazakh",),
    "uz": ("Noto Sans",),
    "mn": ("Noto Sans Mongolian",),
    "el": ("Noto Sans Greek",),
    "ru": ("Noto Sans",),
    "uk": ("Noto Sans",),
    "bg": ("Noto Sans",),
    "sr": ("Noto Sans",),
    "tr": ("Noto Sans",),
}

#: Fonts that a device of a given script *never* carries.  A Japanese
#: device without any Latin font is impossible, and so is an Arabic device
#: that claims CJK fonts it has no use for.
SCRIPT_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "ja": (
        "Noto Sans Devanagari",
        "Noto Sans Thai",
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
        "Noto Sans Bengali",
        "Noto Sans Tamil",
    ),
    "ko": (
        "Noto Sans Devanagari",
        "Noto Sans Thai",
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
        "Noto Sans Bengali",
        "Noto Sans Tamil",
    ),
    "zh": (
        "Noto Sans Devanagari",
        "Noto Sans Thai",
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
        "Noto Sans Bengali",
        "Noto Sans Tamil",
    ),
    "ar": (
        "Noto Sans CJK JP",
        "Noto Serif CJK JP",
        "Noto Sans Devanagari",
        "Noto Sans Thai",
        "Noto Sans Bengali",
        "Noto Sans Tamil",
        "Noto Sans Telugu",
    ),
    "he": (
        "Noto Sans CJK JP",
        "Noto Serif CJK JP",
        "Noto Sans Devanagari",
        "Noto Sans Thai",
        "Noto Sans Bengali",
        "Noto Sans Tamil",
    ),
    "hi": (
        "Noto Sans CJK JP",
        "Noto Serif CJK JP",
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
    ),
    "th": (
        "Noto Sans CJK JP",
        "Noto Serif CJK JP",
        "Noto Sans Devanagari",
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
    ),
}

#: The optional families that a *low* tier device leaves out.  These are
#: the ones a manufacturer drops first when the system partition is tight:
#: the script families that a user of this locale does not read, the
#: decorative families nobody asked for, and the ones that cost real space.
#:
#: ``Roboto`` and ``Noto Color Emoji`` are deliberately absent — no Android
#: device ships without them, so they are never dropped.
LOW_TIER_DROPPED: frozenset[str] = frozenset(
    {
        # Decorative families that only the full set carries.
        "Coming Soon",
        "Carrois Gothic SC",
        "Dancing Script",
        "Eczar",
        "Ubuntu Medium",
        # The script families of the other scripts.
        "Noto Sans CJK JP",
        "Noto Serif CJK JP",
        "Noto Sans Devanagari",
        "Noto Sans Thai",
        "Noto Sans Hebrew",
        "Noto Sans Arabic",
        "Noto Naskh Arabic",
        "Noto Sans Bengali",
        "Noto Sans Tamil",
        "Noto Sans Telugu",
        "Noto Sans Lao",
        "Noto Sans Khmer",
        "Noto Sans Myanmar",
        "Noto Sans Georgian",
        "Noto Sans Armenian",
        "Noto Ethiopic",
        "Noto Nastaliq Urdu",
        "Noto Sans Kazakh",
        "Noto Sans Mongolian",
        "Noto Sans Greek",
        "Noto Serif Thai",
    }
)

#: The families a *budget* device drops as well.  A budget phone ships a
#: smaller system partition again; the monospace family and the condensed
#: variant of the system font are the next to go.
BUDGET_TIER_DROPPED: frozenset[str] = frozenset(
    {
        "Roboto Condensed",
        "Droid Sans Mono",
        "Cutive Mono",
        "Sourc Code Pro",
        "Noto Sans Symbols 2",
        "Noto Serif",
    }
)

#: The mandatory families of every Android device.  A site that checks
#: for these gets an answer from every real device, so a profile that lacks
#: one of them is immediately suspicious.
MANDATORY_FONTS: frozenset[str] = frozenset({"Roboto", "Noto Color Emoji"})

#: The device tiers, from the smallest system partition to the largest.
#: The tier is decided by the device's RAM and its release year, because
#: those are the two things that actually decide how much room the
#: ``fonts.xml`` gets on a real device.
TIERS: tuple[tuple[str, int, int], ...] = (
    # (name, ram_gb_floor, year_floor)
    ("budget", 0, 0),
    ("mid", 6, 0),
    ("flagship", 8, 2022),
)

#: The tier each Android release ships with.  Android 13 and later trim
#: the system partition harder than Android 12 and earlier did, and the
#: Android 14 and 15 devices that ship in 2024 and 2025 are almost all
#: mid tier or better.
RELEASE_TIER_FLOOR: dict[str, str] = {
    "10": "budget",
    "11": "budget",
    "12": "budget",
    "13": "mid",
    "14": "flagship",
    "15": "flagship",
}


def device_tier(ram_gb: int, year: int) -> str:
    """Return the font tier of a device with ``ram_gb`` of RAM from ``year``.

    The rules mirror what the manufacturers actually ship:

    * a device with 8 GB or more that is recent enough is a flagship,
    * a device with 6 GB or more is a mid tier device,
    * everything else is a budget device.

    ``year`` guards the flagship rule so that a 2019 flagship that still
    runs Android 11 does not claim the font set of a 2024 flagship.
    """

    if ram_gb >= 8 and year >= 2022:
        return "flagship"
    if ram_gb >= 6:
        return "mid"
    return "budget"


def fonts_for_locale(locale: str, base: tuple[str, ...]) -> list[str]:
    """Return the font list a device with ``locale`` would report.

    ``base`` is the Latin core set.  The script families of the locale are
    added, and the families that belong to a *different* script are dropped,
    because no real device carries fonts for scripts its user cannot read.
    """

    primary = locale.split("-")[0].lower()
    result = list(base)

    for name in SCRIPT_FONTS.get(primary, ()):
        if name not in result:
            result.append(name)

    for name in SCRIPT_EXCLUSIONS.get(primary, ()):
        if name in result:
            result.remove(name)

    return result


def fonts_for_device(
    locale: str,
    base: tuple[str, ...],
    ram_gb: int,
    year: int,
    release: str = "",
) -> list[str]:
    """Return the font list of a device in ``locale`` with this tier.

    The script follows the locale and the tier follows the device, and
    the two are applied in that order: the script families are added
    first, so that a locale whose script the tier would drop still keeps
    its own script family — a Japanese user's phone always has a CJK
    font, even if it is a budget phone.
    """
    primary = locale.split("-")[0].lower()
    tier = device_tier(ram_gb, year)
    dropped: frozenset[str] = frozenset()

    # Every tier below the flagship one drops the optional families.
    if tier in {"budget", "mid"}:
        dropped = LOW_TIER_DROPPED

    # The script family of this locale survives every tier.
    own_script = set(SCRIPT_FONTS.get(primary, ()))
    protected = set(MANDATORY_FONTS) | own_script

    result = fonts_for_locale(locale, base)
    result = [name for name in result if name not in dropped or name in protected]

    # A budget device has a smaller system partition, so it also drops the
    # decorative families that the mid and flagship sets carry.
    if tier == "budget":
        result = [name for name in result if name not in BUDGET_TIER_DROPPED]

    # The tier floor of the release trims the set further.  Android 13 and
    # later trim harder than Android 12 and earlier.
    floor_tier = RELEASE_TIER_FLOOR.get(release, "")
    extra = LOW_TIER_DROPPED if floor_tier == "budget" else frozenset()

    protected_again = set(MANDATORY_FONTS) | own_script
    result = [name for name in result if name not in extra or name in protected_again]

    return result


__all__ = [
    "BUDGET_TIER_DROPPED",
    "LOW_TIER_DROPPED",
    "MANDATORY_FONTS",
    "RELEASE_TIER_FLOOR",
    "SCRIPT_EXCLUSIONS",
    "SCRIPT_FONTS",
    "TIERS",
    "device_tier",
    "fonts_for_device",
    "fonts_for_locale",
]
