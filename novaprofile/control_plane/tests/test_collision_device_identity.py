"""Tests for the collision guard's device-identity protection.

The guard exists to make "two profiles, two devices" an enforced property.
These tests check the parts of that property that are easy to get wrong:

* a profile that repeats another profile's device identity is rejected, not
  merely flagged,
* the guard changes the *device*, not just the rendering noise, when it
  resolves a collision,
* the device axis is checked before the cheaper rendering axes, because a
  repeated serial has to become a different device rather than a
  differently-drawn canvas.
"""

from __future__ import annotations

import pytest

from novaprofile.core.collision import (
    GUARDED_AXES,
    CollisionGuard,
    InMemorySignatureRegistry,
)
from novaprofile.core.generator import ProfileGenerator
from novaprofile.schema.enums import SignatureKind


@pytest.fixture()
def guard() -> CollisionGuard:
    return CollisionGuard(InMemorySignatureRegistry())


def _profile(seed: str, preset: str = "pixel-8-pro-android14"):
    return ProfileGenerator().generate(preset, master_seed=seed)


def test_the_device_identity_is_part_of_the_guarded_axes():
    """The device axis must be guarded, or repeated serials pass through."""

    assert SignatureKind.DEVICE in GUARDED_AXES


def test_a_repeated_device_identity_is_detected(guard):
    """Two profiles with the same serial are a collision."""

    first = _profile("a" * 64)
    second = _profile("a" * 64)

    guard.allocate_and_commit(first.identity.id, first)

    conflict = guard.check_collision(second, second.identity.id)
    assert conflict is not None
    kind, _signature, other = conflict
    assert kind is SignatureKind.DEVICE
    assert other == first.identity.id


def test_the_device_axis_is_checked_first():
    """A repeated serial must be resolved by changing the device.

    Nudging the rendering noise would leave the serial in place, so the
    profile would still look like the same physical unit to a site that
    reads it.
    """

    assert GUARDED_AXES[0] is SignatureKind.DEVICE


def test_resolving_a_collision_changes_the_device(guard):
    """After the guard resolves a collision, the device is a different one."""

    first = _profile("a" * 64)
    guard.allocate_and_commit(first.identity.id, first)

    second = _profile("a" * 64)
    resolved = guard.allocate(second.identity.id, second)

    assert resolved.android_build.serial != first.android_build.serial
    assert resolved.android_build.fingerprint != first.android_build.fingerprint
    assert resolved.android_build.bootloader != first.android_build.bootloader


def test_a_rolled_seed_produces_a_different_device(guard):
    """Rolling the seed changes the identity, not only the noise."""

    first = _profile("a" * 64)
    rolled = guard._rebuild(
        first,
        first.seed_config.model_copy(update={"seed_epoch": 1}),
        0,
        1,
    )

    assert rolled.android_build.serial != first.android_build.serial
    assert rolled.android_build.fingerprint != first.android_build.fingerprint


def test_the_guard_never_returns_the_same_device_twice(guard):
    """A duplicate is resolved by becoming another device."""

    first = _profile("a" * 64)
    guard.allocate_and_commit(first.identity.id, first)

    second = _profile("a" * 64)
    resolved = guard.allocate(second.identity.id, second)

    assert resolved.android_build.serial != first.android_build.serial
    assert guard.check_collision(resolved, second.identity.id) is None


def test_two_different_seeds_never_collide(guard):
    """Distinct seeds give distinct devices without any intervention."""

    profiles = [_profile(f"{i:064x}") for i in range(20)]
    for profile in profiles:
        guard.allocate_and_commit(profile.identity.id, profile)

    serials = {p.android_build.serial for p in profiles}
    assert len(serials) == len(profiles)


# ----------------------------------------------------------------------
# The independently readable surfaces
# ----------------------------------------------------------------------


def test_the_independently_readable_surfaces_are_guarded():
    """Every surface a site can read on its own must be a guarded axis.

    A vendor does not have to combine canvas, WebGL and the serial into
    one identifier.  It can read the motion sensors, the storage estimate,
    the WebGPU adapter or the font list and cluster on any of them alone,
    so each of those has to be an axis in its own right.
    """

    for kind in (
        SignatureKind.SENSORS,
        SignatureKind.STORAGE,
        SignatureKind.WEBGPU,
        SignatureKind.FONTS,
    ):
        assert kind in GUARDED_AXES, f"{kind.value} is readable on its own and must be guarded"


def test_the_navigator_is_not_a_guarded_axis():
    """The navigator surface is shared by design and must not be an axis.

    ``navigator.userAgent`` for Chromium 110 and later carries no build
    token, and the platform, the core count and the device memory are
    properties of the device *family*.  Two real phones of the same model
    running the same Chrome build report exactly the same navigator
    values, so treating that surface as an identity would demand a
    difference that no real device has -- and the guard would either
    reject the fleet or push it into an obviously synthetic corner.
    """

    assert SignatureKind.NAVIGATOR not in GUARDED_AXES


def test_each_guarded_axis_has_its_own_signature_material():
    """No two axes may hash the same values.

    Two axes over the same material would be two names for one check, and
    the fleet would be one axis weaker than it looks.
    """

    profile = _profile("b" * 64)
    parts = profile.signature_parts()

    materials: dict[str, str] = {}
    for kind in GUARDED_AXES:
        if kind is SignatureKind.FULL:
            continue
        values = parts[kind.value]
        assert values, f"{kind.value} has no signature material"
        material = "\x1f".join(values)
        assert material not in materials, (
            f"{kind.value} hashes the same material as {materials[material]}"
        )
        materials[material] = kind.value


def test_the_webgpu_axis_carries_a_per_device_salt():
    """The WebGPU adapter belongs to the GPU model, not to the unit.

    Two units that carry the same GPU really do report the same adapter
    and the same limit table.  The salt is what identifies the unit
    within the family, so the WebGPU surface cannot link two profiles
    that differ everywhere else.
    """

    profiles = [_profile(f"{i:064x}") for i in range(20)]
    salts = {p.webgpu.salt for p in profiles}
    assert len(salts) == len(profiles)
    assert all(len(salt) == 32 for salt in salts)


def test_the_sensors_axis_distinguishes_the_units():
    """Two units must not report the same motion signal."""

    profiles = [_profile(f"{i:064x}") for i in range(40)]
    readings = {
        (
            p.sensors.accelerometer.x,
            p.sensors.accelerometer.y,
            p.sensors.accelerometer.z,
            p.sensors.gyroscope.x,
            p.sensors.gyroscope.y,
            p.sensors.gyroscope.z,
        )
        for p in profiles
    }
    assert len(readings) == len(profiles)


# ----------------------------------------------------------------------
# The fallback path
# ----------------------------------------------------------------------


def test_the_fallback_rebuild_moves_every_guarded_axis(guard):
    """Without a preset the guard must still be able to resolve a clash.

    A preset can disappear from the catalogue between the moment a profile
    is generated and the moment the guard allocates it.  The fallback then
    re-derives the profile from the seed alone.  If that fallback only
    moved the rendering axes, a collision on the device axis would be
    unresolvable and the allocation would fail outright.
    """

    profile = _profile("c" * 64)
    # Drop the preset so that the guard cannot regenerate the profile.
    stripped = profile.model_copy(
        update={"seed_config": profile.seed_config.model_copy(update={"preset_id": ""})}
    )

    rebuilt = guard._rebuild_axes_only(
        stripped,
        profile.seed_config.model_copy(update={"seed_epoch": 1}),
    )

    assert rebuilt.android_build.serial != profile.android_build.serial
    assert rebuilt.android_build.version_incremental != profile.android_build.version_incremental
    assert rebuilt.canvas_2d.salt != profile.canvas_2d.salt
    assert rebuilt.sensors.motion_profile is not None
    assert rebuilt.webgpu.salt != profile.webgpu.salt
    assert rebuilt.fonts.salt != profile.fonts.salt


def test_the_fallback_rebuild_stays_internally_consistent(guard):
    """The re-derived build must still satisfy the consistency rules.

    The fingerprint carries the build id and the incremental number, so a
    fallback that changed one without the other would produce a profile
    that reports two different builds at once.
    """

    profile = _profile("d" * 64)
    stripped = profile.model_copy(
        update={"seed_config": profile.seed_config.model_copy(update={"preset_id": ""})}
    )

    rebuilt = guard._rebuild_axes_only(
        stripped,
        profile.seed_config.model_copy(update={"seed_epoch": 2}),
    )

    report = guard._resolver.check(rebuilt)
    assert not report.errors, [issue.message for issue in report.errors]
    assert rebuilt.android_build.fingerprint.count(":") == 3
