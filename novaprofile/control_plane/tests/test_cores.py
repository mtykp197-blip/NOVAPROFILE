"""Tests for the multi-core manager and selector.

A NOVA installation may hold several Chromium cores side by side.  These
tests cover the three properties that make that safe:

* every core owns its own directory, and installing or removing one never
  disturbs another,
* the integrity seal covers the core's files but ignores the volatile
  scratch files a running browser produces, so a verification does not
  fail just because the browser wrote a log,
* the selector resolves a requested major onto an installed core with an
  explicit, predictable fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novaprofile.core.cores import (
    SEAL_FILENAME,
    CoreManager,
    CoreSelector,
    seal_directory,
    verify_directory,
)
from novaprofile.core.versions import CoreFallbackPolicy, CoreInstallError, ReleaseChannel


def _fake_core(tmp_path: Path, name: str) -> Path:
    """Create a directory that looks like an unpacked Chromium core."""

    source = tmp_path / f"src-{name}"
    (source / "chrome").mkdir(parents=True)
    (source / "chrome" / "chrome.exe").write_bytes(b"MZ fake binary")
    (source / "chrome" / "resources.pak").write_bytes(b"pak")
    (source / "chrome.dll").write_bytes(b"dll")
    (source / "LICENSE").write_text("license", encoding="utf-8")
    return source


@pytest.fixture()
def manager(tmp_path) -> CoreManager:
    return CoreManager(home=tmp_path)


def test_an_installed_core_gets_its_own_directory(manager, tmp_path):
    """Every core lives in a directory of its own."""

    source = _fake_core(tmp_path, "a")
    record = manager.register_core(
        version="120.0.6099.144", major=120, source_dir=source, executable="chrome.exe"
    )

    assert record.path.is_dir()
    assert (record.path / "chrome" / "chrome.exe").is_file()
    assert (record.path / "core.json").is_file()
    assert record.sealed is True


def test_two_cores_never_share_a_file(manager, tmp_path):
    """Installing a second core does not disturb the first."""

    first_source = _fake_core(tmp_path, "a")
    second_source = _fake_core(tmp_path, "b")
    (second_source / "chrome" / "chrome.exe").write_bytes(b"MZ different binary")

    first = manager.register_core(version="120.0.6099.144", major=120, source_dir=first_source)
    second = manager.register_core(version="155.0.8095.0", major=155, source_dir=second_source)

    assert first.path != second.path
    assert (first.path / "chrome" / "chrome.exe").read_bytes() == b"MZ fake binary"
    assert (second.path / "chrome" / "chrome.exe").read_bytes() == b"MZ different binary"


def test_removing_one_core_leaves_the_others_alone(manager, tmp_path):
    """A core can be replaced without disturbing its neighbours."""

    manager.register_core(version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a"))
    manager.register_core(version="155.0.8095.0", major=155, source_dir=_fake_core(tmp_path, "b"))

    assert manager.remove_core("120.0.6099.144") is True
    assert manager.installed_majors() == [155]


def test_the_seal_ignores_the_volatile_scratch_files(manager, tmp_path):
    """A running browser writes logs; the seal must not care."""

    source = _fake_core(tmp_path, "a")
    record = manager.register_core(version="120.0.6099.144", major=120, source_dir=source)

    # The scratch files a browser leaves behind.
    (record.path / "chrome_debug.log").write_text("noise", encoding="utf-8")
    (record.path / "chrome.exe.tmp").write_text("partial", encoding="utf-8")
    (record.path / "SingletonLock").write_text("", encoding="utf-8")
    (record.path / "crashpad" / "reports").mkdir(parents=True)
    (record.path / "crashpad" / "reports" / "dump.dmp").write_bytes(b"dump")

    ok, missing, modified = manager.verify(record.path)
    assert ok, f"missing={missing} modified={modified}"
    assert missing == []
    assert modified == []


def test_the_seal_catches_a_tampered_binary(manager, tmp_path):
    """A modified core file is detected."""

    source = _fake_core(tmp_path, "a")
    record = manager.register_core(version="120.0.6099.144", major=120, source_dir=source)

    (record.path / "chrome" / "chrome.exe").write_bytes(b"tampered")

    ok, missing, modified = manager.verify(record.path)
    assert not ok
    assert "chrome/chrome.exe" in modified


def test_the_seal_catches_a_missing_file(manager, tmp_path):
    """A removed core file is detected."""

    source = _fake_core(tmp_path, "a")
    record = manager.register_core(version="120.0.6099.144", major=120, source_dir=source)

    (record.path / "chrome" / "resources.pak").unlink()

    ok, missing, _modified = manager.verify(record.path)
    assert not ok
    assert "chrome/resources.pak" in missing


def test_the_seal_is_stable_across_reads(manager, tmp_path):
    """Sealing the same content twice yields the same digests."""

    source = _fake_core(tmp_path, "a")
    manager.register_core(version="120.0.6099.144", major=120, source_dir=source)

    core_dir = manager.core_dir("120.0.6099.144")
    first = seal_directory(core_dir)
    second = seal_directory(core_dir)
    assert first == second


def test_the_selector_finds_an_exact_core(manager, tmp_path):
    """The requested major is served by itself."""

    manager.register_core(version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a"))
    selector = CoreSelector(manager)

    selection = selector.select(120)
    assert selection.exact is True
    assert selection.fallback_used is False
    assert selection.core.major == 120


def test_the_selector_falls_back_to_the_nearest_core(manager, tmp_path):
    """Without the requested core, the nearest one is used."""

    manager.register_core(version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a"))
    manager.register_core(version="155.0.8095.0", major=155, source_dir=_fake_core(tmp_path, "b"))
    selector = CoreSelector(manager, policy=CoreFallbackPolicy.NEAREST)

    selection = selector.select(124)
    assert selection.exact is False
    assert selection.fallback_used is True
    assert selection.core.major in {120, 155}
    assert selection.requested_major == 124


def test_the_error_policy_refuses_to_guess(manager, tmp_path):
    """The strict policy names the missing version instead of guessing."""

    manager.register_core(version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a"))
    selector = CoreSelector(manager, policy=CoreFallbackPolicy.ERROR)

    with pytest.raises(CoreInstallError) as excinfo:
        selector.select(124)
    assert "124" in str(excinfo.value)


def test_the_oldest_and_newest_policies(manager, tmp_path):
    """The two extreme policies pick the extremes."""

    manager.register_core(version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a"))
    manager.register_core(version="155.0.8095.0", major=155, source_dir=_fake_core(tmp_path, "b"))

    oldest = CoreSelector(manager, policy=CoreFallbackPolicy.OLDEST).select(140)
    newest = CoreSelector(manager, policy=CoreFallbackPolicy.NEWEST).select(140)

    assert oldest.core.major == 120
    assert newest.core.major == 155


def test_the_selector_raises_when_nothing_is_installed(manager):
    """Without any core there is nothing to select."""

    with pytest.raises(CoreInstallError):
        CoreSelector(manager).select(120)


def test_the_channel_filter_selects_within_a_channel(manager, tmp_path):
    """A channel filter narrows the candidates."""

    manager.register_core(
        version="120.0.6099.144",
        major=120,
        source_dir=_fake_core(tmp_path, "a"),
        channel=ReleaseChannel.STABLE,
    )
    manager.register_core(
        version="121.0.6167.85",
        major=121,
        source_dir=_fake_core(tmp_path, "b"),
        channel=ReleaseChannel.BETA,
    )

    selector = CoreSelector(manager)
    selection = selector.select(121, channel=ReleaseChannel.BETA)
    assert selection.core.major == 121


def test_a_core_without_a_manifest_is_not_a_core(manager, tmp_path):
    """A stray directory in the cores tree is ignored."""

    manager.register_core(version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a"))
    stray = manager.cores_root / "not-a-core"
    stray.mkdir(parents=True)
    (stray / "random.txt").write_text("hello", encoding="utf-8")

    assert [record.major for record in manager.list_cores()] == [120]


def test_the_manifest_round_trips(manager, tmp_path):
    """The manifest on disk is the manifest in memory."""

    record = manager.register_core(
        version="120.0.6099.144",
        major=120,
        source_dir=_fake_core(tmp_path, "a"),
        executable="chrome.exe",
        build_revision="144",
    )

    reread = manager.read_manifest(record.path)
    assert reread.core_id == record.manifest.core_id
    assert reread.major == 120
    assert reread.executable == "chrome.exe"
    assert reread.build_revision == "144"


def test_the_seal_file_is_itself_excluded(manager, tmp_path):
    """The seal must not seal itself, or it can never verify."""

    record = manager.register_core(
        version="120.0.6099.144", major=120, source_dir=_fake_core(tmp_path, "a")
    )

    assert SEAL_FILENAME not in seal_directory(record.path)
    payload = json.loads((record.path / SEAL_FILENAME).read_text(encoding="utf-8"))
    assert SEAL_FILENAME not in payload["files"]


def test_verify_reports_a_missing_seal(manager, tmp_path):
    """A core that was never sealed cannot be verified."""

    record = manager.register_core(
        version="120.0.6099.144",
        major=120,
        source_dir=_fake_core(tmp_path, "a"),
        seal=False,
    )

    with pytest.raises(CoreInstallError):
        manager.verify(record.path)


def test_verify_directory_reports_extra_files(tmp_path):
    """A file that was not in the seal is reported."""

    root = tmp_path / "core"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    seal = seal_directory(root)

    (root / "b.txt").write_text("b", encoding="utf-8")

    ok, missing, modified = verify_directory(root, seal)
    assert not ok
    assert missing == []
    assert "b.txt" in modified
