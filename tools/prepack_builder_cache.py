"""Pre-extract the electron-builder caches without symbolic links.

The Windows build of ``electron-builder`` unpacks the signing tools with
``7za -snld``, which asks for the symbolic-link privilege.  A machine
without Developer Mode cannot grant it, and the unpack fails on the two
Darwin libraries the archive happens to carry -- libraries that a
Windows build never looks at.

This pre-extracts the archives with the symbolic links turned into
ordinary files, which is all a Windows build needs, and leaves the
extracted tree where ``electron-builder`` expects to find it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


SEVEN_ZIP = Path(
    r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\novaprofile\desktop"
    r"\node_modules\7zip-bin\win\x64\7za.exe"
)


def pre_extract(root: Path) -> int:
    """Extract every archive in ``root`` next to itself."""

    archives = sorted(root.glob("*.7z"))
    done = 0
    for archive in archives:
        target = root / archive.stem
        if (target / "windows-10").is_dir():
            # The tree is already there; the only thing that was
            # missing was the symbolic links, which this machine
            # cannot make and a Windows build does not need.
            continue
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        completed = subprocess.run(
            [str(SEVEN_ZIP), "x", "-snl-", "-bd", str(archive), f"-o{target}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            print(f"failed {archive.name}: {completed.returncode}")
            continue
        done += 1
        print(f"extracted {archive.name}")
    return done


def main() -> int:
    cache = Path(
        r"C:\Users\ezycloudx-admin\AppData\Local\electron-builder\Cache"
    )
    total = 0
    for name in ("winCodeSign", "nsis"):
        folder = cache / name
        if not folder.is_dir():
            print(f"missing {folder}")
            continue
        total += pre_extract(folder)
    print(f"extracted {total} archives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
