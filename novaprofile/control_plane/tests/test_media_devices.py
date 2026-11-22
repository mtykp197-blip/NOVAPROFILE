"""The media devices have to look like the ones Chromium reports.

A camera list is one of the surfaces a script checks, because the
shape of it is cheap to read and hard to fake convincingly.  Two things
give a script away immediately: an identifier that is not an opaque
hash, and a camera list that has the wrong number of entries.

The rules are taken from the Chromium tree this project patches.
``third_party/blink/common/mediastream/media_device_id.cc`` says the
identifier is exactly sixty-four characters of lower case hex, and
``media/audio/audio_device_description.cc`` names the two special
identifiers a browser also accepts.
"""

from __future__ import annotations

import re

import pytest

from novaprofile.core.generator import ProfileGenerator

#: The rule from ``IsValidMediaDeviceId``: sixty-four characters, and
#: every one of them a lower case letter or a digit.
DEVICE_ID_PATTERN = re.compile(r"^[0-9a-z]{64}$")

#: The devices a handset carries, by kind.
KINDS = ("audioinput", "audiooutput", "videoinput")


class TestDeviceIdentifiers:
    """The identifier is an opaque hash."""

    @pytest.mark.parametrize("preset_id", ["pixel-8-pro-android14", "redmi-note-12-android13"])
    def test_every_identifier_is_sixty_four_hex_characters(self, preset_id: str) -> None:
        generator = ProfileGenerator()
        config = generator.generate(preset_id, master_seed="a" * 64)

        devices = (
            config.media_devices.audio_inputs
            + config.media_devices.audio_outputs
            + config.media_devices.video_inputs
        )
        for device in devices:
            assert len(device.device_id) == 64, (
                f"{device.device_id!r} is {len(device.device_id)} characters, "
                "not the sixty-four Chromium produces"
            )
            assert DEVICE_ID_PATTERN.match(device.device_id), (
                f"{device.device_id!r} is not lower case hex"
            )

    def test_the_identifier_is_not_readable(self) -> None:
        """The old identifiers were names; the new ones are hashes."""
        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="b" * 64)

        for device in config.media_devices.video_inputs:
            assert "camera" not in device.device_id.lower()
            assert "_" not in device.device_id

    def test_the_group_identifier_is_a_hash_too(self) -> None:
        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="c" * 64)

        for device in config.media_devices.video_inputs:
            assert DEVICE_ID_PATTERN.match(device.group_id)

    def test_two_profiles_never_share_an_identifier(self) -> None:
        """The salt is what keeps one device from becoming another's twin."""
        generator = ProfileGenerator()
        first = generator.generate("pixel-8-pro-android14", master_seed="d" * 64)
        second = generator.generate("pixel-8-pro-android14", master_seed="e" * 64)

        first_ids = {
            device.device_id
            for device in first.media_devices.video_inputs
        }
        second_ids = {
            device.device_id
            for device in second.media_devices.video_inputs
        }

        assert not (first_ids & second_ids)

    def test_the_same_profile_always_reports_the_same_identifier(self) -> None:
        """A device that changes its identifier between two visits is two
        devices, and that is exactly what a script looks for."""
        generator = ProfileGenerator()
        first = generator.generate("pixel-8-pro-android14", master_seed="f" * 64)
        second = generator.generate("pixel-8-pro-android14", master_seed="f" * 64)

        assert [d.device_id for d in first.media_devices.video_inputs] == [
            d.device_id for d in second.media_devices.video_inputs
        ]


class TestTheCameraList:
    """The list has the shape a real handset has."""

    def test_the_two_cameras_are_in_two_different_groups(self) -> None:
        """The front camera and the back camera are two sensors.

        They sit in separate groups, because a group is a physical
        unit and a handset has two of them.
        """
        generator = ProfileGenerator()
        for index in range(12):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            videos = config.media_devices.video_inputs
            if len(videos) < 2:
                continue
            groups = {device.group_id for device in videos}
            assert len(groups) == len(videos), (
                "two cameras share a group identifier"
            )

    def test_the_microphone_and_the_speaker_share_a_group(self) -> None:
        """They are one unit of hardware, so one group."""
        generator = ProfileGenerator()
        for index in range(12):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            audio_in = config.media_devices.audio_inputs
            audio_out = config.media_devices.audio_outputs
            if not audio_in or not audio_out:
                continue
            assert audio_in[0].group_id == audio_out[0].group_id

    def test_a_handset_has_at_most_two_cameras(self) -> None:
        generator = ProfileGenerator()
        for index in range(20):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            assert len(config.media_devices.video_inputs) <= 2

    def test_the_label_names_the_facing_direction(self) -> None:
        """Android names the camera and the direction it looks."""
        generator = ProfileGenerator()
        for index in range(20):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            for device in config.media_devices.video_inputs:
                assert "facing" in device.label, (
                    f"the label {device.label!r} does not name the direction"
                )

    def test_the_rear_camera_has_the_higher_resolution(self) -> None:
        """The rear camera of a handset is the one with the better sensor."""
        generator = ProfileGenerator()
        for index in range(20):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            videos = config.media_devices.video_inputs
            if len(videos) < 2:
                continue
            rear = [d for d in videos if d.facing_mode == "environment"]
            front = [d for d in videos if d.facing_mode != "environment"]
            if not rear or not front:
                continue
            assert rear[0].width * rear[0].height > front[0].width * front[0].height, (
                "the rear camera does not have the larger sensor"
            )

    def test_every_device_declares_its_kind(self) -> None:
        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="1" * 64)

        assert all(d.kind == "audioinput" for d in config.media_devices.audio_inputs)
        assert all(d.kind == "audiooutput" for d in config.media_devices.audio_outputs)
        assert all(d.kind == "videoinput" for d in config.media_devices.video_inputs)


class TestTheKindsAreRealistic:
    """The population of the fleet matches the population of the world."""

    def test_almost_every_handset_has_a_camera(self) -> None:
        generator = ProfileGenerator()
        with_camera = 0
        total = 200
        for index in range(total):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            if config.media_devices.video_inputs:
                with_camera += 1

        assert with_camera / total > 0.85

    def test_almost_every_handset_has_a_microphone(self) -> None:
        generator = ProfileGenerator()
        with_microphone = 0
        total = 200
        for index in range(total):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            if config.media_devices.audio_inputs:
                with_microphone += 1

        assert with_microphone / total > 0.90

    def test_most_handsets_have_two_cameras(self) -> None:
        """The single-camera handset is the cheap one, and it is rare."""
        generator = ProfileGenerator()
        counts = [0, 0]
        total = 200
        for index in range(total):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            number = len(config.media_devices.video_inputs)
            if number >= 2:
                counts[1] += 1
            else:
                counts[0] += 1

        assert counts[1] / total > 0.75
