"""Tests for the hardware-surface consistency rules.

The canvas, the audio context, the geometry perturbation, the media
devices, the motion sensors, the storage estimate, the WebGPU adapter and
the font list are guarded axes.  A profile that is broken on any of them
is a profile a site can single out, so the resolver has to notice.

These tests cover the rules that were added with the guarded axes; the
rules for the device identity, the client hints and the WebRTC policy live
in their own modules.
"""

from __future__ import annotations

import pytest

from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.generator import ProfileGenerator


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


@pytest.fixture(scope="module")
def resolver() -> ConsistencyResolver:
    return ConsistencyResolver()


@pytest.fixture()
def profile(gen: ProfileGenerator):
    return gen.generate("pixel-8-pro-android14", master_seed="e" * 64)


def _axes(report) -> set[str]:
    return {issue.axis for issue in report.errors}


def test_a_generated_profile_has_no_hardware_surface_errors(resolver, profile) -> None:
    """The rules must accept what the generator produces."""

    report = resolver.check(profile)
    assert not report.errors, [issue.message for issue in report.errors]


def test_a_canvas_without_noise_is_refused(resolver, profile) -> None:
    """A canvas in noise mode that does not perturb is not a canvas."""

    broken = profile.model_copy(
        update={"canvas_2d": profile.canvas_2d.model_copy(update={"noise_strength": 0.0})}
    )
    report = resolver.check(broken)
    assert "canvas_2d" in _axes(report)


def test_an_audio_context_with_too_many_channels_is_refused(resolver, profile) -> None:
    """The channel count cannot exceed the supported maximum."""

    broken = profile.model_copy(
        update={
            "audio": profile.audio.model_copy(
                update={"channel_count": 8, "max_channel_count": 2}
            )
        }
    )
    report = resolver.check(broken)
    assert "audio" in _axes(report)


def test_an_audio_context_that_ignores_quantization_is_refused(resolver, profile) -> None:
    """A quantized context that ignores the quantization leaves the buckets."""

    broken = profile.model_copy(
        update={
            "audio": profile.audio.model_copy(
                update={"respect_core_quantization": False}
            )
        }
    )
    report = resolver.check(broken)
    assert "audio" in _axes(report)


def test_a_gravity_reading_outside_the_earth_is_refused(resolver, profile) -> None:
    """The gravity reading has to be a reading of Earth."""

    broken = profile.model_copy(
        update={
            "sensors": profile.sensors.model_copy(
                update={"gravity": profile.sensors.gravity.model_copy(update={"z": 3.5})}
            )
        }
    )
    report = resolver.check(broken)
    assert "sensors" in _axes(report)


def test_a_stationary_phone_must_stay_near_its_gravity_reading(resolver, profile) -> None:
    """A stationary accelerometer that disagrees with gravity is a warning."""

    broken = profile.model_copy(
        update={
            "sensors": profile.sensors.model_copy(
                update={
                    "accelerometer": profile.sensors.accelerometer.model_copy(update={"z": 0.0})
                }
            )
        }
    )
    report = resolver.check(broken)
    assert "sensors" in {issue.axis for issue in report.warnings}


def test_a_usage_above_the_quota_is_refused(resolver, profile) -> None:
    """The storage estimate cannot report more use than there is room for."""

    broken = profile.model_copy(
        update={
            "storage_network": profile.storage_network.model_copy(
                update={
                    "quota_bytes": 1_000,
                    "usage_bytes": 5_000,
                }
            )
        }
    )
    report = resolver.check(broken)
    assert "storage_network" in _axes(report)


def test_a_4g_cellular_connection_cannot_report_a_wired_download_speed(
    resolver, profile
) -> None:
    """A 4g *cellular* link never exceeds 10 Mbit/s.

    The ceiling belongs to the radio, not to the effective type: a wifi
    link also reports the effective type "4g" -- the Network Information
    API only has four buckets -- and it can carry far more than that.
    A cellular link cannot.
    """

    broken = profile.model_copy(
        update={
            "storage_network": profile.storage_network.model_copy(
                update={
                    "effective_type": "4g",
                    "connection_type": "cellular",
                    "downlink": 95.0,
                }
            )
        }
    )
    report = resolver.check(broken)
    assert "storage_network" in {issue.axis for issue in report.warnings}


def test_a_wifi_link_may_report_more_than_ten_megabits(resolver, profile) -> None:
    """A wifi link is not bound by the cellular ceiling."""

    broken = profile.model_copy(
        update={
            "storage_network": profile.storage_network.model_copy(
                update={
                    "effective_type": "4g",
                    "connection_type": "wifi",
                    "downlink": 95.0,
                }
            )
        }
    )
    report = resolver.check(broken)
    assert "storage_network" not in {issue.axis for issue in report.warnings}


def test_an_enabled_webgpu_adapter_without_limits_is_refused(resolver, profile) -> None:
    """No real GPU exposes an empty limit table."""

    broken = profile.model_copy(
        update={
            "webgpu": profile.webgpu.model_copy(
                update={"enabled": True, "limits": {}}
            )
        }
    )
    report = resolver.check(broken)
    assert "webgpu" in _axes(report)


def test_a_webgpu_surface_without_a_salt_is_refused(resolver, profile) -> None:
    """Without the salt every unit of a GPU model shares one adapter."""

    broken = profile.model_copy(
        update={"webgpu": profile.webgpu.model_copy(update={"salt": ""})}
    )
    report = resolver.check(broken)
    assert "webgpu" in _axes(report)


def test_a_font_list_without_a_salt_is_refused(resolver, profile) -> None:
    """Without the salt every unit of a locale shares one font set."""

    broken = profile.model_copy(update={"fonts": profile.fonts.model_copy(update={"salt": ""})})
    report = resolver.check(broken)
    assert "fonts" in _axes(report)


def test_a_device_without_fonts_is_a_warning(resolver, profile) -> None:
    """No Android device reports an empty font list."""

    broken = profile.model_copy(
        update={"fonts": profile.fonts.model_copy(update={"installed": []})}
    )
    report = resolver.check(broken)
    assert "fonts" in {issue.axis for issue in report.warnings}
