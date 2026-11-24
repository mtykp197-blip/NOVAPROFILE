"""Re-seal the core trees after the binaries have been replaced.

The seal is a record of the digest of every file in the core.  A core
that was rebuilt has different binaries, so the recorded digests no
longer describe what is on disk, and a verification would report the
core as tampered with.

The seal is rewritten for every tree that ships the core.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO / "novaprofile" / "control_plane"))

from novaprofile.core.cores import seal_directory  # noqa: E402

CORE_VERSION = "160.0.8177.0"

TREES = [
    REPO / "core" / "build" / "cores" / CORE_VERSION,
    REPO / "novaprofile" / "desktop" / "dist" / "win-unpacked" / "resources" / "core",
]


def main() -> int:
    for tree in TREES:
        if not tree.is_dir():
            print(f"skip {tree}: not there")
            continue

        digests = seal_directory(tree)
        payload = {
            "sealed_at": datetime.now(UTC).isoformat(),
            "file_count": len(digests),
            "files": digests,
        }
        path = tree / "core.seal.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"sealed {tree} ({len(digests)} files)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
