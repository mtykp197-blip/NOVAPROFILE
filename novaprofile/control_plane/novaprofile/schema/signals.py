"""Signal-level schema groups: audio, rects, fonts, media devices, storage,
battery/speech/gamepad, sensors.

Group 7 of the specification.  These are the axes most anti-bot vendors
(FingerprintJS, CreepJS, DataDome) sample, so the models are deliberately
explicit rather than collapsed into a generic "noise" blob.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    AudioMode,
    ConnectionType,
    EffectiveType,
    FontsMode,
    MediaDeviceIdScope,
    MediaDevicesMode,
    MotionProfile,
    RectsMode,
    RectsTarget,
)

# --------------------------------------------------------------------------
# 7.1 Audio
# --------------------------------------------------------------------------


class Audio(BaseModel):
    """``AudioContext`` impersonation.

    ``respect_core_quantization`` keeps the value inside the 7 buckets that
    Chromium's real Android implementation produces; those buckets are the
    fingerprints anti-bot vendors check.
    """

    model_config = ConfigDict(frozen=True)

    mode: AudioMode = "native_quantized"
    salt: str = Field(min_length=8, max_length=128)
    sample_rate: Literal[48000, 44100] = 48000
    base_latency: float = Field(ge=0.0)
    output_latency: float = Field(ge=0.0)
    channel_count: int = Field(ge=1, le=8)
    max_channel_count: int = Field(ge=1, le=8)
    respect_core_quantization: bool = True

    @field_validator("max_channel_count")
    @classmethod
    def _max_ge_channels(cls, v: int, info) -> int:
        channels = info.data.get("channel_count")
        if channels is not None and v < channels:
            raise ValueError("max_channel_count must be >= channel_count")
        return v


# --------------------------------------------------------------------------
# 7.2 Client rects
# --------------------------------------------------------------------------


class Rects(BaseModel):
    """Sub-pixel geometry perturbation.

    Deltas are applied as integer pixel offsets in the compositor so that
    ``getBoundingClientRect`` stays consistent with layout and hit-testing.
    """

    model_config = ConfigDict(frozen=True)

    mode: RectsMode = "noise"
    salt: str = Field(min_length=8, max_length=128)
    delta_x: int = Field(ge=-4, le=4)
    delta_y: int = Field(ge=-4, le=4)
    delta_width: int = Field(ge=-4, le=4)
    delta_height: int = Field(ge=-4, le=4)
    targets: list[RectsTarget] = Field(
        default_factory=lambda: [
            "getBoundingClientRect",
            "getClientRects",
            "Range.getBoundingClientRect",
            "Range.getClientRects",
        ]
    )


# --------------------------------------------------------------------------
# 7.3 Fonts
# --------------------------------------------------------------------------


class Fonts(BaseModel):
    """Installed font list.

    ``preserve_metric_clusters`` keeps fonts that share a metric cluster
    together, which is what real Android font configuration does -- a random
    subset of a metric cluster is itself an anomaly signal.
    """

    model_config = ConfigDict(frozen=True)

    mode: FontsMode = "android_preset"
    installed: list[str] = Field(default_factory=list)
    salt: str = Field(min_length=8, max_length=128)
    preserve_metric_clusters: bool = True

    @field_validator("installed")
    @classmethod
    def _dedup(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for name in v:
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out


# --------------------------------------------------------------------------
# 7.4 Media devices
# --------------------------------------------------------------------------


class AudioInputDevice(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: str
    group_id: str
    label: str


class AudioOutputDevice(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: str
    group_id: str
    label: str


class VideoInputDevice(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: str
    group_id: str
    label: str
    facing_mode: Literal["environment", "user", "left", "right"] | None = None


class MediaDevices(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: MediaDevicesMode = "deterministic_mock"
    id_scope: MediaDeviceIdScope = "per_profile_and_origin"
    salt: str = Field(min_length=8, max_length=128)
    audio_inputs: list[AudioInputDevice] = Field(default_factory=list)
    audio_outputs: list[AudioOutputDevice] = Field(default_factory=list)
    video_inputs: list[VideoInputDevice] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 7.5 Storage & network information API
# --------------------------------------------------------------------------


class StorageNetwork(BaseModel):
    model_config = ConfigDict(frozen=True)

    quota_bytes: int = Field(ge=0)
    usage_bytes: int = Field(ge=0)
    persisted_default: bool = False
    effective_type: EffectiveType = "4g"
    rtt: int = Field(ge=0)
    downlink: float = Field(ge=0.0)
    save_data: bool = False
    connection_type: ConnectionType = "wifi"
    #: The per-unit salt of the storage surface.
    #:
    #: The quota is a handful of round numbers -- every browser reports
    #: one of a few fixed sizes -- and the usage is a fraction of it, so
    #: the pair carries barely a dozen bits.  A hundred million profiles
    #: would collide on it inevitably.  The salt is what makes this
    #: surface able to hold the fleet; the native core mixes it into the
    #: quota and the usage it hands out.
    salt: str = ""

    @field_validator("usage_bytes")
    @classmethod
    def _usage_le_quota(cls, v: int, info) -> int:
        quota = info.data.get("quota_bytes")
        if quota is not None and v > quota:
            raise ValueError("usage_bytes must not exceed quota_bytes")
        return v


# --------------------------------------------------------------------------
# 7.6 Battery / speech / gamepad
# --------------------------------------------------------------------------


class Battery(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    charging: bool = False
    level: float = Field(ge=0.0, le=1.0)
    charging_time: int = Field(ge=0)
    discharging_time: int = Field(ge=0)

    @field_validator("level")
    @classmethod
    def _level_quantum(cls, v: float) -> float:
        # The Battery Status API reports a fixed 0.01 quantum.
        return round(v, 2)


class SpeechVoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    lang: str
    default: bool = False
    local_service: bool = True
    voice_uri: str = ""


class GamepadDeviceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    index: int = Field(ge=0)
    connected: bool = True
    mapping: str = "standard"
    axes: list[float] = Field(default_factory=list)
    buttons: list[int] = Field(default_factory=list)


class Gamepad(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    devices: list[GamepadDeviceInfo] = Field(default_factory=list)


class BatterySpeechGamepad(BaseModel):
    model_config = ConfigDict(frozen=True)

    battery: Battery
    speech_voices: list[SpeechVoice] = Field(default_factory=list)
    gamepad: Gamepad


# --------------------------------------------------------------------------
# 7.7 Sensors
# --------------------------------------------------------------------------


class Vector3D(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    z: float


class AbsoluteOrientation(BaseModel):
    model_config = ConfigDict(frozen=True)

    alpha: float = Field(ge=0.0, le=360.0)
    beta: float = Field(ge=-180.0, le=180.0)
    gamma: float = Field(ge=-90.0, le=90.0)


class RelativeOrientation(BaseModel):
    model_config = ConfigDict(frozen=True)

    alpha: float = Field(ge=0.0, le=360.0)
    beta: float = Field(ge=-180.0, le=180.0)
    gamma: float = Field(ge=-90.0, le=90.0)


class Sensors(BaseModel):
    """Mobile motion sensors.

    ``stationary_micro_jitter`` is the default because it is what a phone
    sitting on a desk reports; ``walking_subtle`` produces a larger but still
    low-frequency signal.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    sampling_hz: Literal[60, 100, 200] = 60
    motion_profile: MotionProfile = "stationary_micro_jitter"
    accelerometer: Vector3D = Field(default_factory=lambda: Vector3D(x=0.0, y=0.0, z=9.81))
    gyroscope: Vector3D = Field(default_factory=lambda: Vector3D(x=0.0, y=0.0, z=0.0))
    linear_acceleration: Vector3D = Field(default_factory=lambda: Vector3D(x=0.0, y=0.0, z=0.0))
    gravity: Vector3D = Field(default_factory=lambda: Vector3D(x=0.0, y=0.0, z=9.81))
    #: The per-unit salt of the motion surface.
    #:
    #: The gravity reading on its own is the same for every device on
    #: Earth -- about 9.81 -- and the jitter around it is a small
    #: fractional value, so the readings carry far too little entropy to
    #: tell a hundred million units apart.  The salt is what makes the
    #: motion surface an axis that can hold the fleet; the native core
    #: mixes it into every reading it hands out.
    salt: str = ""
    absolute_orientation: AbsoluteOrientation = Field(
        default_factory=lambda: AbsoluteOrientation(alpha=0.0, beta=0.0, gamma=0.0)
    )
    relative_orientation: RelativeOrientation = Field(
        default_factory=lambda: RelativeOrientation(alpha=0.0, beta=0.0, gamma=0.0)
    )


__all__ = [
    "Audio",
    "Rects",
    "Fonts",
    "AudioInputDevice",
    "AudioOutputDevice",
    "VideoInputDevice",
    "MediaDevices",
    "StorageNetwork",
    "Battery",
    "SpeechVoice",
    "GamepadDeviceInfo",
    "Gamepad",
    "BatterySpeechGamepad",
    "Vector3D",
    "AbsoluteOrientation",
    "RelativeOrientation",
    "Sensors",
]
