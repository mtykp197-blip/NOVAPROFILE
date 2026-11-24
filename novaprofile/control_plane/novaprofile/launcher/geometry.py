"""The size of the browser window on the operator's desktop.

A profile carries two different sizes and they must not be confused.

The **profile's** size is the size of the handset it claims to be: a
1080 by 2400 screen for a mid-range Android phone.  That number is what
a site reads through ``screen.width``, ``screen.height`` and
``devicePixelRatio``, and it is part of the fingerprint.  It must never
be changed to suit the desktop.

The **window's** size is the size of the window on the operator's
monitor.  It is what the operator sees, and it has nothing to do with
what a site is told.  A 2400-pixel-tall window on a 1440-pixel-tall
monitor is a window that hangs off the bottom of the screen, with the
taskbar over it and half the page unreachable -- a nuisance, not a
fingerprint.

This module works out the second from the first: it scales the
profile's size down to fit the monitor it is actually on, keeps the
handset's aspect ratio so the page still looks like the handset, and
never lets the result grow past the monitor.

The scaling is a *window* concern only.  ``--window-size`` changes the
window; it does not change what the page reports, because the values
the page sees are injected by the core and the runtime from the profile's
own configuration.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

__all__ = [
    "WindowGeometry",
    "screen_size",
    "window_geometry_for",
    "MAX_WINDOW_WIDTH",
    "MAX_WINDOW_HEIGHT",
]

MAX_WINDOW_WIDTH = 1600
MAX_WINDOW_HEIGHT = 1000
SCREEN_MARGIN = 48
MIN_WINDOW_WIDTH = 360
MIN_WINDOW_HEIGHT = 640


@dataclass(frozen=True)
class WindowGeometry:
    """The size and the position of the window."""

    width: int
    height: int
    x: int = 0
    y: int = 0


def screen_size() -> tuple[int, int]:
    """The size of the primary monitor, in pixels.

    The size is asked of the operating system rather than guessed.  On
    Windows the call is to ``GetSystemMetrics`` with ``SM_CXSCREEN``
    and ``SM_CYSCREEN``; elsewhere the environment variables are read,
    and a machine that reports neither falls back to a common laptop
    panel rather than to something enormous.

    The values are *logical* pixels, which is what Chromium's
    ``--window-size`` expects: a 27-inch monitor at 150 per cent
    scaling is 2560 physical pixels wide and about 1707 logical ones,
    and the window is sized in the logical space.
    """

    if os.name == "nt":
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            if width > 0 and height > 0:
                return (width, height)
        except Exception:
            pass

    try:
        width = int(os.environ.get("NOVA_SCREEN_WIDTH", "") or 0)
        height = int(os.environ.get("NOVA_SCREEN_HEIGHT", "") or 0)
        if width > 0 and height > 0:
            return (width, height)
    except ValueError:
        pass

    return (1920, 1080)


def window_geometry_for(
    profile_width: int,
    profile_height: int,
    *,
    screen: tuple[int, int] | None = None,
    headful: bool = True,
) -> WindowGeometry:
    """The window size for a profile that claims a given screen size.

    Parameters
    ----------
    profile_width, profile_height:
        The size of the screen the profile claims.  These are the
        handset's dimensions and are used only for the *ratio*; they
        are never reported to a site.
    screen:
        The size of the monitor.  Defaults to the real one.
    headful:
        When the browser runs headless there is no window, so the
        profile's own size is returned unchanged.  The headless build
        has no window to fit into the screen.

    Returns
    -------
    WindowGeometry
        The size and position of the window.

    Notes
    -----
    The window keeps the handset's aspect ratio, so the page still
    looks like the handset it claims to be; it is simply smaller.  A
    window that was fitted without keeping the ratio would show a
    letterboxed page, and the operator would have to scroll around a
    black band.
    """

    if not headful:
        return WindowGeometry(
            width=int(profile_width or MAX_WINDOW_WIDTH),
            height=int(profile_height or MAX_WINDOW_HEIGHT),
        )

    monitor_width, monitor_height = screen if screen is not None else screen_size()

    usable_width = max(MIN_WINDOW_WIDTH, monitor_width - 2 * SCREEN_MARGIN)
    usable_height = max(MIN_WINDOW_HEIGHT, monitor_height - 2 * SCREEN_MARGIN)
    usable_width = min(usable_width, MAX_WINDOW_WIDTH)
    usable_height = min(usable_height, MAX_WINDOW_HEIGHT)

    width = int(profile_width or 0)
    height = int(profile_height or 0)
    if width <= 0 or height <= 0:
        return WindowGeometry(width=usable_width, height=usable_height)

    scale = min(1.0, usable_width / width, usable_height / height)
    fitted_width = max(MIN_WINDOW_WIDTH, int(width * scale))
    fitted_height = max(MIN_WINDOW_HEIGHT, int(height * scale))
    fitted_width = min(fitted_width, usable_width)
    fitted_height = min(fitted_height, usable_height)

    x = max(SCREEN_MARGIN, (monitor_width - fitted_width) // 2)
    y = max(SCREEN_MARGIN, (monitor_height - fitted_height) // 2)

    return WindowGeometry(width=fitted_width, height=fitted_height, x=x, y=y)
