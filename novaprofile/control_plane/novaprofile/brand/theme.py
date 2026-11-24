"""The NOVA PROFILE colour palette.

Every colour the engine draws with is named here, so that the command
line, the installer and the brand assets cannot drift apart.  A tool
that shows a different blue in its banner and in its own shortcut is a
tool that looks assembled rather than designed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Colour:
    """One colour of the palette."""

    #: The hex value, with the leading hash.
    hex: str
    #: What the colour is for.
    name: str

    def __str__(self) -> str:
        """The hex value, so a toolkit that formats a colour gets a colour."""

        return self.hex

    @property
    def rgb(self) -> tuple[int, int, int]:
        """The colour as a red, green and blue triple."""

        value = self.hex.lstrip("#")
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    @property
    def rich(self) -> str:
        """The colour in the markup the console library understands."""

        return f"#{self.hex.lstrip('#')}"


#: The deep background of the product.
BACKGROUND = Colour("#0B1020", "background")
#: The lighter panel background.
SURFACE = Colour("#131A2E", "surface")
#: The primary accent, at the start of the gradient.
ACCENT = Colour("#22D3EE", "accent")
#: The secondary accent, at the end of the gradient.
ACCENT_SECONDARY = Colour("#7C5CFF", "accent-secondary")
#: The colour of the main text.
TEXT = Colour("#FFFFFF", "text")
#: The colour of the secondary text.
TEXT_MUTED = Colour("#94A3B8", "text-muted")
#: The colour of a warning.
WARNING = Colour("#F59E0B", "warning")
#: The colour of a failure.
DANGER = Colour("#EF4444", "danger")
#: The colour of a success.
SUCCESS = Colour("#22C55E", "success")

#: Every colour of the palette, in the order they are used.
PALETTE: tuple[Colour, ...] = (
    BACKGROUND,
    SURFACE,
    ACCENT,
    ACCENT_SECONDARY,
    TEXT,
    TEXT_MUTED,
    WARNING,
    DANGER,
    SUCCESS,
)


def gradient() -> tuple[str, str]:
    """The two ends of the brand gradient."""

    return (ACCENT.rich, ACCENT_SECONDARY.rich)


__all__ = [
    "ACCENT",
    "ACCENT_SECONDARY",
    "BACKGROUND",
    "DANGER",
    "PALETTE",
    "SUCCESS",
    "SURFACE",
    "TEXT",
    "TEXT_MUTED",
    "WARNING",
    "Colour",
    "gradient",
]
