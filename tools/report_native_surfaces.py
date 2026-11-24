"""Report the surfaces the native core owns for a profile.

The native core publishes the list so that the JavaScript layer can
stand down on exactly those.  This reads the profile's own document and
applies the core's rule, so the list can be compared with what the
layer guards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def owned_surfaces(config: dict) -> list[str]:
    """The surfaces the native core takes care of, by the core's rule."""

    surfaces: list[str] = []

    navigator = config.get("navigator") or {}
    if (
        navigator.get("user_agent")
        or navigator.get("platform")
        or navigator.get("languages")
    ):
        surfaces.append("navigator")

    screen = config.get("screen") or {}
    if screen.get("width", 0) > 0 or screen.get("height", 0) > 0:
        surfaces.append("screen")
        surfaces.append("viewport")

    user_agent_data = config.get("user_agent_data") or {}
    if user_agent_data.get("brands"):
        surfaces.append("user_agent_data")

    webgl = config.get("webgl") or {}
    if webgl.get("unmasked_vendor") or webgl.get("unmasked_renderer"):
        surfaces.append("webgl")

    return surfaces


def main() -> int:
    path = Path(sys.argv[1])
    config = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(owned_surfaces(config), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
