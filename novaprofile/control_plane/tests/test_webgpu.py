"""Tests for the WebGPU axis.

WebGPU is a newer fingerprint surface than WebGL, and an anti-bot vendor
that reads it looks at three things: whether the adapter exists, which
features it reports and what its limit table holds.

An adapter with an empty feature list and an empty limit table is not
something a real GPU produces.  Every WebGPU implementation exposes a
limit table, so an empty one is a strong signal that the browser was
patched rather than a device.
"""

from __future__ import annotations

import pytest

from novaprofile.core.generator import ProfileGenerator


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _enabled_profiles(gen: ProfileGenerator, count: int = 40):
    return [
        profile
        for profile in (
            gen.generate("pixel-8-pro-android14", master_seed=f"{i:064x}") for i in range(count)
        )
        if profile.webgpu.enabled
    ]


def test_some_profiles_have_webgpu_enabled(gen):
    """WebGPU is present on a minority of the devices, as in the wild."""

    profiles = _enabled_profiles(gen)
    assert profiles, "no profile with WebGPU was generated"
    assert len(profiles) < 40


def test_a_disabled_webgpu_reports_no_limits(gen):
    """A device without WebGPU exposes nothing."""

    for index in range(40):
        profile = gen.generate("pixel-8-pro-android14", master_seed=f"{index:064x}")
        if not profile.webgpu.enabled:
            assert profile.webgpu.limits == {}
            assert profile.webgpu.features == []


def test_an_enabled_adapter_always_has_limits(gen):
    """Every enabled adapter carries a limit table."""

    for profile in _enabled_profiles(gen):
        assert profile.webgpu.limits, "an enabled WebGPU adapter has no limits"
        assert "maxTextureDimension2D" in profile.webgpu.limits
        assert "maxComputeWorkgroupSizeX" in profile.webgpu.limits
        assert "maxBufferSize" in profile.webgpu.limits


def test_an_enabled_adapter_always_has_features(gen):
    """Every enabled adapter carries a feature list."""

    for profile in _enabled_profiles(gen):
        assert profile.webgpu.features, "an enabled WebGPU adapter has no features"
        assert "texture-compression-astc" in profile.webgpu.features


def test_the_limits_follow_the_gpu_of_the_device(gen):
    """The limit table agrees with the WebGL limits of the same device."""

    for profile in _enabled_profiles(gen):
        assert profile.webgpu.limits["maxTextureDimension2D"] == float(
            profile.webgl.parameters.max_texture_size
        )
        assert profile.webgpu.limits["maxColorAttachments"] == float(
            profile.webgl.parameters.max_color_attachments
        )


def test_the_adapter_agrees_with_the_webgl_renderer(gen):
    """The adapter names the same GPU that the WebGL context reports."""

    for profile in _enabled_profiles(gen):
        assert profile.webgpu.adapter.vendor == profile.webgl.unmasked_vendor
        assert profile.webgpu.adapter.description == profile.webgl.unmasked_renderer


def test_the_limits_are_plausible(gen):
    """The numbers are inside the range a mobile GPU reports."""

    for profile in _enabled_profiles(gen):
        limits = profile.webgpu.limits
        assert 2048 <= limits["maxTextureDimension2D"] <= 16384
        assert 0 < limits["maxComputeInvocationsPerWorkgroup"] <= 1024
        assert limits["minUniformBufferOffsetAlignment"] >= 32
        assert limits["maxUniformBufferBindingSize"] >= 65536
