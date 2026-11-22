"""Build the bundle and register it as an installed core.

The two steps belong together: a bundle that is not registered cannot
be launched, and a core that is registered without a bundle points at
an executable that is not there.  Running them in one place makes that
impossible.

What it does
------------
1. Bundles the Chromium output directory -- the executable, the
   resources it refuses to start without, and the locales -- and leaves
   the build output behind.
2. Registers the bundle as a core of the NOVA home, under the version
   the build carries, so that the launcher picks it up when a profile
   declares that major version.
3. Seals the core directory, so a later run can tell whether the files
   were changed after the installation.

Usage
-----
    python -m novaprofile.installer.install_built_core \\
        --chromium C:/chromium/src/out/nova-win \\
        --version 159.0.6896.0 \\
        --major 159 \\
        --home ~/.nova
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .chromium_bundle import BundleError, build_bundle, verify_bundle


def _default_home() -> Path:
    """The NOVA home the launcher reads."""

    try:
        from ..launcher.isolation import default_home

        return Path(default_home())
    except Exception:  # pragma: no cover - the fallback is for a bare checkout
        return Path.home() / ".nova"


def install_built_core(
    chromium_dir: Path,
    *,
    version: str,
    major: int,
    home: Path | None = None,
    work_dir: Path | None = None,
    executable: str = "chrome.exe",
) -> Path:
    """Bundle the build and register it as an installed core.

    Returns the path of the core directory, which is the one the
    launcher will use.
    """

    from ..core.cores import CoreManager

    home = Path(home) if home is not None else _default_home()
    work_dir = Path(work_dir) if work_dir is not None else home / "bundles" / version

    result = build_bundle(chromium_dir, work_dir, version=version)

    problems = verify_bundle(work_dir)
    if problems:
        raise BundleError(
            "the bundle does not verify: " + "; ".join(problems[:5])
        )

    manager = CoreManager(home=home)
    record = manager.register_core(
        version=version,
        major=major,
        source_dir=work_dir,
        executable=executable,
        platform="windows",
        build_revision=version,
    )
    return Path(record.manifest.install_path)


def main(argv: list[str] | None = None) -> int:
    """The command line."""

    parser = argparse.ArgumentParser(
        description="Bundle the built Chromium and register it as a core."
    )
    parser.add_argument(
        "--chromium",
        type=Path,
        default=Path("C:/chromium/src/out/nova-win"),
        help="the Chromium output directory",
    )
    parser.add_argument("--version", required=True, help="the version the build carries")
    parser.add_argument("--major", type=int, required=True, help="the milestone")
    parser.add_argument("--home", type=Path, default=None, help="the NOVA home")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="where the bundle is staged before it is registered",
    )
    args = parser.parse_args(argv)

    try:
        path = install_built_core(
            args.chromium,
            version=args.version,
            major=args.major,
            home=args.home,
            work_dir=args.work_dir,
        )
    except BundleError as error:
        print(f"cannot install the core: {error}")
        return 1

    print(f"the core is installed at {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - the command line
    raise SystemExit(main())
