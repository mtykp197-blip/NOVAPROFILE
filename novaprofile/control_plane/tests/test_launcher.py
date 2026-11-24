"""Tests for the NOVA launcher: isolation, command line and supervision.

Nothing in this suite starts a real browser -- the command line is verified
by inspecting the argument list that :class:`BrowserLauncher` builds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from nova_probe_fixtures import isolation_home, make_config

from novaprofile.launcher import (
    BASE_DISABLE_FEATURES,
    CORE_CONFIG_FILENAME,
    BrowserLauncher,
    ConcurrencyLimiter,
    ProcessSupervisor,
    ProfileIsolation,
    compute_max_concurrent,
    default_home,
)
from novaprofile.launcher.isolation import (
    HOME_ENV_VAR,
    LOCK_FILENAMES,
    STALE_SUFFIXES,
    STORAGE_ENTRIES,
)

# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


class TestIsolation:
    def test_create_builds_the_directory_tree(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        isolation = ProfileIsolation(home)
        user_data_dir = isolation.create("profile-a")

        assert user_data_dir.exists()
        assert user_data_dir.is_dir()
        assert user_data_dir == home / "profiles" / "profile-a"
        assert (user_data_dir / "Default").is_dir()

    def test_user_data_dir_path_is_stable(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        isolation = ProfileIsolation(home)
        expected = home / "profiles" / "profile-b"
        assert isolation.user_data_dir("profile-b") == expected
        assert not expected.exists()

    def test_environment_variable_overrides_the_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = isolation_home(tmp_path)
        monkeypatch.setenv(HOME_ENV_VAR, str(home))
        assert default_home() == home

    def test_cleanup_lock_files_removes_only_lock_artifacts(self, tmp_path: Path) -> None:
        user_data_dir = tmp_path / "profile"
        user_data_dir.mkdir()
        for name in LOCK_FILENAMES:
            (user_data_dir / name).write_text("lock", encoding="utf-8")
        (user_data_dir / "stale.tmp").write_text("x", encoding="utf-8")
        (user_data_dir / "leftover.old").write_text("x", encoding="utf-8")
        keep = user_data_dir / "Preferences"
        keep.write_text("{}", encoding="utf-8")
        keep_dir = user_data_dir / "Default"
        keep_dir.mkdir()
        (keep_dir / "History").write_text("x", encoding="utf-8")

        removed = ProfileIsolation.cleanup_lock_files(user_data_dir)

        removed_names = {p.name for p in removed}
        for name in LOCK_FILENAMES:
            assert name in removed_names
            assert not (user_data_dir / name).exists()
        assert "stale.tmp" in removed_names
        assert "leftover.old" in removed_names
        assert not (user_data_dir / "stale.tmp").exists()
        assert not (user_data_dir / "leftover.old").exists()

        # Unrelated files and directories must survive untouched.
        assert keep.exists()
        assert keep.read_text(encoding="utf-8") == "{}"
        assert keep_dir.is_dir()
        assert (keep_dir / "History").exists()

    def test_cleanup_lock_files_on_a_missing_directory_is_a_noop(self, tmp_path: Path) -> None:
        assert ProfileIsolation.cleanup_lock_files(tmp_path / "nope") == []

    def test_cleanup_lock_files_does_not_delete_directories(self, tmp_path: Path) -> None:
        user_data_dir = tmp_path / "profile"
        user_data_dir.mkdir()
        directory = user_data_dir / "SingletonLock"
        directory.mkdir()
        (directory / "inner").write_text("x", encoding="utf-8")

        ProfileIsolation.cleanup_lock_files(user_data_dir)
        assert directory.is_dir()

    def test_wipe_storage_removes_storage_but_keeps_configuration(self, tmp_path: Path) -> None:
        user_data_dir = tmp_path / "profile"
        user_data_dir.mkdir()
        for name in STORAGE_ENTRIES:
            target = user_data_dir / name
            if name in {"Cookies", "Cookies-journal"}:
                target.write_text("cookies", encoding="utf-8")
            else:
                target.mkdir()
                (target / "entry.bin").write_text("data", encoding="utf-8")

        default_profile = user_data_dir / "Default"
        default_profile.mkdir()
        (default_profile / "Local Storage").mkdir()
        (default_profile / "Cookies").write_text("c", encoding="utf-8")

        preferences = user_data_dir / "Preferences"
        preferences.write_text("{}", encoding="utf-8")
        local_state = user_data_dir / "Local State"
        local_state.write_text("{}", encoding="utf-8")
        core = user_data_dir / CORE_CONFIG_FILENAME
        core.write_text("{}", encoding="utf-8")

        ProfileIsolation.wipe_storage(user_data_dir)

        for name in STORAGE_ENTRIES:
            assert not (user_data_dir / name).exists(), f"{name} was not wiped"
        assert not (default_profile / "Local Storage").exists()
        assert not (default_profile / "Cookies").exists()

        # The configuration survives.
        assert preferences.exists()
        assert local_state.exists()
        assert core.exists()
        assert default_profile.is_dir()

    def test_wipe_storage_on_a_missing_directory_is_a_noop(self, tmp_path: Path) -> None:
        ProfileIsolation.wipe_storage(tmp_path / "nope")

    def test_remove_profile_deletes_the_tree(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        isolation = ProfileIsolation(home)
        isolation.create("profile-c")
        assert isolation.remove_profile("profile-c") is True
        assert not (home / "profiles" / "profile-c").exists()
        assert isolation.remove_profile("profile-c") is False

    def test_stale_suffixes_and_lock_names_are_sane(self) -> None:
        assert ".tmp" in STALE_SUFFIXES
        assert ".old" in STALE_SUFFIXES
        assert "SingletonLock" in LOCK_FILENAMES
        assert "SingletonCookie" in LOCK_FILENAMES
        assert "SingletonSocket" in LOCK_FILENAMES


# --------------------------------------------------------------------------
# Launcher command line
# --------------------------------------------------------------------------


class TestLauncherCommandLine:
    def _launcher(self, tmp_path: Path, **kwargs: Any) -> BrowserLauncher:
        home = isolation_home(tmp_path)
        return BrowserLauncher(
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            isolation=ProfileIsolation(home),
            **kwargs,
        )

    def test_builds_the_expected_arguments(self, tmp_path: Path) -> None:
        config = make_config()
        launcher = self._launcher(tmp_path)
        user_data_dir = tmp_path / "udd"

        args = launcher.build_args(config, user_data_dir)

        assert args[0] == r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        assert f"--user-data-dir={user_data_dir}" in args
        assert f"--user-agent={config.navigator.user_agent}" in args
        assert f"--lang={config.navigator.language}" in args
        # The window is fitted to the monitor; the handset size is never
        # used as the raw window size and the DPR is not forced.
        assert "--window-size=1080,2400" not in args
        assert any(a.startswith("--window-size=") for a in args)
        assert not any(a.startswith("--force-device-scale-factor") for a in args)
        assert "--proxy-server=http://198.51.100.7:8080" in args
        assert "--proxy-server-credentials=user:secret" in args
        assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost" in args
        # The stand-in config uses mode=proxy_only with ICE transport "relay",
        # so the strictest applicable policy wins.
        assert "--force-webrtc-ip-handling-policy=relay" in args
        assert f"--nova-timezone={config.timezone.iana_name}" in args
        assert f"--nova-locale={config.locale.primary}" in args
        assert f"--nova-config={user_data_dir / CORE_CONFIG_FILENAME}" in args
        assert "--no-first-run" in args
        assert "--disable-sync" in args

    def test_disable_features_contains_the_base_set(self, tmp_path: Path) -> None:
        launcher = self._launcher(tmp_path)
        args = launcher.build_args(make_config(), tmp_path / "udd")
        disable = next(a for a in args if a.startswith("--disable-features="))
        for feature in BASE_DISABLE_FEATURES:
            assert feature in disable

    def test_headless_adds_the_headless_flag(self, tmp_path: Path) -> None:
        launcher = self._launcher(tmp_path, headless=True)
        args = launcher.build_args(make_config(), tmp_path / "udd")
        assert "--headless=new" in args

    def test_proxy_is_omitted_when_disabled(self, tmp_path: Path) -> None:
        config = make_config()
        config.proxy.enabled = False
        launcher = self._launcher(tmp_path)
        args = launcher.build_args(config, tmp_path / "udd")
        assert not any(a.startswith("--proxy-server=") for a in args)

    def test_direct_proxy_is_omitted(self, tmp_path: Path) -> None:
        config = make_config()
        config.proxy.protocol = "direct"
        launcher = self._launcher(tmp_path)
        args = launcher.build_args(config, tmp_path / "udd")
        assert not any(a.startswith("--proxy-server=") for a in args)

    def test_webrtc_disabled_mode_is_mapped(self, tmp_path: Path) -> None:
        config = make_config()
        config.webrtc.mode = "disabled"
        launcher = self._launcher(tmp_path)
        args = launcher.build_args(config, tmp_path / "udd")
        assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args

    def test_webrtc_real_mode_is_mapped(self, tmp_path: Path) -> None:
        config = make_config()
        config.webrtc.mode = "real"
        launcher = self._launcher(tmp_path)
        args = launcher.build_args(config, tmp_path / "udd")
        assert "--force-webrtc-ip-handling-policy=default" in args

    def test_exactly_one_webrtc_ip_policy_is_emitted(self, tmp_path: Path) -> None:
        """Chrome honours the last occurrence, so only one may be present."""

        launcher = self._launcher(tmp_path)
        for mode in ("real", "proxy_only", "disabled", "altered"):
            config = make_config()
            config.webrtc.mode = mode
            args = launcher.build_args(config, tmp_path / "udd")
            policies = [a for a in args if a.startswith("--force-webrtc-ip-handling-policy=")]
            assert len(policies) == 1, f"{mode} produced {policies}"

    def test_dns_system_mode_adds_no_host_resolver_rules(self, tmp_path: Path) -> None:
        config = make_config()
        config.dns.mode = "system"
        launcher = self._launcher(tmp_path)
        args = launcher.build_args(config, tmp_path / "udd")
        assert not any(a.startswith("--host-resolver-rules=") for a in args)

    def test_extra_args_are_appended(self, tmp_path: Path) -> None:
        launcher = self._launcher(tmp_path, extra_args=["--incognito"])
        args = launcher.build_args(make_config(), tmp_path / "udd")
        assert args[-1] == "--incognito"

    def test_no_tls_or_http2_spofing_flags_are_emitted(self, tmp_path: Path) -> None:
        """The native Chromium network stack must stay untouched."""

        launcher = self._launcher(tmp_path)
        args = launcher.build_args(make_config(), tmp_path / "udd")
        forbidden = ("--tls", "--ssl", "--http2", "--quic", "--cipher", "--curve")
        for arg in args:
            for marker in forbidden:
                assert not arg.lower().startswith(marker), f"unexpected flag {arg}"

    def test_write_core_config_emits_the_config_file(self, tmp_path: Path) -> None:
        config = make_config()
        launcher = self._launcher(tmp_path)
        user_data_dir = tmp_path / "udd"
        path = launcher.write_core_config(config, user_data_dir)

        assert path == user_data_dir / CORE_CONFIG_FILENAME
        assert path.exists()
        document = json.loads(path.read_text(encoding="utf-8"))
        # The core reads the profile fields at the root, with no envelope.
        assert "identity" in document
        assert "navigator" in document
        assert "payload" not in document

    def test_write_core_config_round_trips_the_payload(self, tmp_path: Path) -> None:
        config = make_config()
        launcher = self._launcher(tmp_path)
        path = launcher.write_core_config(config, tmp_path / "udd")
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document
        assert payload["navigator"]["user_agent"] == config.navigator.user_agent
        assert payload["identity"]["id"] == config.identity.id

    def test_launch_requires_an_identity_id(self, tmp_path: Path) -> None:
        config = make_config()
        config.identity.id = ""
        launcher = self._launcher(tmp_path)
        with pytest.raises(ValueError):
            launcher.launch(config)

    def test_find_chrome_reports_a_missing_executable(self) -> None:
        with pytest.raises(FileNotFoundError):
            BrowserLauncher.find_chrome("definitely-not-a-real-chrome-binary")

    def test_format_command_quotes_arguments(self) -> None:
        rendered = BrowserLauncher.format_command(["chrome", "--flag", "a b"])
        assert rendered.startswith("chrome")
        assert "'a b'" in rendered


# --------------------------------------------------------------------------
# Concurrency limiter
# --------------------------------------------------------------------------


class TestConcurrencyLimiter:
    def test_respects_the_limit(self) -> None:
        limiter = ConcurrencyLimiter(2)
        assert limiter.max_concurrent == 2
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False
        assert limiter.active == 2
        assert limiter.available == 0

        limiter.release()
        assert limiter.active == 1
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_release_without_acquire_never_goes_negative(self) -> None:
        limiter = ConcurrencyLimiter(1)
        limiter.release()
        limiter.release()
        assert limiter.active == 0

    def test_context_manager_releases_the_slot(self) -> None:
        limiter = ConcurrencyLimiter(1)
        with limiter:
            assert limiter.active == 1
        assert limiter.active == 0

    def test_slot_context_manager_releases_on_error(self) -> None:
        limiter = ConcurrencyLimiter(1)
        with pytest.raises(RuntimeError), limiter.slot(timeout=1.0):
            raise RuntimeError("boom")
        assert limiter.active == 0

    def test_compute_max_concurrent_is_clamped(self) -> None:
        # A tiny machine still allows two profiles.
        assert compute_max_concurrent(cpu_count=1, total_ram_bytes=1, available_ram_bytes=1) >= 2
        # A huge machine is capped at 16.
        assert (
            compute_max_concurrent(
                cpu_count=256,
                total_ram_bytes=1024**4,
                available_ram_bytes=1024**4,
            )
            == 16
        )

    def test_compute_max_concurrent_accounts_for_ram(self) -> None:
        # 1.6 GB available with 800 MB per profile -> two profiles.
        limit = compute_max_concurrent(
            cpu_count=16,
            total_ram_bytes=8 * 1024**3,
            available_ram_bytes=int(1.6 * 1024**3),
        )
        assert limit == 2

    def test_default_limiter_has_a_sane_limit(self) -> None:
        limiter = ConcurrencyLimiter()
        assert 2 <= limiter.max_concurrent <= 16


# --------------------------------------------------------------------------
# Process supervisor
# --------------------------------------------------------------------------


class TestProcessSupervisor:
    def test_register_and_unregister(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        supervisor = ProcessSupervisor(ProfileIsolation(home))
        entry = supervisor.register("profile-x", pid=4242, user_data_dir=tmp_path / "udd")
        assert entry.profile_id == "profile-x"
        assert "profile-x" in supervisor.profiles
        supervisor.unregister("profile-x")
        assert "profile-x" not in supervisor.profiles

    def test_check_crashes_detects_a_dead_process(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        supervisor = ProcessSupervisor(ProfileIsolation(home))
        user_data_dir = home / "profiles" / "dead"
        user_data_dir.mkdir(parents=True)
        (user_data_dir / "SingletonLock").write_text("x", encoding="utf-8")

        # PID 0x7FFFFFFF does not exist on Windows/Linux.
        supervisor.register("dead", pid=0x7FFFFFFF, user_data_dir=user_data_dir)
        crashed = supervisor.check_crashes()

        assert len(crashed) == 1
        assert crashed[0].crashed is True
        assert crashed[0].alive is False
        # The stale lock file must be cleaned up so the profile can restart.
        assert not (user_data_dir / "SingletonLock").exists()

    def test_check_crashes_keeps_live_processes(self, tmp_path: Path) -> None:
        import os as _os

        home = isolation_home(tmp_path)
        supervisor = ProcessSupervisor(ProfileIsolation(home))
        supervisor.register("live", pid=_os.getpid(), user_data_dir=tmp_path / "udd")
        assert supervisor.check_crashes() == []
        assert "live" in supervisor.live_profiles()

    def test_reap_orphans_for_an_unknown_profile_is_empty(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        supervisor = ProcessSupervisor(ProfileIsolation(home))
        assert supervisor.reap_orphans("nope") == []

    def test_sweep_reports_crashes(self, tmp_path: Path) -> None:
        home = isolation_home(tmp_path)
        supervisor = ProcessSupervisor(ProfileIsolation(home))
        supervisor.register("dead", pid=0x7FFFFFFF, user_data_dir=tmp_path / "udd")
        crashed = supervisor.sweep()
        assert len(crashed) == 1
        assert crashed[0].crashed is True


def test_env_var_home_is_respected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path))
    assert default_home() == tmp_path
    assert os.environ[HOME_ENV_VAR] == str(tmp_path)


# --------------------------------------------------------------------------
# Integration with the real ProfileConfig
# --------------------------------------------------------------------------


def _real_config() -> Any:
    """Build a real ``ProfileConfig`` or return ``None``."""

    try:
        from novaprofile.core.generator import ProfileGenerator
    except Exception:
        return None
    try:
        generator = ProfileGenerator()
        return generator.generate("pixel-7-android14", name="Launcher Test", master_seed="b" * 64)
    except Exception:
        return None


def test_launcher_builds_args_for_the_real_profile_config(tmp_path: Path) -> None:
    config = _real_config()
    if config is None:
        pytest.skip("the real ProfileConfig is not available yet")

    home = isolation_home(tmp_path)
    launcher = BrowserLauncher(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        isolation=ProfileIsolation(home),
    )
    args = launcher.build_args(config, tmp_path / "udd")

    assert f"--user-agent={config.navigator.user_agent}" in args
    assert f"--lang={config.navigator.language}" in args
    assert f"--window-size={config.screen.width},{config.screen.height}" in args
    assert "--nova-config=" in " ".join(args)
    policies = [a for a in args if a.startswith("--force-webrtc-ip-handling-policy=")]
    assert len(policies) == 1

    path = launcher.write_core_config(config, tmp_path / "udd")
    document = json.loads(path.read_text(encoding="utf-8"))
    payload = document
    assert payload["identity"]["id"] == config.identity.id
    assert payload["navigator"]["user_agent"] == config.navigator.user_agent
