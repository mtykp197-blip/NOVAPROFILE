"""The installation of a built core.

The bundle is what the browser is; the registration is what makes the
launcher use it.  A customer who installs a core and finds that the
launcher still starts the system Chrome has been sold a broken product,
so the two steps are checked together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novaprofile.core.cores import CoreManager
from novaprofile.installer.chromium_bundle import BundleError
from novaprofile.installer.install_built_core import install_built_core


@pytest.fixture
def chromium_dir(tmp_path: Path) -> Path:
    """A directory that looks like a finished Chromium build."""

    source = tmp_path / "out"
    (source / "locales").mkdir(parents=True)
    (source / "chrome.exe").write_bytes(b"MZ" + b"\x00" * 4094)
    (source / "resources.pak").write_bytes(b"PAK" + b"\x00" * 1022)
    (source / "icudtl.dat").write_bytes(b"ICU" + b"\x00" * 1022)
    (source / "locales" / "en-US.pak").write_bytes(b"PAK" + b"\x00" * 512)
    return source


class TestTheInstallation:
    """What an installation leaves behind."""

    def test_the_core_lands_in_the_cores_tree(
        self, chromium_dir: Path, tmp_path: Path
    ) -> None:
        """The core lives where the launcher looks for it."""

        home = tmp_path / "home"
        path = install_built_core(
            chromium_dir,
            version="159.0.1.2",
            major=159,
            home=home,
            work_dir=tmp_path / "work",
        )

        manager = CoreManager(home=home)
        assert manager.core_dir("159.0.1.2").is_dir()
        assert (manager.core_dir("159.0.1.2") / "chrome.exe").is_file()
        assert path == manager.core_dir("159.0.1.2")

    def test_the_launcher_can_see_the_installed_major(
        self, chromium_dir: Path, tmp_path: Path
    ) -> None:
        """The selector finds the core by its milestone."""

        home = tmp_path / "home"
        install_built_core(
            chromium_dir,
            version="159.0.1.2",
            major=159,
            home=home,
            work_dir=tmp_path / "work",
        )

        manager = CoreManager(home=home)
        assert 159 in manager.installed_majors()

    def test_the_core_carries_its_manifest(
        self, chromium_dir: Path, tmp_path: Path
    ) -> None:
        """The manifest names the executable the launcher will run."""

        home = tmp_path / "home"
        install_built_core(
            chromium_dir,
            version="159.0.1.2",
            major=159,
            home=home,
            work_dir=tmp_path / "work",
        )

        manager = CoreManager(home=home)
        record = manager.find_core(159)
        assert record is not None
        assert record.manifest.executable == "chrome.exe"
        assert (Path(record.manifest.install_path) / "chrome.exe").is_file()

    def test_the_core_is_sealed(
        self, chromium_dir: Path, tmp_path: Path
    ) -> None:
        """A sealed core can be checked against its own digests."""

        home = tmp_path / "home"
        install_built_core(
            chromium_dir,
            version="159.0.1.2",
            major=159,
            home=home,
            work_dir=tmp_path / "work",
        )

        manager = CoreManager(home=home)
        ok, missing, changed = manager.verify(manager.core_dir("159.0.1.2"))
        assert ok, (missing, changed)

    def test_a_build_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        """An unfinished build cannot be installed."""

        with pytest.raises(BundleError):
            install_built_core(
                tmp_path / "nowhere",
                version="159.0.1.2",
                major=159,
                home=tmp_path / "home",
                work_dir=tmp_path / "work",
            )
