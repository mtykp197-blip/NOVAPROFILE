"""Tests for the hand-over from the profile to the browser core.

The browser core reads a JSON document that the control plane writes.
Two things can go wrong in that hand-over, and both of them are silent:
the browser starts, it looks like a browser, and it reports the host's
values.

**A value that is truncated.**  The core reads most numbers with a
32-bit integer reader, so a float where an integer is expected is a
type error and the coercion turns it into a whole number.  That is
right for the fields the core reads as integers and wrong for the ones
it reads as a double: a pixel ratio of 2.625 is a real value, and 2.625
turned into 2 is a different device.

**A path that does not resolve.**  The simulator derives the expected
values of an axis from the profile, and a path that names a field the
schema does not have reads as "the device does not know" -- so the
simulator falls back to a hard-coded constant, and the two sides of the
comparison disagree about a value that both of them hold correctly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.launcher.core_document import (
    FRACTIONAL_FIELDS,
    build_core_document,
    coerce_document,
)
from novaprofile.probe import simulator

#: The preset the checks read a profile from.  It has to be one the
#: catalogue holds, because the fields under test are the ones a real
#: handset produces.
PRESET = "pixel-8-pro-android14"


@pytest.fixture(scope="module")
def profile():
    """A profile of the catalogue."""

    return ProfileGenerator().generate(preset_id=PRESET, stratum=0)


@pytest.fixture(scope="module")
def document(profile):
    """The document the browser core reads."""

    return build_core_document(profile)


# --------------------------------------------------------------------------
# The fractional values
# --------------------------------------------------------------------------


def test_the_pixel_ratio_keeps_its_fraction(profile, document):
    """The pixel ratio is a double, and 2.625 is not 2.

    A handset whose panel is 420 dpi at a scale of 2.625 reports
    2.625.  Truncating it to 2 reports a panel the profile never
    described, and a site that reads the ratio and the density together
    sees the contradiction straight away.
    """

    assert profile.screen.device_pixel_ratio != int(profile.screen.device_pixel_ratio), (
        "the preset has to carry a fractional ratio for this test to mean anything"
    )
    assert document["screen"]["device_pixel_ratio"] == profile.screen.device_pixel_ratio


def test_the_fractional_fields_are_the_ones_the_core_reads_as_doubles():
    """The list of fractional fields has to match the core's readers.

    The core reads these with ``ReadDouble``; every other number it
    reads with ``ReadInt``.  A field that is on the wrong side of that
    line is either truncated for no reason or refused as a type error.
    """

    assert "device_pixel_ratio" in FRACTIONAL_FIELDS
    assert "visual_viewport_scale" in FRACTIONAL_FIELDS
    assert "downlink" in FRACTIONAL_FIELDS
    assert "base_latency" in FRACTIONAL_FIELDS
    assert "output_latency" in FRACTIONAL_FIELDS


def test_an_integer_field_is_still_truncated():
    """The truncation of the integer fields must not have been lost."""

    assert coerce_document({"width": 512.7}) == {"width": 512}


def test_the_coercion_keeps_the_fraction_of_a_fractional_field():
    """The coercion is what truncates, and it must not truncate these."""

    assert coerce_document({"device_pixel_ratio": 2.625}) == {"device_pixel_ratio": 2.625}
    assert coerce_document({"downlink": 10.5}) == {"downlink": 10.5}
    assert coerce_document({"inner_width": 512.5}) == {"inner_width": 512}


def test_the_document_carries_the_fractional_values_of_the_profile(document):
    """Every fractional field of the profile survives the hand-over."""

    downlink = document["storage_network"]["downlink"]

    assert isinstance(downlink, float)
    assert downlink != int(downlink), "the downlink was turned into a whole number"


# --------------------------------------------------------------------------
# The paths that do not resolve
# --------------------------------------------------------------------------


def _resolve(config: object, path: str) -> object:
    """Resolve a dotted path on the profile, or ``None``."""

    current: object = config
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _paths_in_simulator() -> set[str]:
    """The dotted paths the simulator reads off the profile."""

    source = Path(simulator.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'_getattr_path\(\s*config,\s*"([^"]+)"', source))


def test_every_path_the_simulator_uses_resolves(profile):
    """A path that does not resolve makes the simulator fall back.

    The fallback is a hard-coded constant, so the simulator ends up
    disagreeing with the browser about a value that both of them hold
    correctly -- and the disagreement looks like a broken hook.
    """

    broken = sorted(
        path for path in _paths_in_simulator() if _resolve(profile, path) is None
    )
    assert not broken, f"the simulator reads paths the profile does not have: {broken}"


def test_the_storage_expectations_come_from_the_profile(profile):
    """The quota, the rtt and the downlink have to be the profile's."""

    payload = simulator.simulate_payload(profile)
    axes = payload.get("axes", {})
    storage = axes.get("storage", {})

    quota = storage.get("storage", {}).get("quota")
    connection = storage.get("connection", {})

    assert quota == profile.storage_network.quota_bytes
    assert connection["rtt"] == profile.storage_network.rtt
    assert connection["downlink"] == profile.storage_network.downlink


def test_the_font_expectations_come_from_the_profile(profile):
    """The installed fonts have to be the profile's, not a default list."""

    payload = simulator.simulate_payload(profile)
    fonts = payload.get("axes", {}).get("fonts", {})

    assert sorted(fonts["installed"]) == sorted(profile.fonts.installed)
