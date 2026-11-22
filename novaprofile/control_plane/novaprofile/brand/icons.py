"""The brand assets of NOVA PROFILE.

The images live in ``novaprofile/brand/`` as PNG files.  This module
finds them, and it can turn the largest one into the multi-resolution
``.ico`` file Windows wants for a shortcut and the ``.icns`` file macOS
wants for an application bundle.

The module is written so that a checkout **without** the images still
works.  The assets are artwork, and a missing artwork must not stop the
engine from generating profiles; the installer then simply does without
an icon rather than failing the installation.
"""

from __future__ import annotations

import struct
from pathlib import Path

#: The directory the artwork lives in.
BRAND_DIR = Path(__file__).resolve().parent

#: The directory the artwork is kept in.
#:
#: The assets sit beside the package rather than inside it, so that a
#: checkout can replace the artwork without touching the code.  The
#: package directory is accepted as well, so that an installation that
#: carries the package alone still finds its images.
ASSET_DIR = BRAND_DIR.parent / "asset"

#: The name of the largest PNG, without the extension.
LOGO_STEM = "nova-logo-1024"

#: The name of the wordmark PNG, without the extension.
WORDMARK_STEM = "nova-wordmark"

#: The directories the artwork is looked for in, in the order they are
#: searched.
#:
#: The artwork is drawn in the repository's ``asset`` directory and is
#: copied into ``novaprofile/asset`` for a package that carries it.
#: A checkout, an installed package and a source tree all have the
#: artwork in a different place, so the directories are found by walking
#: up from the package: the first directory that holds an ``asset``
#: folder with the artwork in it comes first.
def _asset_dirs() -> tuple[Path, ...]:
    """The directories the artwork is looked for in."""

    found: list[Path] = []
    directory = BRAND_DIR

    for _ in range(6):
        candidate = directory / "asset"
        if candidate not in found and (candidate / f"{LOGO_STEM}.png").exists():
            found.append(candidate)
        parent = directory.parent
        if parent == directory:
            break
        directory = parent

    # The directory beside the package is always searched, even when it
    # holds no artwork yet, so that the order does not change when the
    # artwork appears.
    for candidate in (ASSET_DIR, BRAND_DIR):
        if candidate not in found:
            found.append(candidate)

    return tuple(found)


#: The sizes the Windows icon carries.
ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)


def _find(stem: str) -> Path | None:
    """The path of an asset, or ``None`` when it is not there.

    The asset directories are searched in order, so that a checkout
    with a replaced artwork wins over the copy that shipped with the
    package.
    """

    for folder in _asset_dirs():
        candidate = folder / f"{stem}.png"
        if candidate.exists():
            return candidate
    return None


def logo_path() -> Path | None:
    """The path of the largest logo PNG."""

    return _find(LOGO_STEM)


def wordmark_path() -> Path | None:
    """The path of the wordmark PNG."""

    return _find(WORDMARK_STEM)


def has_assets() -> bool:
    """Whether the brand artwork is present."""

    return logo_path() is not None


def _png_size(path: Path) -> tuple[int, int]:
    """The width and height of a PNG, read from its header.

    The header of a PNG is fixed: eight bytes of signature, then a
    length, then the type ``IHDR``, then the width and the height as
    four-byte big-endian integers.  Reading them here means the module
    can report the size of an asset without opening it as an image.
    """

    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} is not a PNG file")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def write_ico(source: Path, target: Path, sizes: tuple[int, ...] = ICON_SIZES) -> bool:
    """Write a multi-resolution Windows icon from a PNG.

    The PNG is resized with Pillow when it is installed, and the
    resized images are written into the icon container by hand.  The
    container format is simple enough that writing it directly is
    clearer than pulling in a library for it: a six-byte header, one
    sixteen-byte directory entry per image, and then the images
    themselves, each prefixed by its own length.

    ``False`` is returned when the artwork is missing or Pillow is not
    installed.  A build without an icon is a build without an icon, not
    a failed build.
    """

    if not source.exists():
        return False

    try:
        from PIL import Image
    except ImportError:
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        frames: list[tuple[int, int, bytes]] = []
        for size in sizes:
            resized = image.convert("RGBA").resize((size, size), Image.LANCZOS)
            import io

            buffer = io.BytesIO()
            resized.save(buffer, format="PNG")
            frames.append((size, size, buffer.getvalue()))

    with target.open("wb") as handle:
        # The header: the reserved word, the type (1 is an icon) and
        # the number of images.
        handle.write(struct.pack("<HHH", 0, 1, len(frames)))

        offset = 6 + 16 * len(frames)
        for width, height, data in frames:
            # The directory entry.  A size of 256 is written as zero,
            # because the field is a single byte and 256 does not fit
            # in one.
            handle.write(
                struct.pack(
                    "<BBBBHHII",
                    width if width < 256 else 0,
                    height if height < 256 else 0,
                    0,
                    0,
                    1,
                    32,
                    len(data),
                    offset,
                )
            )
            offset += len(data)

        for _, _, data in frames:
            handle.write(data)

    return True


def write_icns(source: Path, target: Path) -> bool:
    """Write a macOS application icon from a PNG.

    The ``icns`` container is an eight-byte header with the literal
    ``icns``, a total length, and then a sequence of typed chunks.  Each
    chunk carries its own type, its own length and its own data; the
    data of a chunk is a PNG file for the types macOS reads that way.

    ``False`` is returned when the artwork is missing or Pillow is not
    installed.
    """

    if not source.exists():
        return False

    try:
        from PIL import Image
    except ImportError:
        return False

    import io

    #: The size of each entry and the type that carries it.
    entries = (
        (16, "icp4"),
        (32, "icp5"),
        (64, "icp6"),
        (128, "ic07"),
        (256, "ic08"),
        (512, "ic09"),
        (1024, "ic10"),
    )

    chunks: list[bytes] = []
    with Image.open(source) as image:
        for size, chunk_type in entries:
            resized = image.convert("RGBA").resize((size, size), Image.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format="PNG")
            data = buffer.getvalue()
            chunks.append(chunk_type.encode("ascii") + struct.pack(">I", len(data) + 8) + data)

    target.parent.mkdir(parents=True, exist_ok=True)
    body = b"".join(chunks)
    with target.open("wb") as handle:
        handle.write(b"icns" + struct.pack(">I", len(body) + 8) + body)

    return True


def build_all(target_dir: Path) -> dict[str, Path]:
    """Build every derived asset and report where they went.

    The artwork itself is copied as well, so that an installation
    carries the whole brand kit and not only the icon.
    """

    produced: dict[str, Path] = {}
    target_dir.mkdir(parents=True, exist_ok=True)

    logo = logo_path()
    if logo is not None:
        import shutil

        for stem in (LOGO_STEM, "nova-logo-512", "nova-logo-256", "nova-logo-128"):
            source = _find(stem)
            if source is not None:
                destination = target_dir / source.name
                shutil.copy2(source, destination)
                produced[source.name] = destination

        ico = target_dir / "nova.ico"
        if write_ico(logo, ico):
            produced["nova.ico"] = ico

        icns = target_dir / "nova.icns"
        if write_icns(logo, icns):
            produced["nova.icns"] = icns

    wordmark = wordmark_path()
    if wordmark is not None:
        import shutil

        destination = target_dir / wordmark.name
        shutil.copy2(wordmark, destination)
        produced[wordmark.name] = destination

    return produced


__all__ = [
    "BRAND_DIR",
    "ICON_SIZES",
    "LOGO_STEM",
    "WORDMARK_STEM",
    "build_all",
    "has_assets",
    "logo_path",
    "wordmark_path",
    "write_icns",
    "write_ico",
]
