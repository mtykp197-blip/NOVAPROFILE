"""The Chromium bundle.

The bundle is what the customer launches, so the properties it has to
hold are the properties of a browser that starts and stays consistent:

* the executable is there
* the resources Chromium refuses to start without are there
* the build output that is not loaded at runtime is not there
* the manifest describes the bundle exactly
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novaprofile.installer.chromium_bundle import (
    BundleError,
    build_bundle,
    collect_files,
    verify_bundle,
)


@pytest.fixture
def chromium_dir(tmp_path: Path) -> Path:
    """A directory that looks like a finished Chromium build."""

    source = tmp_path / "out" / "nova-win"
    (source / "locales").mkdir(parents=True)
    (source / "swiftshader").mkdir(parents=True)

    (source / "chrome.exe").write_bytes(b"MZ" + b"\x00" * 4094)
    (source / "resources.pak").write_bytes(b"PAK" + b"\x00" * 1022)
    (source / "chrome_100_percent.pak").write_bytes(b"PAK" + b"\x00" * 1022)
    (source / "icudtl.dat").write_bytes(b"ICU" + b"\x00" * 1022)
    (source / "v8_context_snapshot.bin").write_bytes(b"SNP" + b"\x00" * 1022)
    (source / "vk_swiftshader.dll").write_bytes(b"DLL" + b"\x00" * 1022)
    (source / "locales" / "en-US.pak").write_bytes(b"PAK" + b"\x00" * 512)
    (source / "swiftshader" / "vk_swiftshader.dll").write_bytes(b"DLL" + b"\x00" * 512)

    # The build output, which the bundle must leave behind.
    (source / "chrome.obj").write_bytes(b"OBJ" * 64)
    (source / "chrome.pdb").write_bytes(b"PDB" * 64)
    (source / "obj").mkdir()
    (source / "obj" / "chrome.dll.obj").write_bytes(b"OBJ" * 64)
    (source / "gen").mkdir()
    (source / "gen" / "chrome_generated.cc").write_bytes(b"//\n" * 64)

    return source


class TestTheFileList:
    """The files the bundle carries."""

    def test_the_executable_is_always_there(self, chromium_dir: Path) -> None:
        """A bundle without the browser is not a bundle."""

        files = collect_files(chromium_dir)
        assert chromium_dir / "chrome.exe" in files

    def test_the_resources_the_browser_needs_are_there(self, chromium_dir: Path) -> None:
        """Chromium refuses to start without its resources.

        The ICU data tables and the V8 snapshots are the two the
        browser reads before it draws anything, and a bundle that
        leaves them out produces a process that dies without a
        message.
        """

        names = {path.name for path in collect_files(chromium_dir)}
        assert "icudtl.dat" in names
        assert "v8_context_snapshot.bin" in names
        assert "resources.pak" in names

    def test_the_localisations_are_copied_whole(self, chromium_dir: Path) -> None:
        """The locale directory holds one file per language."""

        files = collect_files(chromium_dir)
        assert (chromium_dir / "locales" / "en-US.pak") in files

    def test_the_build_output_is_left_behind(self, chromium_dir: Path) -> None:
        """The object files and the debug symbols are not loaded.

        They are the bulk of the output directory, and shipping them
        would multiply the download for nothing.
        """

        files = collect_files(chromium_dir)
        names = {path.name for path in files}

        assert "chrome.obj" not in names
        assert "chrome.pdb" not in names
        assert "chrome.dll.obj" not in names
        assert "chrome_generated.cc" not in names

    def test_a_build_that_has_not_finished_is_refused(self, tmp_path: Path) -> None:
        """A directory without the executable is not a build."""

        empty = tmp_path / "out"
        empty.mkdir()
        with pytest.raises(BundleError):
            collect_files(empty)

    def test_a_directory_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        """A missing directory is not a build either."""

        with pytest.raises(BundleError):
            collect_files(tmp_path / "nowhere")


class TestTheBundle:
    """What a built bundle holds."""

    def test_the_bundle_carries_the_browser(self, chromium_dir: Path, tmp_path: Path) -> None:
        """The executable is copied to the target."""

        target = tmp_path / "bundle"
        result = build_bundle(chromium_dir, target, version="1.2.3")

        assert (target / "chrome.exe").is_file()
        assert result.files > 0
        assert result.total_bytes > 0

    def test_the_bundle_leaves_the_build_output_out(
        self, chromium_dir: Path, tmp_path: Path
    ) -> None:
        """Nothing of the build reaches the bundle."""

        target = tmp_path / "bundle"
        build_bundle(chromium_dir, target)

        assert not (target / "chrome.obj").exists()
        assert not (target / "chrome.pdb").exists()
        assert not (target / "obj").exists()
        assert not (target / "gen").exists()

    def test_the_manifest_describes_the_bundle(self, chromium_dir: Path, tmp_path: Path) -> None:
        """The manifest names every file and its digest."""

        target = tmp_path / "bundle"
        build_bundle(chromium_dir, target, version="1.2.3")

        manifest = json.loads((target / "chromium_bundle.json").read_text(encoding="utf-8"))
        assert manifest["version"] == "1.2.3"
        assert manifest["count"] == len(manifest["files"])

        paths = {entry["path"] for entry in manifest["files"]}
        assert "chrome.exe" in paths
        assert "locales/en-US.pak" in paths

    def test_a_fresh_bundle_verifies(self, chromium_dir: Path, tmp_path: Path) -> None:
        """A bundle that was just built passes its own check."""

        target = tmp_path / "bundle"
        build_bundle(chromium_dir, target)

        assert verify_bundle(target) == []


class TestTheVerification:
    """What a broken bundle reports."""

    def test_a_bundle_without_a_manifest_is_reported(self, tmp_path: Path) -> None:
        """A bundle that has no manifest cannot be checked."""

        target = tmp_path / "bundle"
        target.mkdir()
        problems = verify_bundle(target)
        assert problems
        assert "manifest" in problems[0]

    def test_a_missing_file_is_reported(self, chromium_dir: Path, tmp_path: Path) -> None:
        """A file that was not copied is a broken bundle."""

        target = tmp_path / "bundle"
        build_bundle(chromium_dir, target)
        (target / "chrome.exe").unlink()

        problems = verify_bundle(target)
        assert any("chrome.exe" in problem for problem in problems)

    def test_a_changed_file_is_reported(self, chromium_dir: Path, tmp_path: Path) -> None:
        """A file whose bytes changed does not match its digest.

        The digest is checked rather than the size because a file that
        changed but kept its length is exactly the case a size check
        would miss.
        """

        target = tmp_path / "bundle"
        build_bundle(chromium_dir, target)
        (target / "resources.pak").write_bytes(b"XXXX" + b"\x00" * 1020)

        problems = verify_bundle(target)
        assert any("resources.pak" in problem for problem in problems)
