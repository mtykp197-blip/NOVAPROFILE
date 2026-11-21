"""The contract between the control plane and the browser core.

The core reads ``nova_core.json``: the control plane writes it and the
native parser reads it.  The two are written in different languages and
they are not compiled together, so nothing but a test keeps them in step.

When the control plane adds a field that the native parser does not read,
the profile is silently incomplete: the surface keeps reporting the
host's value and every profile of the fleet collapses into one bucket.
When the native parser requires a field that the control plane does not
write, the core refuses to start.

These tests therefore check both directions against the real sources.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.launcher.launcher import BrowserLauncher
from novaprofile.schema.profile import ProfileConfig

#: The root of the NOVA project, which holds the native core patch.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = PROJECT_ROOT / "core_patch" / "src"


def _core_source(name: str) -> str:
    return (CORE_SRC / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile() -> ProfileConfig:
    return ProfileGenerator().generate("pixel-8-pro-android14", master_seed="f" * 64)


@pytest.fixture(scope="module")
def payload(profile: ProfileConfig) -> dict:
    return BrowserLauncher._config_payload(profile)


def _required_fields(function: str) -> set[str]:
    """The field names a native parser function reads.

    ``ReadString`` and friends fail the parse when the field is missing,
    so the control plane has to write every one of them.  Fields read
    with a default are optional: the core tolerates their absence, but
    when the control plane does write them the core has to consume them
    or the surface keeps reporting the host's value.
    """

    text = _core_source("nova_core_config.cc")
    # Isolate the function body.
    start = text.index(f"bool {function}(")
    end = text.find("\nbool ", start + 1)
    body = text[start:] if end == -1 else text[start:end]

    required: set[str] = set()
    optional: set[str] = set()
    for match in re.finditer(
        r"Read(?:String|Bool|Int|Double)\(\s*(?:dict|adapter->GetDict\(\))\s*,\s*\"(\w+)\"",
        body,
    ):
        required.add(match.group(1))
    # Some fields are read straight off the dictionary rather than through
    # a helper -- ``dict.Find("quota_bytes")`` and friends.
    for match in re.finditer(r"dict\.Find\(\s*\"(\w+)\"", body):
        required.add(match.group(1))
    # Optional fields are read with a default; the control plane may omit
    # them, but when it writes one the core has to consume it.
    for match in re.finditer(
        r"ReadStringOrDefault\(\s*dict\s*,\s*\"(\w+)\"", body
    ):
        optional.add(match.group(1))
    return required, optional


def _present(payload: dict, field: str) -> bool:
    """Whether the payload carries a non-empty value under ``field``."""

    node: object = payload
    for part in field.split("."):
        if not isinstance(node, dict):
            return False
        node = node.get(part)
        if node is None:
            return False
    return node not in (None, "", [], {})


@pytest.mark.parametrize(
    ("function", "field"),
    [
        ("ParseAndroidBuild", "android_build.serial"),
        ("ParseAndroidBuild", "android_build.fingerprint"),
        ("ParseAndroidBuild", "android_build.bootloader"),
        ("ParseSeed", "seed_config.master_seed"),
        ("ParseSeed", "seed_config.preset_id"),
        ("ParseCanvas", "canvas_2d.salt"),
        ("ParseWebGL", "webgl.unmasked_renderer"),
        ("ParseAudio", "audio.salt"),
        ("ParseRects", "rects.salt"),
        ("ParseFonts", "fonts.salt"),
        ("ParseMediaDevices", "media_devices.salt"),
        ("ParseWebGPU", "webgpu.salt"),
        ("ParseSensors", "sensors.motion_profile"),
        ("ParseSensors", "sensors.salt"),
        ("ParseStorageNetwork", "storage_network.quota_bytes"),
        ("ParseStorageNetwork", "storage_network.usage_bytes"),
        ("ParseStorageNetwork", "storage_network.salt"),
        ("ParseNavigator", "navigator.user_agent"),
        ("ParseUserAgentData", "user_agent_data.platform"),
        ("ParseScreen", "screen.width"),
        ("ParseScreen", "screen.height"),
        ("ParseWebRtc", "webrtc.mode"),
    ],
)
def test_the_core_reads_a_field_the_control_plane_writes(
    function: str, field: str, payload: dict
) -> None:
    """Every field the native parser insists on has to be in the payload.

    A missing field makes the core refuse to start, which is a loud
    failure -- but a failure that takes the whole fleet down.
    """

    required, optional = _required_fields(function)
    assert required or optional, f"{function} was not found in the native parser"
    leaf = field.split(".")[-1]
    assert leaf in required or leaf in optional, (
        f"{function} does not read {field}; the test needs to be updated"
    )
    assert _present(payload, field), f"the payload does not carry {field}"


def test_the_payload_carries_every_axis_the_guard_uses(payload: dict) -> None:
    """The payload has to carry the surfaces the guard guarantees.

    The guard's whole job is that no two profiles agree on a surface.  A
    surface the core does not receive keeps the host's value, and the
    guard's guarantee does not hold on it.
    """

    for field in (
        "android_build.serial",
        "android_build.fingerprint",
        "android_build.bootloader",
        "canvas_2d.salt",
        "audio.salt",
        "rects.salt",
        "fonts.salt",
        "media_devices.salt",
        "webgpu.salt",
        "sensors.salt",
        "sensors.gravity.z",
        "storage_network.salt",
        "storage_network.quota_bytes",
    ):
        assert _present(payload, field), f"the payload does not carry {field}"


def test_the_payload_is_json_serialisable(payload: dict) -> None:
    """The core reads the payload as JSON, so it has to be plain JSON."""

    text = json.dumps(payload, separators=(",", ":"))
    assert json.loads(text) == payload


def test_the_webgpu_salt_is_present_even_when_webgpu_is_disabled(payload: dict) -> None:
    """The salt is generated whether or not the adapter is enabled.

    A profile that has the surface switched off and later switched on
    would otherwise expose the adapter of every other profile that was
    generated with the same seed.
    """

    assert "salt" in payload["webgpu"]
    assert payload["webgpu"]["salt"]
