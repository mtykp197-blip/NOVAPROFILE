"""Tests for the brand icons.

The containers are checked by reading the bytes back, not by asking the
module that wrote them whether it did its job.  A test that asks the
writer "did you write what I wanted" only proves that the writer
believes it wrote what the writer was asked to write; the same mistake
that lets a self-consistent but broken patch series through.

The readers below are therefore written independently of
:mod:`novaprofile.brand.icons`: they parse the ICO directory and the
ICNS chunk list straight out of the file, following the published
formats, and they cross-check the result against Pillow -- which is a
different implementation of the same formats and therefore an outside
opinion rather than a mirror.

The tests that need Pillow are skipped when Pillow is not installed,
because a missing optional dependency is not a broken icon.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from novaprofile.brand import icons

#: The Pillow image module, when it is installed.
#:
#: Pillow is what turns the artwork into the images that go into the
#: containers, so the tests that build an icon cannot run without it.
#: A missing optional dependency is not a broken icon, so the suite is
#: skipped rather than failed when it is absent.
Image = pytest.importorskip("PIL.Image", reason="Pillow is needed to build the icons")


# --------------------------------------------------------------------------
# Independent readers
# --------------------------------------------------------------------------


def read_ico(path: Path) -> list[dict[str, int]]:
    """The images inside a Windows icon, read from the file.

    The format is a six-byte header, one sixteen-byte directory entry
    per image and then the images themselves.  The entry holds the
    width and the height as single bytes, and 256 is written as zero
    because the field is too small to hold it.
    """

    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0, "the reserved word of an icon must be zero"
    assert kind == 1, "the type of an icon must be 1, not a cursor"

    images: list[dict[str, int]] = []
    for index in range(count):
        start = 6 + 16 * index
        width, height, _colours, _reserved, _planes, _bits, size, offset = struct.unpack(
            "<BBBBHHII", data[start : start + 16]
        )
        images.append(
            {
                "width": width or 256,
                "height": height or 256,
                "size": size,
                "offset": offset,
            }
        )
    return images


def read_icns(path: Path) -> tuple[list[dict[str, int]], list[dict[str, int]]]:
    """The chunks of a macOS icon, read from the file.

    The first return value is the table of contents, the second is the
    image chunks.  Each chunk is a four-byte type, a four-byte
    big-endian length that counts the eight bytes of its own header,
    and then the payload.
    """

    data = path.read_bytes()
    assert data[:4] == b"icns", "the magic of an icns file must be 'icns'"
    declared = struct.unpack(">I", data[4:8])[0]
    assert declared == len(data), "the header length must match the file length"

    toc: list[dict[str, int]] = []
    images: list[dict[str, int]] = []
    offset = 8
    while offset < len(data):
        chunk_type = data[offset : offset + 4]
        length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        assert length >= 8, "a chunk is at least as long as its own header"
        assert offset + length <= len(data), "a chunk must fit in the file"
        payload = data[offset + 8 : offset + length]

        if chunk_type == b"TOC ":
            for index in range(0, len(payload), 8):
                entry_type = payload[index : index + 4]
                entry_length = struct.unpack(">I", payload[index + 4 : index + 8])[0]
                toc.append({"type": entry_type, "length": entry_length})
        else:
            images.append({"type": chunk_type, "length": length, "payload": len(payload)})

        offset += length

    return toc, images


#: The chunk types an icns file must carry, with the pixel size of each.
#:
#: The list is the one in the Apple Icon Image format.  The ``ic11`` to
#: ``ic14`` entries are the two-times-scale counterparts of the icons
#: that have them, and macOS reads a missing one as a blurry icon.
EXPECTED_ICNS_CHUNKS: dict[bytes, int] = {
    b"icp4": 16,
    b"icp5": 32,
    b"icp6": 64,
    b"ic07": 128,
    b"ic08": 256,
    b"ic09": 512,
    b"ic10": 1024,
    b"ic11": 32,
    b"ic12": 64,
    b"ic13": 256,
    b"ic14": 512,
}


# --------------------------------------------------------------------------
# The artwork
# --------------------------------------------------------------------------


class TestTheArtwork:
    def test_the_logo_is_present(self) -> None:
        """The repository carries the artwork.

        The module is written so that a checkout without artwork still
        works, so this test does not live in the module: the artwork is
        a fact about the repository, and the repository is expected to
        have it.
        """

        assert icons.has_assets(), "the brand artwork is missing from the checkout"
        logo = icons.logo_path()
        assert logo is not None
        assert logo.exists()

    def test_the_logo_is_a_square_png_of_at_least_1024(self) -> None:
        """The source artwork is big enough to shrink from.

        An icon that is generated from a 256-pixel logo has no detail
        left in the small sizes, and a small size that has no detail is
        a blur.
        """

        logo = icons.logo_path()
        assert logo is not None
        width, height = icons._png_size(logo)
        assert width == height, "the logo must be square"
        assert width >= 1024, "the logo must be at least 1024 pixels wide"

    def test_the_logo_has_an_alpha_channel(self) -> None:
        """The artwork is transparent, so the icon can be.

        An icon without transparency gets a white box around it in
        every place the product is used.
        """

        logo = icons.logo_path()
        assert logo is not None
        with Image.open(logo) as image:
            assert image.mode == "RGBA", "the logo must carry an alpha channel"
            extrema = image.getchannel("A").getextrema()
        assert extrema[0] == 0, "the logo must have fully transparent pixels"
        assert extrema[1] == 255, "the logo must have fully opaque pixels"

    def test_the_wordmark_is_present(self) -> None:
        """The wordmark travels with the product."""

        wordmark = icons.wordmark_path()
        assert wordmark is not None
        assert wordmark.exists()


# --------------------------------------------------------------------------
# The Windows icon
# --------------------------------------------------------------------------


class TestTheWindowsIcon:
    def test_the_icon_carries_every_size(self, tmp_path: Path) -> None:
        """The icon has at least six sizes, from 16 to 256.

        Explorer asks for 16 in a small-icons view, 32 in a list view,
        48 in a details view and 256 in a large-icons view, and the
        shell scales whatever it is given.  An icon that only carries
        one size is blurry in every other place.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.ico"
        assert icons.write_ico(logo, target)

        sizes = {entry["width"] for entry in read_ico(target)}
        assert len(sizes) >= 6, f"the icon carries only {sorted(sizes)}"
        for required in (16, 32, 48, 64, 128, 256):
            assert required in sizes, f"the icon has no {required} pixel image"

    def test_every_image_of_the_icon_is_a_png_of_its_size(self, tmp_path: Path) -> None:
        """The directory points at real images of the promised size.

        A directory entry that points at the wrong bytes produces an
        icon that Explorer opens but renders as garbage, and a size
        field that does not match the image is exactly that.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.ico"
        assert icons.write_ico(logo, target)

        data = target.read_bytes()
        for entry in read_ico(target):
            start = entry["offset"]
            blob = data[start : start + entry["size"]]
            assert len(blob) == entry["size"], "an image runs past the end of the file"
            assert blob[:8] == b"\x89PNG\r\n\x1a\n", "an image is not a PNG"
            width, height = struct.unpack(">II", blob[16:24])
            assert (width, height) == (entry["width"], entry["height"])

    def test_the_icon_opens_in_an_outside_reader(self, tmp_path: Path) -> None:
        """Pillow reads the icon back and finds every size.

        Pillow is a separate implementation of the format, so this is
        an outside opinion rather than a mirror of the writer.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.ico"
        assert icons.write_ico(logo, target)

        with Image.open(target) as image:
            found = {size[0] for size in image.info.get("sizes", [])}
        assert {16, 32, 48, 64, 128, 256} <= found, f"an outside reader found {sorted(found)}"

    def test_the_256_pixel_size_is_written_as_zero(self, tmp_path: Path) -> None:
        """The size field of the largest image is the byte zero.

        The field is a single byte and 256 does not fit in one, so the
        format says that zero means 256.  Writing 256 as a truncated
        byte would make the image one pixel wide.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.ico"
        assert icons.write_ico(logo, target)

        largest = max(entry["width"] for entry in read_ico(target))
        assert largest == 256

        data = target.read_bytes()
        count = struct.unpack("<HHH", data[:6])[2]
        for index in range(count):
            start = 6 + 16 * index
            if data[start] == 0:
                assert data[start + 8] > 0, "the entry must carry an image"

    def test_a_missing_source_produces_no_icon(self, tmp_path: Path) -> None:
        """A build without artwork does not fail.

        The artwork is optional, and a missing file must not take the
        whole installation down with it.
        """

        target = tmp_path / "nova.ico"
        assert icons.write_ico(tmp_path / "absent.png", target) is False
        assert not target.exists()


# --------------------------------------------------------------------------
# The macOS icon
# --------------------------------------------------------------------------


class TestTheMacIcon:
    def test_the_icon_carries_every_chunk(self, tmp_path: Path) -> None:
        """The icns file has the chunk of every size macOS reads.

        The retina chunks are not optional decoration: macOS reads
        ``ic11`` to ``ic14`` as the two-times-scale version of the
        smaller icons, and an icon that lacks them is blurry on every
        retina display.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.icns"
        assert icons.write_icns(logo, target)

        _toc, images = read_icns(target)
        found = {image["type"] for image in images}
        for chunk_type in EXPECTED_ICNS_CHUNKS:
            assert chunk_type in found, f"the icon has no {chunk_type.decode()} chunk"

    def test_the_table_of_contents_is_present_and_first(self, tmp_path: Path) -> None:
        """The file opens with a table of contents.

        Apple has written one since 10.7.  It lists the type and the
        length of every chunk that follows, so that a reader can find
        one image without walking the whole file.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.icns"
        assert icons.write_icns(logo, target)

        data = target.read_bytes()
        assert data[8:12] == b"TOC ", "the first chunk must be the table of contents"

        toc, images = read_icns(target)
        assert toc, "the table of contents is empty"
        assert len(toc) == len(images), "the table must list every chunk"
        assert {entry["type"] for entry in toc} == {image["type"] for image in images}
        for entry, image in zip(toc, images, strict=True):
            assert entry["length"] == image["length"], "the table disagrees with the file"

    def test_the_table_of_contents_length_is_right(self, tmp_path: Path) -> None:
        """The table is eight bytes per chunk, plus its own header."""

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.icns"
        assert icons.write_icns(logo, target)

        data = target.read_bytes()
        length = struct.unpack(">I", data[12:16])[0]
        _toc, images = read_icns(target)
        assert length == 8 + 8 * len(images)

    def test_every_chunk_holds_a_png_of_its_size(self, tmp_path: Path) -> None:
        """The payload of each chunk is a PNG of the size it promises.

        The pixel size of a chunk is fixed by the format, so a chunk
        that carries an image of the wrong size is read by macOS as an
        image of the size the chunk promises, which is a stretched
        logo.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.icns"
        assert icons.write_icns(logo, target)

        data = target.read_bytes()
        offset = 8
        while offset < len(data):
            chunk_type = data[offset : offset + 4]
            length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
            if chunk_type != b"TOC ":
                payload = data[offset + 8 : offset + length]
                assert payload[:8] == b"\x89PNG\r\n\x1a\n", f"{chunk_type!r} is not a PNG"
                width, height = struct.unpack(">II", payload[16:24])
                expected = EXPECTED_ICNS_CHUNKS[chunk_type]
                assert (width, height) == (expected, expected), f"{chunk_type!r} is the wrong size"
            offset += length

    def test_the_icon_opens_in_an_outside_reader(self, tmp_path: Path) -> None:
        """Pillow reads the icon back and finds the retina sizes too.

        Pillow is a separate implementation of the format, so this is
        an outside opinion rather than a mirror of the writer.
        """

        logo = icons.logo_path()
        assert logo is not None
        target = tmp_path / "nova.icns"
        assert icons.write_icns(logo, target)

        with Image.open(target) as image:
            assert image.format == "ICNS"
            found = {size for size in image.info.get("sizes", [])}

        for pixels, scale in ((16, 1), (32, 1), (64, 1), (128, 1), (256, 1), (512, 1)):
            assert (pixels, pixels, scale) in found, f"no {pixels}@{scale}x image"
        for pixels in (16, 32, 128, 256):
            assert (pixels, pixels, 2) in found, f"no {pixels}@2x retina image"

    def test_a_missing_source_produces_no_icon(self, tmp_path: Path) -> None:
        """A build without artwork does not fail."""

        target = tmp_path / "nova.icns"
        assert icons.write_icns(tmp_path / "absent.png", target) is False
        assert not target.exists()


# --------------------------------------------------------------------------
# The installation
# --------------------------------------------------------------------------


class TestTheInstallation:
    def test_build_all_produces_the_whole_kit(self, tmp_path: Path) -> None:
        """The build copies the artwork and writes both icons."""

        produced = icons.build_all(tmp_path / "brand")

        assert "nova.ico" in produced
        assert "nova.icns" in produced
        assert produced["nova.ico"].exists()
        assert produced["nova.icns"].exists()
        assert (tmp_path / "brand" / "nova-logo-1024.png").exists()
        assert (tmp_path / "brand" / "nova-wordmark.png").exists()

    def test_the_installed_icon_is_a_real_multi_size_icon(self, tmp_path: Path) -> None:
        """The icon that lands in the installation opens and has the sizes.

        This is the icon Explorer and the shortcut will use, so it is
        the one that has to be right.
        """

        produced = icons.build_all(tmp_path / "brand")
        ico = produced["nova.ico"]

        sizes = {entry["width"] for entry in read_ico(ico)}
        assert {16, 32, 48, 64, 128, 256} <= sizes
        with Image.open(ico) as image:
            assert image.info.get("sizes"), "an outside reader finds no images in the icon"

    def test_the_installed_mac_icon_has_every_chunk(self, tmp_path: Path) -> None:
        """The macOS icon that lands in the installation is complete."""

        produced = icons.build_all(tmp_path / "brand")
        icns = produced["nova.icns"]

        toc, images = read_icns(icns)
        assert toc, "the table of contents is missing"
        assert {image["type"] for image in images} == set(EXPECTED_ICNS_CHUNKS)

    def test_the_installer_embeds_the_icons(self, tmp_path: Path) -> None:
        """The installer really puts the icons into the installation.

        The builder is the only thing that decides what the customer
        receives, so the icons are checked where the builder puts them:
        in the ``brand`` directory and, for the Windows icon, at the
        root of the installation, which is where Explorer looks for the
        icon of the folder itself.
        """

        from novaprofile.installer.builder import InstallerBuilder

        builder = InstallerBuilder(Path(__file__).resolve().parents[3], tmp_path / "out")
        staging = tmp_path / "staging"
        staging.mkdir()
        builder._install_brand(staging)

        brand = staging / "brand"
        assert (brand / "nova.ico").exists(), "the installer did not write the Windows icon"
        assert (brand / "nova.icns").exists(), "the installer did not write the macOS icon"
        assert (staging / "nova.ico").exists(), "the installer did not write the root icon"

        sizes = {entry["width"] for entry in read_ico(staging / "nova.ico")}
        assert {16, 32, 48, 64, 128, 256} <= sizes

        _toc, images = read_icns(brand / "nova.icns")
        assert {image["type"] for image in images} == set(EXPECTED_ICNS_CHUNKS)

        # The root icon is the same icon, not a different one.
        assert (staging / "nova.ico").read_bytes() == (brand / "nova.ico").read_bytes()

    def test_the_installer_survives_a_checkout_without_artwork(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A checkout with no artwork still installs.

        The artwork is optional.  The installer records that it has no
        icon rather than failing, because a missing icon must not stop
        a customer from getting the product.
        """

        from novaprofile.installer.builder import InstallerBuilder

        monkeypatch.setattr(icons, "has_assets", lambda: False)

        builder = InstallerBuilder(Path(__file__).resolve().parents[3], tmp_path / "out")
        staging = tmp_path / "staging"
        staging.mkdir()
        builder._install_brand(staging)

        assert not (staging / "nova.ico").exists()
        assert any("no icon" in step for step in builder.steps), builder.steps
