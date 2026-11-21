"""Tests for the floor on the canvas noise strength.

A canvas that is not perturbed at all belongs to the real device; a canvas
that is perturbed by a hair can still render the same bytes on two
different devices.  At the scale this system targets, the weakest tail of
the noise distribution decides how large the clusters of indistinguishable
profiles become, so the strength has a floor.
"""

from __future__ import annotations

import pytest

from novaprofile.core.generator import CANVAS_NOISE_FLOOR, ProfileGenerator


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def test_no_profile_carries_an_unperturbed_canvas(gen):
    """A strength of zero would leave the real canvas in place."""

    for index in range(200):
        profile = gen.generate("pixel-8-pro-android14", master_seed=f"{index:064x}")
        assert profile.canvas_2d.noise_strength > 0


def test_no_profile_falls_below_the_floor(gen):
    """The floor holds across the whole distribution."""

    for index in range(500):
        profile = gen.generate("pixel-8-pro-android14", master_seed=f"{index:064x}")
        assert profile.canvas_2d.noise_strength >= CANVAS_NOISE_FLOOR


def test_the_strength_is_still_spread_out(gen):
    """The floor must not collapse the distribution into one value."""

    values = {
        gen.generate("pixel-8-pro-android14", master_seed=f"{index:064x}").canvas_2d.noise_strength
        for index in range(200)
    }
    assert len(values) > 50


def test_the_strength_stays_within_the_valid_range(gen):
    """The strength never leaves the 0..1 interval."""

    for index in range(200):
        profile = gen.generate("pixel-8-pro-android14", master_seed=f"{index:064x}")
        assert 0.0 <= profile.canvas_2d.noise_strength <= 1.0


def test_the_floor_is_also_respected_after_a_nudge(gen):
    """A nudged profile keeps the floor; the guard must not weaken it."""

    from novaprofile.core.collision import CollisionGuard, InMemorySignatureRegistry

    guard = CollisionGuard(InMemorySignatureRegistry())
    profile = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)

    for nudge in range(3):
        profile = guard._rebuild(profile, profile.seed_config, nudge, 0)
        assert profile.canvas_2d.noise_strength >= CANVAS_NOISE_FLOOR
