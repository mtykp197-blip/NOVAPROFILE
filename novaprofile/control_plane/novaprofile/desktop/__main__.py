"""The entry point of the packaged NOVA PROFILE desktop application.

The window is the product for the person who uses it, so the packaged
executable opens the window directly rather than a command prompt.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    """Open the desktop window."""

    # A frozen build re-executes the entry point when a child process is
    # spawned.  Without this guard the child would open a second window
    # every time the window starts a worker.
    if getattr(sys, "frozen", False):
        multiprocessing.freeze_support()

    from novaprofile.desktop.app import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
