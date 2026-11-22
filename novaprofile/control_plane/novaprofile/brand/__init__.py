"""The brand kit of NOVA PROFILE.

The package holds the artwork and the colour palette.  The artwork is
optional: a checkout that carries no images still runs, and the
installer then ships without an icon rather than failing.
"""

from __future__ import annotations

from novaprofile.brand.icons import (
    ASSET_DIR,
    BRAND_DIR,
    ICON_SIZES,
    build_all,
    has_assets,
    logo_path,
    wordmark_path,
    write_icns,
    write_ico,
)
from novaprofile.brand.theme import (
    ACCENT,
    ACCENT_SECONDARY,
    BACKGROUND,
    DANGER,
    PALETTE,
    SUCCESS,
    SURFACE,
    TEXT,
    TEXT_MUTED,
    WARNING,
    Colour,
    gradient,
)

__all__ = [
    "ACCENT",
    "ACCENT_SECONDARY",
    "ASSET_DIR",
    "BACKGROUND",
    "BRAND_DIR",
    "DANGER",
    "ICON_SIZES",
    "PALETTE",
    "SUCCESS",
    "SURFACE",
    "TEXT",
    "TEXT_MUTED",
    "WARNING",
    "Colour",
    "build_all",
    "gradient",
    "has_assets",
    "logo_path",
    "wordmark_path",
    "write_icns",
    "write_ico",
]
