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
    kind: Literal["audioinput"] = "audioinput"


class AudioOutputDevice(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: str
    group_id: str
    label: str
    kind: Literal["audiooutput"] = "audiooutput"


class VideoInputDevice(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: str
    group_id: str
    label: str
    kind: Literal["videoinput"] = "videoinput"
    facing_mode: Literal["environment", "user", "left", "right"] | None = None
    #: The resolution the camera reports through ``getCapabilities``.  A
    #: real camera does not advertise one size; it advertises a set of
    #: widths and heights it can be driven at, and the largest of them
    #: is what a script reads first.
    width: int = 1920
    height: int = 1080
    frame_rate: float = 30.0


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


# --------------------------------------------------------------------------
# 7.8 Floating-point precision
# --------------------------------------------------------------------------


class MathPrecision(BaseModel):
    """The floating-point behaviour of the transcendental functions.

    ``Math.acos(0.5)`` and its relatives are the classic way to tell a
    real browser from a JavaScript engine that fakes the DOM.  The result
    is not the mathematical one -- it is whatever the particular C
    library produces, and that differs between platforms and between
    architectures.

    The values here are the last few bits of the double that Chromium on
    x86-64 produces.  ``epsilon`` is how far the reported value may
    drift from the real one; the runtime mixes the profile salt into it
    so that two profiles never report the same drift.

    Setting ``enabled`` to ``False`` leaves the host's own ``Math``
    untouched, which is the right thing to do on a real Chromium build
    -- there is nothing to correct.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    #: The reference values, keyed by the function name.  These are the
    #: values at the canonical probe point ``0.5``, as x86-64 produces
    #: them -- the last bits differ from the correctly rounded result.
    acos: float = 1.0471975511965979
    asin: float = 0.5235987755982989
    sinh: float = 0.5210953054937474
    cosh: float = 1.1276259652063807
    tanh: float = 0.46211715726000974
    atan: float = 0.4636476090008061
    #: The largest absolute deviation the runtime may introduce.  The
    #: band is sixteen ulps wide: one ulp would hold only three
    #: distinct doubles, which is no entropy at all for a fleet.
    epsilon: float = Field(default=3.552713678800501e-15, ge=0.0, le=1e-8)
    #: The per-unit salt of the math surface.
    salt: str = ""


# --------------------------------------------------------------------------
# 7.9 Heap size limit
# --------------------------------------------------------------------------


class HeapSizeLimit(BaseModel):
    """``performance.memory.jsHeapSizeLimit``.

    Chromium reports the V8 heap ceiling in bytes, and the value is one
    of a small set of fixed numbers -- 2 GB on most desktop builds, a
    lower ceiling on memory-constrained ones.  ThumbmarkJS reads it
    under the ``hardware`` group and treats an implausible value as a
    signal.

    The value is also a strong hint about the device class: a phone that
    claims 12 GB of RAM should not report the same ceiling as a 2 GB
    one, so the consistency resolver pins it to the device memory.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    #: The heap ceiling in bytes.  2 GiB is the Chromium default.
    js_heap_size_limit: int = Field(default=2147483648, ge=268435456, le=8589934592)
    #: The heap ceiling the device's own memory implies.
    memory_tier_limit: int = Field(default=2147483648, ge=268435456, le=8589934592)
    #: The per-unit salt of the heap surface.
    salt: str = ""


# --------------------------------------------------------------------------
# 7.10 Media decode support
# --------------------------------------------------------------------------


class MediaDecode(BaseModel):
    """``MediaRecorder.isTypeSupported`` and its relatives.

    A browser claims to support a codec if it can actually decode it.
    The set of supported container/codec strings is a property of the
    platform's media stack, and it is what ThumbmarkJS probes with
    ``mediaDecode``.  A phone that claims to be an Android device but
    answers ``true`` for a codec its build cannot play gives itself away.

    ``supported`` holds the MIME strings the runtime will answer
    ``true`` for.  Everything else falls through to the host, so an
    unusual codec still reports whatever the real engine says.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    #: The MIME types the profile claims to support.
    supported: list[str] = Field(
        default_factory=lambda: [
            'video/mp4;codecs="avc1.42E01E"',
            'video/mp4;codecs="avc1.4D401E"',
            'video/mp4;codecs="avc1.640028"',
            'video/webm;codecs="vp8, vorbis"',
            'video/webm;codecs="vp9"',
            'audio/webm;codecs="opus"',
            'audio/mp4;codecs="mp4a.40.2"',
            'audio/ogg;codecs="vorbis"',
        ]
    )
    #: The per-unit salt of the media-decode surface.
    salt: str = ""


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
    "MathPrecision",
    "HeapSizeLimit",
    "MediaDecode",
]
