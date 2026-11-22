"""Tests for the page-context JavaScript layer.

The layer is the fallback that makes a stock Chromium carry the
fingerprint.  These tests check three things:

* the generated script is well-formed and carries the profile values,
* the runtime follows the anti-detection rules (the descriptor shape, the
  ``toString`` hiding, the no-own-properties rule),
* the injector writes, verifies and removes the payload.

The runtime is JavaScript, so it cannot be executed here without a
browser.  What can be checked is that the *contract* it relies on is
present in the generated text -- which is what these tests do.  The
behavioural check belongs to the end-to-end harness, which runs a real
browser.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.seed import new_master_seed
from novaprofile.js_runtime import (
    JS_RUNTIME_FILENAME,
    InjectionPoint,
    JSRuntimeBuilder,
    build_script,
    remove_runtime,
    runtime_digest,
    write_runtime,
)


@pytest.fixture()
def profile():
    """A real profile, generated the way the product generates them."""

    return ProfileGenerator().generate(
        "pixel-8-pro-android14",
        master_seed=new_master_seed(),
    )


def _other_profile():
    """A second profile, from a different preset and a different seed."""

    return ProfileGenerator().generate(
        "galaxy-s24-ultra-android14",
        master_seed=new_master_seed(),
    )


# --------------------------------------------------------------------------
# The builder
# --------------------------------------------------------------------------


def test_the_script_carries_the_profile_values(profile) -> None:
    """The payload holds the values the page is supposed to read."""

    script = build_script(profile)
    assert "window.__NOVA_PROFILE__=" in script

    payload = script.split("window.__NOVA_PROFILE__=", 1)[1].split(";\n", 1)[0]
    data = json.loads(payload)

    assert data["navigator"]["userAgent"] == profile.navigator.user_agent
    assert data["navigator"]["hardwareConcurrency"] == profile.navigator.hardware_concurrency
    assert data["screen"]["width"] == profile.screen.width
    assert data["screen"]["devicePixelRatio"] == profile.screen.device_pixel_ratio
    assert data["timezone"]["iana_name"] == profile.timezone.iana_name
    assert data["storage_network"]["quota_bytes"] == profile.storage_network.quota_bytes


def test_two_profiles_produce_two_different_scripts(profile) -> None:
    """The payload is per-unit, not per-preset."""

    builder = JSRuntimeBuilder()
    assert builder.build_hash(profile) != builder.build_hash(_other_profile())


def test_the_same_profile_produces_the_same_script(profile) -> None:
    """The script is deterministic -- a stable fingerprint."""

    builder = JSRuntimeBuilder()
    assert builder.build_hash(profile) == builder.build_hash(profile)


def test_the_runtime_is_the_same_for_every_profile(profile) -> None:
    """The runtime half never changes; only the payload does.

    This is what lets the runtime be hashed once and cached, and it is
    what makes a change to the runtime visible as a change to the digest.
    """

    builder = JSRuntimeBuilder()
    script = builder.build(profile)
    runtime = script.split(";\n", 1)[1]
    assert runtime_digest() == hashlib.sha256(runtime.encode()).hexdigest()


# --------------------------------------------------------------------------
# The anti-detection rules
# --------------------------------------------------------------------------


def test_the_runtime_hides_the_overrides(profile) -> None:
    """An overridden function must not leak its source.

    ``Function.prototype.toString`` on a native function returns
    ``"function () { [native code] }"``.  The runtime gives its overrides
    the same answer, so a page that reads the source of an overridden
    accessor sees the stock text.
    """

    script = build_script(profile)
    assert "[native code]" in script
    assert "Object.defineProperty(fn, 'toString'" in script


def test_the_runtime_installs_on_the_prototype(profile) -> None:
    """The overrides go on the prototype, not on the instance.

    An own property on the instance would show up in
    ``Object.getOwnPropertyNames(navigator)`` and would be a giveaway.
    """

    script = build_script(profile)
    assert "Navigator.prototype" in script
    assert "Screen.prototype" in script
    assert "defineProperty(window," in script


def test_the_runtime_stands_down_for_the_native_core(profile) -> None:
    """When the native core is present, the JavaScript layer does nothing.

    Two mechanisms reporting different values would be a contradiction the
    page could see, so only one of them may be active.
    """

    script = build_script(profile)
    assert "__NOVA_NATIVE__" in script
    assert "if (window.__NOVA_NATIVE__) { return; }" in script


def test_the_runtime_marks_itself_once(profile) -> None:
    """The runtime is idempotent -- a second injection does nothing."""

    script = build_script(profile)
    assert "if (window.__NOVA_RUNTIME__) { return; }" in script


def test_the_runtime_covers_every_axis(profile) -> None:
    """Every guarded axis has a surface in the runtime.

    The twelve axes are the ones the collision guard hashes.  A surface
    that is missing here is an axis the JavaScript fallback cannot hold,
    and it would silently fall back to the host's real values.
    """

    script = build_script(profile)
    required = {
        "identity": "Navigator.prototype",
        "client_hints": "userAgentData",
        "screen": "Screen.prototype",
        "webgl": "WebGLRenderingContext",
        "canvas": "CanvasRenderingContext2D",
        "fonts": "document.fonts",
        "rects": "getBoundingClientRect",
        "media_devices": "navigator.mediaDevices",
        "sensors": "DeviceMotionEvent",
        "storage": "navigator.storage",
        "webgpu": "navigator.gpu",
    }
    for axis, marker in required.items():
        assert marker in script, f"axis {axis} has no surface in the runtime"

    # The audio axis is the one that needs a different marker.
    assert "AudioContext" in script or "baseLatency" in script


def test_the_runtime_uses_the_profile_not_a_constant(profile) -> None:
    """The runtime reads the payload; it does not hardcode a value.

    A hardcoded value in the runtime would make every profile report the
    same thing -- which is the collision this project exists to prevent.
    """

    body = build_script(profile).split(";\n", 1)[1]
    assert "P.screen" in body
    assert "P.navigator" in body
    assert "P.storage_network" in body


# --------------------------------------------------------------------------
# The injector
# --------------------------------------------------------------------------


def test_the_injector_writes_the_script(tmp_path: Path, profile) -> None:
    """The payload lands in the user data directory."""

    written = write_runtime(profile, tmp_path)
    assert written.path == tmp_path / JS_RUNTIME_FILENAME
    assert written.path.exists()
    assert written.bytes_written > 0
    assert written.digest


def test_the_injector_is_idempotent(tmp_path: Path, profile) -> None:
    """Writing the same profile twice does not rewrite the file."""

    first = write_runtime(profile, tmp_path)
    stamp = first.path.stat().st_mtime_ns
    second = write_runtime(profile, tmp_path)
    assert second.path.stat().st_mtime_ns == stamp
    assert second.digest == first.digest


def test_the_injector_notices_a_different_profile(tmp_path: Path, profile) -> None:
    """A different profile replaces the payload on disk."""

    write_runtime(profile, tmp_path)
    written = write_runtime(_other_profile(), tmp_path)
    assert written.digest != write_runtime(profile, tmp_path).digest


def test_the_injector_removes_the_payload(tmp_path: Path, profile) -> None:
    """The payload does not survive the browser.

    A fingerprint left on disk in the clear is a liability: the next
    profile in the same directory would be able to read it.
    """

    write_runtime(profile, tmp_path)
    assert remove_runtime(tmp_path) is True
    assert not (tmp_path / JS_RUNTIME_FILENAME).exists()
    assert remove_runtime(tmp_path) is False


def test_the_injection_point_is_recorded(tmp_path: Path, profile) -> None:
    """The manifest records how the script was injected."""

    written = write_runtime(
        profile, tmp_path, injection_point=InjectionPoint.DOCUMENT_START
    )
    manifest = json.loads(
        (tmp_path / "nova_js_runtime.json").read_text(encoding="utf-8")
    )
    assert manifest["injection_point"] == "document_start"
    assert manifest["digest"] == written.digest
    assert manifest["profile_id"] == profile.profile_id
