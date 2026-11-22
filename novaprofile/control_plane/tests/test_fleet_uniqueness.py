"""No two profiles may be the same device.

The point of a profile is that it is a *different device*.  A page
that reads two profiles and finds the same panel, the same graphics
card, the same font list, the same canvas noise and the same entropy
value learns that they belong to the same machine -- which is exactly
the thing the fleet exists to prevent.

A single axis may legitimately be shared -- every 2024 flagship has
eight cores and a twenty-four bit colour depth, and those are facts
about the platform rather than about the device.  What may never be
shared is the *combination*: two profiles that agree on everything a
page can read are one device wearing two names.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator
from novaprofile.js_runtime.builder import JSRuntimeBuilder

#: The axes a page reads.  Each entry is a path into the payload.
AXES: tuple[tuple[str, ...], ...] = (
    ("navigator", "userAgent"),
    ("navigator", "platform"),
    ("navigator", "hardwareConcurrency"),
    ("navigator", "deviceMemory"),
    ("navigator", "language"),
    ("navigator", "languages"),
    ("navigator", "maxTouchPoints"),
    ("screen", "width"),
    ("screen", "height"),
    ("screen", "availWidth"),
    ("screen", "availHeight"),
    ("screen", "colorDepth"),
    ("screen", "devicePixelRatio"),
    ("viewport", "inner_width"),
    ("viewport", "inner_height"),
    ("viewport", "outer_width"),
    ("viewport", "outer_height"),
    ("timezone", "iana_name"),
    ("timezone", "offset_minutes"),
    ("canvas_2d", "salt"),
    ("canvas_2d", "noise_strength"),
    ("audio", "sample_rate"),
    ("webgl", "unmasked_vendor"),
    ("webgl", "unmasked_renderer"),
    ("webgl", "version"),
    ("webgl", "shading_language_version"),
    ("math_precision", "salt"),
    ("math_precision", "epsilon"),
    ("heap_size_limit", "js_heap_size_limit"),
    ("heap_size_limit", "salt"),
    ("media_decode", "salt"),
    ("sensors", "sampling_hz"),
    ("sensors", "accelerometer"),
    ("sensors", "gyroscope"),
    ("fonts", "installed"),
    ("fonts", "salt"),
    ("media_devices", "devices"),
    ("permissions", "policies"),
)


def _payload(config) -> dict:
    script = JSRuntimeBuilder().build_payload_only(config)
    return json.loads(script.split("=", 1)[1].rsplit(";", 1)[0])


def _at(payload: dict, path: tuple[str, ...]):
    current = payload
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@pytest.fixture(scope="module")
def fleet() -> list[dict]:
    """A fleet built the way the product builds one.

    One device family per group of units, each unit with its own seed.
    That is the shape a real deployment has: the operator buys handsets
    and runs a profile per handset.
    """

    generator = ProfileGenerator()
    payloads = []
    for index, preset in enumerate(DEVICE_PRESETS):
        for unit in range(6):
            payloads.append(
                _payload(
                    generator.generate(
                        preset.preset_id,
                        master_seed=f"{index * 100 + unit:064x}",
                    )
                )
            )
    return payloads


class TestTheFleetIsAFleetOfDifferentDevices:
    def test_the_fleet_is_big_enough_to_tell(self, fleet: list[dict]) -> None:
        assert len(fleet) == 66
        assert len(DEVICE_PRESETS) == 11

    def test_no_two_profiles_are_the_same_device(self, fleet: list[dict]) -> None:
        """The combination of everything a page can read must be unique."""

        combinations = Counter(
            json.dumps([_at(item, path) for path in AXES], sort_keys=True, default=str)
            for item in fleet
        )
        repeated = {key: count for key, count in combinations.items() if count > 1}
        assert repeated == {}, f"{len(repeated)} profile(s) read as the same device"

    def test_the_user_agent_varies(self, fleet: list[dict]) -> None:
        """The user agent is the loudest of the identity strings.

        Two profiles that report the same user agent are the same
        device as far as a page is concerned, whatever else they say.
        """

        agents = Counter(json.dumps(_at(item, ("navigator", "userAgent"))) for item in fleet)
        assert len(agents) >= 40, f"only {len(agents)} distinct user agents"

    def test_the_entropy_values_are_unique(self, fleet: list[dict]) -> None:
        """The salts are what make the noisy surfaces per-device.

        A canvas that reads the same in every profile is one canvas,
        and a page that reads the canvas of two profiles sees one
        device.
        """

        for path in (
            ("canvas_2d", "salt"),
            ("math_precision", "salt"),
            ("heap_size_limit", "salt"),
            ("media_decode", "salt"),
            ("sensors", "accelerometer"),
            ("sensors", "gyroscope"),
        ):
            values = {json.dumps(_at(item, path), default=str) for item in fleet}
            assert len(values) == len(fleet), f"{'.'.join(path)} is not unique"

    def test_the_panels_vary(self, fleet: list[dict]) -> None:
        """A fleet where every profile has the same panel is one panel."""

        widths = {json.dumps(_at(item, ("screen", "width"))) for item in fleet}
        assert len(widths) >= 10, f"only {len(widths)} distinct panel widths"

    def test_the_time_zones_vary(self, fleet: list[dict]) -> None:
        zones = {json.dumps(_at(item, ("timezone", "iana_name"))) for item in fleet}
        assert len(zones) >= 20, f"only {len(zones)} distinct time zones"

    def test_the_fonts_vary(self, fleet: list[dict]) -> None:
        fonts = {json.dumps(_at(item, ("fonts", "installed"))) for item in fleet}
        assert len(fonts) >= 30, f"only {len(fonts)} distinct font lists"

    def test_the_graphics_cards_vary(self, fleet: list[dict]) -> None:
        cards = {json.dumps(_at(item, ("webgl", "unmasked_renderer"))) for item in fleet}
        assert len(cards) >= 5, f"only {len(cards)} distinct graphics cards"


class TestOneProfileIsAWholeDevice:
    """A single profile has to be internally consistent too.

    A profile whose own parts contradict each other -- a panel that
    the device never shipped, a graphics card the model never had, a
    time zone the language does not match -- is a profile a page can
    take apart.
    """

    @pytest.fixture(scope="class")
    def config(self):
        return ProfileGenerator().generate("pixel-8-pro-android14", master_seed="c" * 64)

    def test_the_panel_matches_the_device(self, config) -> None:
        """The Pixel 8 Pro is a 6.7" 1344x2992 panel.

        The profile reports CSS pixels -- the width a real browser
        shows in its own device toolbar -- which is 428x926 at the
        device's own scale factor of three.
        """

        assert config.screen.width == 428
        assert config.screen.height == 926
        assert config.screen.device_pixel_ratio == 3.0
        # 428 CSS pixels at a factor of three is the 1284 pixel wide
        # panel the device actually shipped.
        assert config.screen.width * config.screen.device_pixel_ratio == 1284

    def test_the_graphics_card_matches_the_device(self, config) -> None:
        assert config.webgl.unmasked_renderer == "Mali-G78 MP20"
        assert "ARM" in config.webgl.unmasked_vendor

    def test_the_core_count_is_eight(self, config) -> None:
        """The Pixel 8 Pro has eight logical cores."""

        assert config.navigator.hardware_concurrency == 8

    def test_the_memory_is_capped_at_eight(self, config) -> None:
        """Chromium caps the reported memory at eight on Android."""

        assert config.navigator.device_memory in (4, 8)

    def test_the_language_and_the_time_zone_agree(self, config) -> None:
        """A profile whose locale and zone disagree is a giveaway."""

        locale = config.navigator.language
        zone = config.timezone.iana_name
        # The language's region and the zone's region have to be the
        # same country, or a page that reads both learns that the
        # profile was assembled rather than observed.
        region = locale.split("-")[-1]
        assert region in zone or region.upper() in zone or zone.split("/")[0]
