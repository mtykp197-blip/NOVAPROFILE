"""Generate a large fleet and check that no two profiles collide.

This is the claim the engine makes: a hundred million profiles, and not
one pair of them the same.  The check is on the *signature* -- the
values a site would read -- and not on the identifier, because the
identifier is the engine's own bookkeeping and the signature is what
the outside world sees.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261124)
    args = parser.parse_args()

    from novaprofile.core.fleet import generate_fleet  # noqa: PLC0415

    started = time.time()
    profiles = generate_fleet(count=args.count, name_prefix="chk")
    elapsed = time.time() - started

    print(f"generated {len(profiles)} profiles in {elapsed:.1f}s")

    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []

    for profile in profiles:
        parts = profile.signature_parts()
        key = "|".join(f"{k}={v}" for k, v in sorted(parts.items()))
        previous = seen.get(key)
        if previous is not None:
            duplicates.append((previous, profile.identity.id, "signature"))
        else:
            seen[key] = profile.identity.id

    print(f"distinct signatures: {len(seen)}")
    print(f"duplicates: {len(duplicates)}")
    for pair in duplicates[:5]:
        print("  ", pair)

    if duplicates:
        print("FAIL: two profiles share a signature")
        return 1

    print("OK: no two profiles share a signature")
    return 0


if __name__ == "__main__":
    sys.exit(main())
