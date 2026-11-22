"""The aggregate profile model.

:class:`ProfileConfig` is the single payload handed to the browser core.  It
bundles every axis of the specification into one validated object, together
with the seed configuration that determines all of its derived values.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.seed import SeedDeriver, new_master_seed, new_uuid7
from .enums import ProfileStatus
from .hardware import (
    AndroidAbiSoc,
    AndroidBuild,
    Canvas2DConfig,
    MediaQueries,
    Orientation,
    Screen,
    Viewport,
    WebGLConfig,
    WebGPUConfig,
)
from .identity import (
    Geolocation,
    Locale,
    Navigator,
    Permissions,
    PluginsMime,
    Timezone,
    UserAgentData,
)
from .network import (
    Dns,
    GeoIntelligence,
    GeoSync,
    HttpHeaders,
    Proxy,
    ProxyRotation,
    WebRtc,
)
from .signals import (
    Audio,
    BatterySpeechGamepad,
    Fonts,
    HeapSizeLimit,
    MathPrecision,
    MediaDecode,
    MediaDevices,
    Rects,
    Sensors,
    StorageNetwork,
)
from .version import BrowserVersion

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SeedConfig(BaseModel):
    """The deterministic inputs of a profile.

    ``master_seed`` is the root of everything: every other value in the
    profile is either copied from the device preset or derived from this seed.
    """

    model_config = ConfigDict(frozen=True)

    master_seed: str = Field(min_length=64, max_length=64)
    seed_epoch: int = Field(default=0, ge=0)
    nudge_step: int = Field(default=0, ge=0)
    hkdf_version: str = SeedDeriver.HKDF_VERSION
    preset_id: str = ""
    core_version: str = "1.0.0"

    #: Which unit of the device family this profile is.
    #:
    #: The narrow readings -- the browser major, the core count, the
    #: memory -- are drawn *stratified*, so that two handsets of one
    #: model do not report the same ones.  The stratum is the unit's
    #: place in the family, and it has to travel with the seed: a
    #: profile that is regenerated from its seed must come back as the
    #: same handset, or its fingerprint changes every time the
    #: operator opens it.
    #:
    #: ``None`` means the profile stands alone and every choice is
    #: drawn independently.
    stratum: int | None = Field(default=None, ge=0)

    @field_validator("master_seed")
    @classmethod
    def _valid_seed(cls, v: str) -> str:
        if not _HEX64.match(v):
            raise ValueError("master_seed must be 64 hexadecimal characters")
        return v.lower()

    def deriver(self) -> SeedDeriver:
        """A :class:`SeedDeriver` bound to this configuration."""
        return SeedDeriver(self.master_seed, self.seed_epoch, self.nudge_step)

    def with_nudge(self, nudge_step: int) -> SeedConfig:
        """Copy with a different nudge step."""
        return self.model_copy(update={"nudge_step": nudge_step})

    def with_epoch(self, seed_epoch: int, master_seed: str | None = None) -> SeedConfig:
        """Copy with a different seed epoch (and optionally a fresh master seed)."""
        return self.model_copy(
            update={
                "seed_epoch": seed_epoch,
                "master_seed": (master_seed or self.master_seed).lower(),
                "nudge_step": 0,
            }
        )


class ProfileIdentity(BaseModel):
    """Who the profile is, in the control plane's terms."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    group_id: str | None = None
    folder_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    status: ProfileStatus = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_started_at: datetime | None = None
    last_closed_at: datetime | None = None
    total_runtime_ms: int = Field(default=0, ge=0)
    launch_count: int = Field(default=0, ge=0)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        # A profile id is normally a UUIDv7, but the control plane must be
        # able to address a profile by any stable opaque identifier (an
        # imported id, a legacy id, a test fixture).  Only the shape and the
        # length are constrained.
        if not v or len(v) > 128:
            raise ValueError("profile id must be a non-empty string of at most 128 chars")
        if any(ch in v for ch in " \t\r\n/\\"):
            raise ValueError("profile id must not contain whitespace or path separators")
        return v

    @classmethod
    def create(cls, name: str, *, profile_id: str | None = None, **kwargs) -> ProfileIdentity:
        """Build a fresh identity with a UUIDv7."""
        return cls(id=profile_id or new_uuid7(), name=name, **kwargs)


class ProfileConfig(BaseModel):
    """The complete, immutable profile payload.

    This is the object that is serialised into the browser core and that the
    consistency resolver validates.
    """

    model_config = ConfigDict(frozen=True)

    identity: ProfileIdentity
    seed_config: SeedConfig
    browser_version: BrowserVersion = Field(default_factory=lambda: BrowserVersion(major=120))

    # -- 1. android device ------------------------------------------------
    android_build: AndroidBuild
    android_abi_soc: AndroidAbiSoc

    # -- 3. navigator & client hints -------------------------------------
    navigator: Navigator
    user_agent_data: UserAgentData
    plugins_mime: PluginsMime
    permissions: Permissions

    # -- 4. screen & viewport --------------------------------------------
    screen: Screen
    orientation: Orientation
    viewport: Viewport
    media_queries: MediaQueries

    # -- 5. timezone, locale, geolocation --------------------------------
    timezone: Timezone
    locale: Locale
    geolocation: Geolocation

    # -- 6. canvas, webgl, webgpu ----------------------------------------
    canvas_2d: Canvas2DConfig
    webgl: WebGLConfig
    webgpu: WebGPUConfig

    # -- 7. signals -------------------------------------------------------
    audio: Audio
    rects: Rects
    fonts: Fonts
    media_devices: MediaDevices
    storage_network: StorageNetwork
    battery_speech_gamepad: BatterySpeechGamepad
    sensors: Sensors
    math_precision: MathPrecision = Field(default_factory=MathPrecision)
    heap_size_limit: HeapSizeLimit = Field(default_factory=HeapSizeLimit)
    media_decode: MediaDecode = Field(default_factory=MediaDecode)

    # -- 2. network -------------------------------------------------------
    proxy: Proxy
    proxy_rotation: ProxyRotation
    geo_intelligence: GeoIntelligence
    geo_sync: GeoSync
    webrtc: WebRtc
    dns: Dns
    http_headers: HttpHeaders

    @property
    def profile_id(self) -> str:
        return self.identity.id

    @property
    def master_seed(self) -> str:
        return self.seed_config.master_seed

    def deriver(self) -> SeedDeriver:
        return self.seed_config.deriver()

    def signature_parts(self) -> dict[str, list[str]]:
        """The per-axis signature inputs used by the collision guard.

        Each axis contributes a small tuple of the values that a bot vendor
        would sample.  The guard hashes these to detect collisions.

        A signature must only include values that actually *vary* between two
        profiles of the same device family, otherwise every profile built
        from one preset would share a signature and collide immediately.
        The static GPU parameter table is therefore combined with the
        profile's own salt instead of being used on its own.
        """
        return {
            "sig_canvas": [
                self.canvas_2d.salt,
                self.canvas_2d.mode,
                str(self.canvas_2d.noise_strength),
            ],
            "sig_webgl_draw": [
                self.webgl.unmasked_renderer,
                self.webgl.image_noise.value,
                # The per-unit salt of the draw surface.  The pixel-noise
                # value on its own carries only about 56 bits, which at
                # 100 million profiles collides with a probability of
                # roughly 6.7 percent; the salt is what makes this axis
                # able to hold the fleet.
                self.webgl.salt,
            ],
            "sig_webgl_params": [
                # The static GPU table identifies the *device family*; the
                # salt identifies the individual device within it.
                self.canvas_2d.salt,
                self.webgl.unmasked_vendor,
                self.webgl.unmasked_renderer,
                str(self.webgl.parameters.max_texture_size),
                str(self.webgl.parameters.max_samples),
                str(self.webgl.parameters.max_varying_vectors),
                self.webgl.version,
            ],
            "sig_rects": [
                self.rects.salt,
                str(self.rects.delta_x),
                str(self.rects.delta_y),
                str(self.rects.delta_width),
                str(self.rects.delta_height),
            ],
            "sig_devices": [
                self.media_devices.salt,
                self.media_devices.mode,
                str(len(self.media_devices.audio_inputs)),
                str(len(self.media_devices.video_inputs)),
            ],
            "sig_audio": [
                self.audio.salt,
                self.audio.mode,
                str(self.audio.sample_rate),
                str(self.audio.base_latency),
                str(self.audio.output_latency),
                str(self.audio.channel_count),
            ],
            "sig_device": [
                # The identity of the physical unit.  A site that reads the
                # serial or the build fingerprint sees this pair, so two
                # profiles must never share it.
                self.android_build.serial,
                self.android_build.fingerprint,
                self.android_build.bootloader,
                # The incremental build and the patch level are readable on
                # their own through ``Build.VERSION.INCREMENTAL`` and
                # ``Build.SECURITY_PATCH``; a site that reads only one of
                # them must still not be able to cluster two units.
                self.android_build.version_incremental,
                self.android_build.security_patch,
                str(self.screen.width),
                str(self.screen.height),
                str(self.screen.device_pixel_ratio),
            ],
            "sig_sensors": [
                # The motion sensors.  ``DeviceMotionEvent`` hands out the
                # raw accelerometer, gyroscope and gravity readings, so two
                # profiles that report the same signal are the same device
                # as far as a fingerprinting script is concerned -- even if
                # their canvas hashes differ.
                #
                # The salt comes first because it is what carries the
                # uniqueness: the gravity reading is about 9.81 on every
                # device on Earth and the jitter around it is a small
                # fraction, so the readings alone cannot tell a hundred
                # million units apart.
                self.sensors.salt,
                self.sensors.motion_profile,
                str(self.sensors.sampling_hz),
                str(self.sensors.accelerometer.x),
                str(self.sensors.accelerometer.y),
                str(self.sensors.accelerometer.z),
                str(self.sensors.gyroscope.x),
                str(self.sensors.gyroscope.y),
                str(self.sensors.gyroscope.z),
                str(self.sensors.gravity.z),
                str(self.sensors.relative_orientation.alpha),
                str(self.sensors.relative_orientation.beta),
                str(self.sensors.relative_orientation.gamma),
            ],
            "sig_storage": [
                # ``navigator.storage.estimate()`` and
                # ``navigator.connection`` are both readable on their own.
                #
                # The salt comes first because it is what carries the
                # uniqueness: the quota is one of a handful of round
                # numbers and the usage is a fraction of it, so the two
                # together carry barely a dozen bits.
                self.storage_network.salt,
                str(self.storage_network.quota_bytes),
                str(self.storage_network.usage_bytes),
                self.storage_network.effective_type,
                str(self.storage_network.rtt),
                str(self.storage_network.downlink),
                self.storage_network.connection_type,
            ],
            "sig_webgpu": [
                # ``navigator.gpu.requestAdapter()`` returns the adapter
                # description and its limits.  Those belong to the GPU
                # *model* and are shared by a whole family of units, which
                # is what real hardware does; the salt identifies the
                # individual unit within that family.
                self.webgpu.salt,
                self.webgpu.adapter.vendor,
                self.webgpu.adapter.architecture,
                self.webgpu.adapter.device,
                self.webgpu.adapter.description,
                str(sorted(self.webgpu.features)),
                str(sorted((k, v) for k, v in self.webgpu.limits.items())),
            ],
            "sig_fonts": [
                # The installed font list.  A site measures text with
                # ``CanvasRenderingContext2D.measureText`` and compares the
                # widths against a reference font, which reveals the font
                # configuration of the device.  The list is shuffled and
                # trimmed per unit, so it identifies the unit.
                self.fonts.salt,
                str(self.fonts.installed),
            ],
            "sig_math": [
                # The floating-point behaviour of the transcendental
                # functions.  ``Math.acos(0.5)`` hands out a double whose
                # last bits come from the C library of the platform, and
                # a site that compares the exact bit pattern against a
                # table of known engines can tell a real browser from a
                # faked DOM.
                #
                # The salt comes first because the drift itself is inside
                # a single ulp -- the values are all but identical, and
                # the salt is what tells two units apart.
                self.math_precision.salt,
                repr(self.math_precision.acos),
                repr(self.math_precision.asin),
                repr(self.math_precision.sinh),
                repr(self.math_precision.cosh),
                repr(self.math_precision.tanh),
                repr(self.math_precision.atan),
            ],
            "sig_heap": [
                # ``performance.memory.jsHeapSizeLimit`` is readable on
                # its own, and it follows the physical memory of the
                # machine.  Reporting the desktop default on a handset
                # is a signal.
                self.heap_size_limit.salt,
                str(self.heap_size_limit.js_heap_size_limit),
            ],
            "sig_media_decode": [
                # The media codecs the platform claims to decode.  A
                # device that claims a codec its build cannot play gives
                # itself away.
                self.media_decode.salt,
                str(sorted(self.media_decode.supported)),
            ],
        }

    def full_signature_parts(self) -> list[str]:
        """Every axis combined -- the composite signature.

        The per-axis salts are included so that two profiles of the same
        device family remain distinguishable even when every observable
        value outside the noise axes happens to match.
        """
        parts: list[str] = []
        for values in self.signature_parts().values():
            parts.extend(values)
        parts.extend(
            [
                self.android_build.fingerprint,
                self.navigator.user_agent,
                str(self.screen.width),
                str(self.screen.height),
                str(self.screen.device_pixel_ratio),
                self.timezone.iana_name,
                self.locale.primary,
                self.android_abi_soc.soc_model,
                str(self.browser_version.major),
                self.browser_version.full_version,
            ]
        )
        return parts


def make_seed_config(
    preset_id: str = "",
    master_seed: str | None = None,
    seed_epoch: int = 0,
    nudge_step: int = 0,
) -> SeedConfig:
    """Build a seed configuration, generating a fresh seed when needed."""
    return SeedConfig(
        master_seed=(master_seed or new_master_seed()).lower(),
        seed_epoch=seed_epoch,
        nudge_step=nudge_step,
        preset_id=preset_id,
    )


__all__ = [
    "SeedConfig",
    "ProfileIdentity",
    "ProfileConfig",
    "make_seed_config",
]
