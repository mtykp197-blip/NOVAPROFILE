"""Tests for the deterministic seed engine."""

from __future__ import annotations

import pytest

from novaprofile.core.seed import (
    DOMAIN_SALTS,
    Axis,
    SeedDeriver,
    SeedError,
    hkdf,
    hkdf_expand,
    hkdf_extract,
    new_master_seed,
    new_uuid7,
)

SEED_A = "a" * 64
SEED_B = "b" * 64


class TestHkdf:
    def test_extract_is_deterministic(self) -> None:
        assert hkdf_extract(b"salt", bytes.fromhex(SEED_A)) == hkdf_extract(
            b"salt", bytes.fromhex(SEED_A)
        )

    def test_extract_differs_by_salt(self) -> None:
        ikm = bytes.fromhex(SEED_A)
        assert hkdf_extract(b"salt1", ikm) != hkdf_extract(b"salt2", ikm)

    def test_expand_length(self) -> None:
        prk = hkdf_extract(b"salt", bytes.fromhex(SEED_A))
        assert len(hkdf_expand(prk, b"info", 100)) == 100
        assert len(hkdf_expand(prk, b"info", 0)) == 0

    def test_expand_differs_by_info(self) -> None:
        prk = hkdf_extract(b"salt", bytes.fromhex(SEED_A))
        assert hkdf_expand(prk, b"info1", 32) != hkdf_expand(prk, b"info2", 32)

    def test_full_hkdf_matches_expand_of_extract(self) -> None:
        ikm = bytes.fromhex(SEED_A)
        prk = hkdf_extract(b"salt", ikm)
        assert hkdf(b"salt", ikm, b"info", 32) == hkdf_expand(prk, b"info", 32)


class TestSeedDeriver:
    def test_rejects_bad_seed(self) -> None:
        with pytest.raises(SeedError):
            SeedDeriver("short")
        with pytest.raises(SeedError):
            SeedDeriver("z" * 64)
        with pytest.raises(SeedError):
            SeedDeriver("a" * 63)

    def test_rejects_negative_epoch(self) -> None:
        with pytest.raises(SeedError):
            SeedDeriver(SEED_A, seed_epoch=-1)

    def test_every_axis_has_a_unique_salt(self) -> None:
        """Reusing a salt would couple two axes together."""
        salts = list(DOMAIN_SALTS.values())
        assert len(salts) == len(set(salts))
        assert len(salts) == len(Axis)

    def test_axis_keys_are_independent(self) -> None:
        deriver = SeedDeriver(SEED_A)
        keys = {axis: deriver.axis_key(axis) for axis in Axis}
        assert len(set(keys.values())) == len(Axis)

    def test_axis_key_is_stable_across_instances(self) -> None:
        assert SeedDeriver(SEED_A).axis_key(Axis.CANVAS_2D) == SeedDeriver(SEED_A).axis_key(
            Axis.CANVAS_2D
        )

    def test_different_seeds_give_different_values(self) -> None:
        a = SeedDeriver(SEED_A).derive_bytes(Axis.CANVAS_2D, 32)
        b = SeedDeriver(SEED_B).derive_bytes(Axis.CANVAS_2D, 32)
        assert a != b

    def test_derive_int_is_in_range(self) -> None:
        deriver = SeedDeriver(SEED_A)
        for _ in range(200):
            value = deriver.derive_int(Axis.NAVIGATOR, 4, 12)
            assert 4 <= value <= 12

    def test_derive_int_is_roughly_uniform(self) -> None:
        deriver = SeedDeriver(SEED_A)
        counts = {n: 0 for n in range(4, 13)}
        for _ in range(4000):
            counts[deriver.derive_int(Axis.NAVIGATOR, 4, 12)] += 1
        # Every bucket should get a meaningful share.
        assert all(count > 200 for count in counts.values())

    def test_derive_float_in_range(self) -> None:
        deriver = SeedDeriver(SEED_A)
        for _ in range(100):
            value = deriver.derive_float(Axis.AUDIO, 0.0, 1.0)
            assert 0.0 <= value < 1.0

    def test_derive_choice(self) -> None:
        deriver = SeedDeriver(SEED_A)
        options = ("x", "y", "z")
        assert deriver.derive_choice(Axis.NAVIGATOR, options) in options

    def test_derive_weighted_choice_respects_weights(self) -> None:
        deriver = SeedDeriver(SEED_A)
        counts = {"common": 0, "rare": 0}
        for _ in range(1000):
            result = deriver.derive_weighted_choice(
                Axis.NAVIGATOR, [("common", 0.9), ("rare", 0.1)]
            )
            counts[result] += 1
        assert counts["common"] > counts["rare"] * 3

    def test_derive_shuffled_is_a_permutation(self) -> None:
        deriver = SeedDeriver(SEED_A)
        items = list(range(20))
        shuffled = deriver.derive_shuffled(Axis.FONTS, items)
        assert sorted(shuffled) == items

    def test_epoch_changes_every_axis(self) -> None:
        a = SeedDeriver(SEED_A, seed_epoch=0)
        b = SeedDeriver(SEED_A, seed_epoch=1)
        for axis in Axis:
            assert a.derive_bytes(axis, 32) != b.derive_bytes(axis, 32)

    def test_nudge_changes_every_axis(self) -> None:
        a = SeedDeriver(SEED_A, nudge_step=0)
        b = SeedDeriver(SEED_A, nudge_step=1)
        for axis in Axis:
            assert a.derive_bytes(axis, 32) != b.derive_bytes(axis, 32)


class TestWebGlImageValue:
    """The anti cross-GPU-collision axis."""

    def test_requires_a_renderer(self) -> None:
        with pytest.raises(SeedError):
            SeedDeriver(SEED_A).derive_webgl_image_value("")

    def test_has_at_least_17_significant_digits(self) -> None:
        deriver = SeedDeriver(SEED_A)
        value = deriver.derive_webgl_image_value("Mali-G715 MC10")
        digits = len(value.replace("-", "").replace(".", "").lstrip("0").split("e")[0])
        assert digits >= 17, f"{value} has only {digits} significant digits"

    def test_is_deterministic(self) -> None:
        a = SeedDeriver(SEED_A).derive_webgl_image_value("Mali-G715 MC10")
        b = SeedDeriver(SEED_A).derive_webgl_image_value("Mali-G715 MC10")
        assert a == b

    def test_different_renderer_gives_different_value(self) -> None:
        """Two GPU models must never share an image hash."""
        deriver = SeedDeriver(SEED_A)
        mali = deriver.derive_webgl_image_value("Mali-G715 MC10")
        adreno = deriver.derive_webgl_image_value("Adreno (TM) 740")
        assert mali != adreno

    def test_renderer_binding_holds_across_many_pairs(self) -> None:
        deriver = SeedDeriver(SEED_A)
        renderers = [
            "Mali-G715 MC10",
            "Mali-G57 MC2",
            "Mali-G68 MC4",
            "Adreno (TM) 740",
            "Adreno (TM) 730",
            "Adreno (TM) 660",
            "Adreno (TM) 642",
            "Mali-G78 MP20",
        ]
        values = {r: deriver.derive_webgl_image_value(r) for r in renderers}
        assert len(set(values.values())) == len(renderers)

    def test_nudge_shifts_the_value(self) -> None:
        """The nudge axis of the collision guard must move this value."""
        a = SeedDeriver(SEED_A, nudge_step=0).derive_webgl_image_value("Mali-G715 MC10")
        b = SeedDeriver(SEED_A, nudge_step=1).derive_webgl_image_value("Mali-G715 MC10")
        assert a != b

    def test_nudge_shifts_deep_digits_only(self) -> None:
        """A nudge moves the deep decimal digits, not the leading ones."""
        a = SeedDeriver(SEED_A, nudge_step=0).derive_webgl_image_value("Mali-G715 MC10")
        b = SeedDeriver(SEED_A, nudge_step=1).derive_webgl_image_value("Mali-G715 MC10")
        assert a[0] == b[0]
        assert a != b

    def test_different_seeds_give_different_values(self) -> None:
        a = SeedDeriver(SEED_A).derive_webgl_image_value("Mali-G715 MC10")
        b = SeedDeriver(SEED_B).derive_webgl_image_value("Mali-G715 MC10")
        assert a != b

    def test_round_trips_through_a_double(self) -> None:
        """The value must survive the trip through C++ double and JSON."""
        deriver = SeedDeriver(SEED_A)
        value = deriver.derive_webgl_image_value("Mali-G715 MC10")
        assert float(value) == float(repr(float(value)))


class TestHelpers:
    def test_new_master_seed_is_64_hex_chars(self) -> None:
        seed = new_master_seed()
        assert len(seed) == 64
        int(seed, 16)

    def test_new_master_seed_is_random(self) -> None:
        assert new_master_seed() != new_master_seed()

    def test_new_uuid7_shape(self) -> None:
        import uuid

        value = new_uuid7()
        parsed = uuid.UUID(value)
        assert str(parsed) == value
        assert parsed.version == 7

    def test_new_uuid7_is_time_ordered(self) -> None:
        first = uuid_sort_key(new_uuid7())
        second = uuid_sort_key(new_uuid7())
        assert first <= second


class TestFingerprintStability:
    """A profile's fingerprint must not change between runs.

    These tests guard the specific failure mode where a field is derived from
    ``hash()``: CPython randomises the string hash per process, so such a
    field silently changes on every restart and the device stops looking like
    the same physical unit.
    """

    def test_device_serial_is_stable_across_processes(self) -> None:
        """The serial must not come from the randomised ``hash()``.

        The value is recomputed here with the hash seed disabled, which is
        what a fresh interpreter would produce with ``PYTHONHASHSEED=0``.
        """
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent(
            """
            from novaprofile.core.devices import get_preset
            print(get_preset("pixel-8-pro-android14").serial)
            """
        )
        runs = []
        for seed in ("0", "1", "12345"):
            env = {
                "PYTHONHASHSEED": seed,
                "PATH": "",
                "PYTHONPATH": ".",
            }
            import os

            env["PATH"] = os.environ.get("PATH", "")
            env["PYTHONPATH"] = os.getcwd()
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            runs.append(result.stdout.strip())
        assert len(set(runs)) == 1, f"serial is not stable: {runs}"

    def test_generated_profile_is_byte_identical_on_regeneration(self) -> None:
        """Regenerating from the stored seed must reproduce the fingerprint.

        The identity metadata (the UUID, the timestamps, the display name) is
        management data and is expected to differ between two generations;
        everything that a vendor can observe must not.
        """
        from novaprofile.core.generator import ProfileGenerator

        seed = "c" * 64
        generator = ProfileGenerator()
        first = generator.generate(
            "pixel-8-pro-android14",
            master_seed=seed,
            seed_epoch=0,
            nudge_step=0,
        )
        second = generator.generate(
            "pixel-8-pro-android14",
            master_seed=seed,
            seed_epoch=0,
            nudge_step=0,
        )
        assert first.full_signature_parts() == second.full_signature_parts()
        assert first.android_build.model_dump() == second.android_build.model_dump()
        assert first.navigator.model_dump() == second.navigator.model_dump()
        assert first.screen.model_dump() == second.screen.model_dump()
        assert first.canvas_2d.model_dump() == second.canvas_2d.model_dump()
        assert first.webgl.model_dump() == second.webgl.model_dump()
        assert first.audio.model_dump() == second.audio.model_dump()
        assert first.rects.model_dump() == second.rects.model_dump()
        assert first.fonts.model_dump() == second.fonts.model_dump()

    def test_serial_is_stable_across_regeneration(self) -> None:
        """The Android build serial must survive a regeneration."""
        from novaprofile.core.generator import ProfileGenerator

        generator = ProfileGenerator()
        first = generator.generate(
            "pixel-8-pro-android14",
            master_seed="d" * 64,
            seed_epoch=0,
            nudge_step=0,
        )
        second = generator.generate(
            "pixel-8-pro-android14",
            master_seed="d" * 64,
            seed_epoch=0,
            nudge_step=0,
        )
        assert first.android_build.serial == second.android_build.serial

    def test_serial_differs_between_presets(self) -> None:
        """Two different device families must not share a serial."""
        from novaprofile.core.devices import list_presets

        serials = [preset.serial for preset in list_presets()]
        assert len(set(serials)) == len(serials)


def uuid_sort_key(value: str) -> int:
    """The timestamp prefix of a UUIDv7, for ordering checks."""
    return int(value.replace("-", "")[:12], 16)
