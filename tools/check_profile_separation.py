"""Check what separates a hundred thousand profiles that share a machine.

The engine's answer to "a hundred million profiles" is that most of
them share a machine: the catalogue only owns a few thousand handsets,
and a real shop of that size owns a few of every model.  So the
question is what tells two profiles that share a machine apart, and
whether a site can read the difference.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.core.fleet import generate_fleet  # noqa: E402


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    profiles = generate_fleet(count=count, name_prefix="sep")

    machine = Counter()
    for profile in profiles:
        navigator = profile.navigator
        screen = profile.screen
        webgl = profile.webgl
        machine[
            (
                navigator.platform,
                navigator.hardware_concurrency,
                navigator.device_memory,
                navigator.max_touch_points,
                screen.width,
                screen.height,
                webgl.unmasked_vendor,
                webgl.unmasked_renderer,
            )
        ] += 1

    print(f"profiles: {len(profiles)}")
    print(f"distinct machine readings: {len(machine)}")
    print(f"largest group: {max(machine.values())}")
    print()

    salts = Counter(profile.canvas_2d.salt for profile in profiles)
    print(f"distinct canvas salts: {len(salts)}")
    print(f"largest canvas salt group: {max(salts.values())}")

    webgl_salts = Counter(profile.webgl.salt for profile in profiles)
    print(f"distinct webgl salts: {len(webgl_salts)}")
    print(f"largest webgl salt group: {max(webgl_salts.values())}")

    signatures = Counter()
    for profile in profiles:
        parts = profile.signature_parts()
        signatures[repr(sorted(parts.items(), key=lambda item: item[0]))] += 1

    print()
    print(f"distinct full signatures: {len(signatures)}")
    print(f"largest full-signature group: {max(signatures.values())}")

    if len(signatures) == len(profiles):
        print("OK: every profile is distinct on the full signature")
        return 0
    print("FAIL: profiles share a full signature")
    return 1


if __name__ == "__main__":
    sys.exit(main())
