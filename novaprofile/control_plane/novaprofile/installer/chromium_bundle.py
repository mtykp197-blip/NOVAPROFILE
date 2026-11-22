"""Package the built Chromium into an installable bundle.

The product is a control plane that drives a browser it built itself.
The browser is the part the customer cannot reproduce: building Chromium
takes hours, a diskful of toolchain and a machine with the right
clocks, so the shipped product carries the built binary rather than the
sources it was built from.

What is in the bundle
---------------------
Chromium on Windows is one executable plus a pile of side files, and
most of them are load-bearing:

* ``chrome.exe`` and the DLLs beside it -- the browser itself
* ``*.pak`` -- the resources: the localisations, the PDF viewer, the
  WebUI resources
* ``icudtl.dat`` -- the ICU data tables; Chromium refuses to start
  without them
* ``v8_context_snapshot.bin`` and the other ``*_snapshot.bin`` -- the
  V8 startup snapshots; the browser is noticeably slower without them
  but it does start
* ``locales/`` -- the per-language resources
* ``swiftshader/`` and ``vk_swiftshader.dll`` -- the software GL
  fallback

What is left out
----------------
Everything that only matters to the build:

* ``*.obj``, ``*.pdb``, ``*.ilk`` -- the object files and the debug
  symbols; they are the bulk of the output directory and none of them
  are loaded at runtime
* ``*.manifest`` beside the DLLs -- they are the side-by-side assembly
  manifests, which are only read by the installer
* ``gen/`` and ``obj/`` -- the generated sources and the objects

The result is a directory the product can point its launcher at.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: The files Chromium needs beside the executable, by extension.
#
#: The list is deliberately by pattern rather than by name: Chromium
#: renames its resources between milestones (``resources.pak`` became
#: ``chrome_100_percent.pak`` and friends), and a bundle that names
#: them one by one breaks on the next release.
REQUIRED_PATTERNS: tuple[str, ...] = (
    "*.pak",
    "*.dat",
    "*_snapshot.bin",
    "*.bin",
)

#: The directories Chromium loads at runtime.
REQUIRED_DIRECTORIES: tuple[str, ...] = (
    "locales",
    "swiftshader",
)

#: The extensions that only matter to the build.
BUILD_ONLY_SUFFIXES: tuple[str, ...] = (
    ".obj",
    ".pdb",
    ".ilk",
    ".lib",
    ".exp",
    ".manifest",
)

#: The names that are copied whole, whatever they are called.
REQUIRED_NAMES: tuple[str, ...] = (
    "chrome.exe",
    "vk_swiftshader.dll",
    "vk_swiftshader_icd.json",
    "vulkan-1.dll",
)

#: The suffix of the file that records what the bundle holds.
MANIFEST_NAME = "chromium_bundle.json"


class BundleError(RuntimeError):
    """Raised when the bundle cannot be built."""


@dataclass
class BundleResult:
    """What a bundle produced."""

    root: Path
    files: int = 0
    total_bytes: int = 0
    manifest: dict[str, object] = field(default_factory=dict)

    def summary(self) -> str:
        """A one-line description of the bundle."""

        megabytes = self.total_bytes / (1024 * 1024)
        return f"{self.files} files, {megabytes:.1f} MB in {self.root}"


def _is_build_output(path: Path) -> bool:
    """Whether a file only matters to the build."""

    return path.suffix.lower() in BUILD_ONLY_SUFFIXES


def collect_files(chromium_dir: Path) -> list[Path]:
    """The files of the build that the browser loads at runtime.

    The walk is shallow on purpose.  Chromium's output directory holds
    the object files of the whole build in ``obj/`` and the generated
    sources in ``gen/``; both are excluded because they are neither
    loaded nor small.
    """

    if not chromium_dir.is_dir():
        raise BundleError(f"{chromium_dir} is not a directory")

    executable = chromium_dir / "chrome.exe"
    if not executable.is_file():
        raise BundleError(f"{executable} does not exist; the build has not finished")

    collected: list[Path] = [executable]

    for name in REQUIRED_NAMES:
        candidate = chromium_dir / name
        if candidate.is_file() and candidate not in collected:
            collected.append(candidate)

    for pattern in REQUIRED_PATTERNS:
        for candidate in sorted(chromium_dir.glob(pattern)):
            if candidate.is_file() and not _is_build_output(candidate) and candidate not in collected:
                collected.append(candidate)

    for directory in REQUIRED_DIRECTORIES:
        source = chromium_dir / directory
        if not source.is_dir():
            continue
        for candidate in sorted(source.rglob("*")):
            if candidate.is_file() and not _is_build_output(candidate):
                collected.append(candidate)

    return collected


def _digest(path: Path) -> str:
    """The SHA-256 of a file."""

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_bundle(chromium_dir: Path, target: Path, *, version: str = "") -> BundleResult:
    """Copy the built Chromium into a directory the product can launch.

    The manifest records the version, the file list and the digest of
    every file, so that a customer can tell which build their
    installation carries and so that a corrupted download is
    detectable.
    """

    chromium_dir = chromium_dir.resolve()
    target = target.resolve()

    files = collect_files(chromium_dir)
    target.mkdir(parents=True, exist_ok=True)

    result = BundleResult(root=target)
    entries: list[dict[str, object]] = []

    for source in files:
        relative = source.relative_to(chromium_dir)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

        size = destination.stat().st_size
        result.files += 1
        result.total_bytes += size
        entries.append(
            {
                "path": relative.as_posix(),
                "bytes": size,
                "sha256": _digest(destination),
            }
        )

    manifest: dict[str, object] = {
        "version": version,
        "files": entries,
        "count": len(entries),
        "bytes": result.total_bytes,
    }
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    result.manifest = manifest
    return result


def verify_bundle(target: Path) -> list[str]:
    """The problems a bundle has, if it has any.

    A bundle whose files do not match the manifest is a bundle whose
    browser will crash in a way that is very hard to read, so the check
    compares the digests rather than the sizes.
    """

    target = target.resolve()
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"{manifest_path} does not exist; the bundle has no manifest"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return ["the manifest carries no file list"]

    problems: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("the manifest carries an entry that is not a record")
            continue
        relative = str(entry.get("path", ""))
        expected = str(entry.get("sha256", ""))
        candidate = target / relative
        if not candidate.is_file():
            problems.append(f"{relative} is missing")
            continue
        if _digest(candidate) != expected:
            problems.append(f"{relative} does not match its digest")

    executable = target / "chrome.exe"
    if not executable.is_file():
        problems.append("chrome.exe is missing")

    return problems


def main(argv: list[str] | None = None) -> int:
    """The command line of the bundler."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Package the built Chromium into an installable bundle."
    )
    parser.add_argument(
        "--chromium",
        type=Path,
        default=Path("C:/chromium/src/out/nova-win"),
        help="the Chromium output directory",
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="where the bundle is written",
    )
    parser.add_argument("--version", default="", help="the version the bundle carries")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="check an existing bundle instead of building one",
    )
    args = parser.parse_args(argv)

    if args.verify:
        problems = verify_bundle(args.target)
        for problem in problems:
            print(problem)
        return 1 if problems else 0

    try:
        result = build_bundle(args.chromium, args.target, version=args.version)
    except BundleError as error:
        print(f"cannot build the bundle: {error}")
        return 1

    print(result.summary())
    return 0


if __name__ == "__main__":  # pragma: no cover - the command line
    raise SystemExit(main())
