"""Copy the freshly built Chromium core into the packaging trees.

The desktop app ships the core under two trees: the one the core
builder publishes to, and the one the packaged application carries.  A
change to the core has to land in both, or the application keeps
running the older core and the change never reaches a profile.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SOURCE = Path(r"C:\chromium\src\out\nova-win")
CORE_VERSION = "160.0.8177.0"

TARGETS = [
    REPO / "core" / "build" / "cores" / CORE_VERSION,
    REPO / "novaprofile" / "desktop" / "dist" / "win-unpacked" / "resources" / "core",
    # The builder writes into ``dist-new`` while the packager reads
    # from ``dist``.  A core that lands in only one of the two leaves
    # the shipped application running the older one, so both trees are
    # written.
    REPO / "novaprofile" / "desktop" / "dist-new" / "win-unpacked" / "resources" / "core",
]

#: The files the core is made of.  The locales are a directory and are
#: copied as a whole.
BINARIES = [
    "chrome.dll",
    "chrome.exe",
    "chrome_100_percent.pak",
    "chrome_200_percent.pak",
    "chrome_elf.dll",
    "content_shell.pak",
    "headless_command_resources.pak",
    "icudtl.dat",
    "resources.pak",
    "shell_resources.pak",
    "snapshot_blob.bin",
    "v8_context_snapshot.bin",
    "vk_swiftshader.dll",
    "vk_swiftshader_icd.json",
    "vulkan-1.dll",
]


def main() -> int:
    if not SOURCE.is_dir():
        print(f"no build tree at {SOURCE}")
        return 1

    copied = 0
    for target in TARGETS:
        target.mkdir(parents=True, exist_ok=True)
        for name in BINARIES:
            source_file = SOURCE / name
            if not source_file.exists():
                print(f"  skip {name}: not in the build tree")
                continue
            shutil.copy2(source_file, target / name)
            copied += 1

        locales = SOURCE / "locales"
        if locales.is_dir():
            destination = target / "locales"
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(locales, destination)

        # The application runs the core under its own name, so the
        # executable has to carry both names.
        chrome_exe = target / "chrome.exe"
        if chrome_exe.exists():
            shutil.copy2(chrome_exe, target / "NOVAPROFILE.exe")

    print(f"copied {copied} files into {len(TARGETS)} trees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
