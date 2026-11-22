"""Tests for the native core status check.

The check is read-only and reads from the core tree under the NOVA
home, so the tests build a small tree of their own and point the home
at it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novaprofile.core import native_core
from novaprofile.core.cores import MANIFEST_FILENAME


def _write_core(home: Path, version: str, executable: str | None) -> Path:
    """Install a core directory with a manifest."""

    core_dir = home / "cores" / version
    core_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "core_id": version,
        "version": version,
        "major": int(version.split(".")[0]),
        "channel": "stable",
        "build_revision": "test",
        "platform": "windows",
        "executable": executable or "",
        "install_path": str(core_dir),
    }
    (core_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    if executable:
        target = core_dir / executable
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not really a browser", encoding="utf-8")

    return core_dir


def test_no_core_reports_that_the_fleet_runs_on_a_stock_browser(tmp_path, monkeypatch):
    """An empty core tree is reported as a missing core, not as an error."""

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))

    ok, detail = native_core.core_status()

    assert ok is False
    assert "no native core is installed" in detail
    assert "stock browser" in detail


def test_a_core_with_an_executable_is_reported_as_ready(tmp_path, monkeypatch):
    """An installed core with its browser is the healthy state."""

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))
    _write_core(tmp_path, "160.0.8177.0", "chrome/chrome.exe")

    ok, detail = native_core.core_status()

    assert ok is True
    assert "160.0.8177.0" in detail


def test_a_core_without_an_executable_is_reported_as_incomplete(tmp_path, monkeypatch):
    """A core directory without a browser is an incomplete installation.

    The difference matters: "no core at all" means the fleet runs on a
    stock browser, and "a core without a browser" means an install that
    went wrong.  Both are failures, and an operator needs to tell them
    apart.
    """

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))
    _write_core(tmp_path, "160.0.8177.0", None)

    ok, detail = native_core.core_status()

    assert ok is False
    assert "incomplete" in detail
    assert "160.0.8177.0" in detail


def test_the_summary_lists_every_installed_core(tmp_path, monkeypatch):
    """The summary is what the operator reads to find the cores."""

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))
    _write_core(tmp_path, "159.0.8154.0", "chrome/chrome.exe")
    _write_core(tmp_path, "160.0.8177.0", "chrome/chrome.exe")

    summary = native_core.installed_core_summary()

    versions = [version for version, _ in summary]
    assert versions == ["159.0.8154.0", "160.0.8177.0"]
    assert all(Path(path).is_dir() for _, path in summary)


def test_a_directory_without_a_manifest_is_not_a_core(tmp_path, monkeypatch):
    """A stray directory in the core tree does not become a core."""

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))
    (tmp_path / "cores" / "leftovers").mkdir(parents=True)

    assert native_core.installed_core_summary() == []
    ok, detail = native_core.core_status()
    assert ok is False
    assert "no native core is installed" in detail


def test_the_check_never_touches_the_core_tree(tmp_path, monkeypatch):
    """The check is read-only.

    The doctor runs on every machine that starts the control plane, and
    it runs on machines that are only being inspected.  A check that
    writes is a check that can break an installation.
    """

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))
    core_dir = _write_core(tmp_path, "160.0.8177.0", "chrome/chrome.exe")

    before = sorted(
        (path.relative_to(core_dir).as_posix(), path.stat().st_mtime)
        for path in core_dir.rglob("*")
    )

    native_core.core_status()

    after = sorted(
        (path.relative_to(core_dir).as_posix(), path.stat().st_mtime)
        for path in core_dir.rglob("*")
    )
    assert before == after


@pytest.mark.parametrize("spec", ["160.0.8177.0", "chrome/chrome.exe", None])
def test_the_check_survives_a_broken_manifest(tmp_path, monkeypatch, spec):
    """A manifest that cannot be parsed does not crash the doctor."""

    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path))
    core_dir = tmp_path / "cores" / "160.0.8177.0"
    core_dir.mkdir(parents=True)
    if spec is None:
        (core_dir / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    else:
        (core_dir / MANIFEST_FILENAME).write_text(spec, encoding="utf-8")

    ok, detail = native_core.core_status()
    assert isinstance(ok, bool)
    assert isinstance(detail, str)
    assert detail
