"""Font catalogue per script.

``document.fonts`` (and the width of a rendered glyph) tells a site which
scripts the user's device can draw.  A German user whose device reports only
CJK fonts, or a Japanese user whose device reports no CJK font at all, is
either a bot or a very unusual person.

The base set in :mod:`novaprofile.core.generator` is the Latin part.  This
module adds the script-specific families that a device in the given locale
would actually carry, so the font list follows the profile's language.
"""

from __future__ import annotations

#: Fonts that a device using the given script normally has installed.
#: The key is the primary language subtag (``ja``, ``ko``, ``zh``, ...).
SCRIPT_FONTS: dict[str, tuple[str, ...]] = {
    "ja": ("Noto Sans CJK JP", "Noto Serif CJK JP"),
    "ko": ("Noto Sans CJK KR", "Noto Serif CJK KR"),
    "zh": ("Noto Sans CJK SC", "Noto Serif CJK SC"),
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
    "am": ("Noto Sans Ethiopic",),
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
        "Noto Sans Thai",
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


__all__ = ["SCRIPT_EXCLUSIONS", "SCRIPT_FONTS", "fonts_for_locale"]
