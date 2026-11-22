"""A bundle is runnable on a machine that has never seen this project.

The bundle is the product.  Everything else -- the generator, the
runtime builder, the brand assets -- is the means of making one.  So
the bundle has to hold up on its own: it has to contain everything it
needs, and it has to be readable by something that was never written
by this project.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from novaprofile.bundle import build_bundle, platform_for_profile, write_profile, write_runtime


@pytest.fixture(scope="module")
def profile():
    from novaprofile.core.generator import ProfileGenerator

    return ProfileGenerator().generate("pixel-8-pro-android14", master_seed="f" * 64)


class TestThePlatform:
    """The bundle picks the browser the profile belongs on."""

    def test_an_android_profile_wants_the_android_build(self, profile) -> None:
        assert platform_for_profile(profile) == "linux-arm64"

    def test_the_choice_follows_the_user_agent(self, profile) -> None:
        """The platform is read off the agent, not off the host.

        The whole fleet is mobile -- that is the product -- so the
        question the bundle has to answer is not "which kind of device
        is this" but "does the browser have to agree with the agent
        the page will read".
        """

        assert "Android" in profile.navigator.user_agent
        assert platform_for_profile(profile) == "linux-arm64"

    def test_a_desktop_agent_wants_the_desktop_build(self) -> None:
        expected = "win64" if sys.platform.startswith("win") else "linux-x64"

        class _Navigator:
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/160.0.0.0 Safari/537.36"
            )

        class _Profile:
            navigator = _Navigator()

        assert platform_for_profile(_Profile()) == expected


class TestTheBundle:
    """The bundle holds everything it needs."""

    def test_it_writes_the_runtime_and_the_profile(self, tmp_path: Path) -> None:
        report = build_bundle(
            tmp_path / "b",
            preset="pixel-8-pro-android14",
            master_seed="f" * 64,
            include_browser=False,
        )

        assert (tmp_path / "b" / "runtime.js").exists()
        assert (tmp_path / "b" / "profile.json").exists()
        assert (tmp_path / "b" / "bundle.json").exists()
        assert report.runtime_bytes > 1000
        assert report.profile_bytes > 100

    def test_the_manifest_carries_the_profile(self, tmp_path: Path) -> None:
        build_bundle(
            tmp_path / "b",
            preset="pixel-8-pro-android14",
            master_seed="f" * 64,
            include_browser=False,
        )
        manifest = json.loads((tmp_path / "b" / "bundle.json").read_text("utf-8"))

        assert manifest["preset"] == "pixel-8-pro-android14"
        assert manifest["master_seed"] == "f" * 64
        assert manifest["profile"]["navigator"]["user_agent"]

    def test_the_same_seed_gives_the_same_bundle(self, tmp_path: Path) -> None:
        build_bundle(
            tmp_path / "a",
            preset="pixel-8-pro-android14",
            master_seed="f" * 64,
            include_browser=False,
        )
        build_bundle(
            tmp_path / "b",
            preset="pixel-8-pro-android14",
            master_seed="f" * 64,
            include_browser=False,
        )

        assert (tmp_path / "a" / "profile.json").read_text("utf-8") == (
            tmp_path / "b" / "profile.json"
        ).read_text("utf-8")
        assert (tmp_path / "a" / "runtime.js").read_text("utf-8") == (
            tmp_path / "b" / "runtime.js"
        ).read_text("utf-8")

    def test_a_different_seed_gives_a_different_profile(self, tmp_path: Path) -> None:
        build_bundle(
            tmp_path / "a",
            preset="pixel-8-pro-android14",
            master_seed="f" * 64,
            include_browser=False,
        )
        build_bundle(
            tmp_path / "b",
            preset="pixel-8-pro-android14",
            master_seed="0" * 64,
            include_browser=False,
        )

        assert (tmp_path / "a" / "profile.json").read_text("utf-8") != (
            tmp_path / "b" / "profile.json"
        ).read_text("utf-8")


class TestTheRuntimeInTheBundle:
    """The script in the bundle is the one the runtime builds."""

    def test_it_parses_as_javascript(self, tmp_path: Path, profile) -> None:
        script = write_runtime(profile, tmp_path / "runtime.js")
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")

        result = subprocess.run(
            [node, "--check", str(tmp_path / "runtime.js")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert script > 1000

    def test_the_profile_is_json_that_round_trips(self, tmp_path: Path, profile) -> None:
        write_profile(profile, tmp_path / "profile.json")
        loaded = json.loads((tmp_path / "profile.json").read_text("utf-8"))

        assert loaded["navigator"]["user_agent"]
        assert loaded["screen"]["width"] > 0
