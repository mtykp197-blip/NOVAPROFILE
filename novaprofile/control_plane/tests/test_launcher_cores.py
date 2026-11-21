"""Tests for the launcher's use of the multi-core installation.

The launcher must run the core that matches the profile's declared major
version, and it must fall back to its own executable when no core manager
is configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novaprofile.core.cores import CoreManager
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.versions import CoreFallbackPolicy
from novaprofile.launcher.launcher import BrowserLauncher
from novaprofile.schema.version import BrowserVersion


def _core(tmp_path: Path, version: str, major: int) -> Path:
    source = tmp_path / f"src-{major}"
    (source / "chrome").mkdir(parents=True)
    (source / "chrome" / "chrome.exe").write_bytes(b"MZ core")
    return source


@pytest.fixture()
def manager(tmp_path) -> CoreManager:
    return CoreManager(home=tmp_path)


def test_without_a_core_manager_the_launcher_uses_its_own_binary(manager, tmp_path):
    """A single-binary installation is unaffected."""

    launcher = BrowserLauncher(chrome_path="chrome.exe")
    assert launcher.select_core(object()) is None


def test_the_declared_major_selects_its_core(manager, tmp_path):
    """A profile that declares Chromium 120 is served by the 120 core."""

    source = _core(tmp_path, "120.0.6099.144", 120)
    manager.register_core(
        version="120.0.6099.144",
        major=120,
        source_dir=source,
        executable="chrome/chrome.exe",
    )

    launcher = BrowserLauncher(chrome_path="chrome.exe", core_manager=manager)
    config = ProfileGenerator().generate(
        "pixel-8-pro-android14",
        master_seed="a" * 64,
        browser_version=BrowserVersion(major=120),
    )

    selection = launcher.select_core(config)
    assert selection is not None
    assert selection.exact is True
    assert selection.core.major == 120


def test_the_command_line_uses_the_core_executable(manager, tmp_path):
    """The core's own binary is what ends up on the command line."""

    source = _core(tmp_path, "120.0.6099.144", 120)
    manager.register_core(
        version="120.0.6099.144",
        major=120,
        source_dir=source,
        executable="chrome/chrome.exe",
    )

    launcher = BrowserLauncher(chrome_path="chrome.exe", core_manager=manager)
    config = ProfileGenerator().generate(
        "pixel-8-pro-android14",
        master_seed="a" * 64,
        browser_version=BrowserVersion(major=120),
    )

    args = launcher.build_args(config, tmp_path / "udd")
    expected = str(manager.core_dir("120.0.6099.144") / "chrome" / "chrome.exe")
    assert args[0] == expected


def test_the_error_policy_stops_the_launch(manager, tmp_path):
    """A profile that needs a missing core does not silently use another."""

    manager.register_core(
        version="120.0.6099.144",
        major=120,
        source_dir=_core(tmp_path, "120.0.6099.144", 120),
        executable="chrome/chrome.exe",
    )

    launcher = BrowserLauncher(
        chrome_path="chrome.exe",
        core_manager=manager,
        core_policy=CoreFallbackPolicy.ERROR,
    )
    config = ProfileGenerator().generate(
        "pixel-8-pro-android14",
        master_seed="a" * 64,
        browser_version=BrowserVersion(major=155),
    )

    with pytest.raises(Exception) as excinfo:
        launcher.select_core(config)
    assert "155" in str(excinfo.value)
