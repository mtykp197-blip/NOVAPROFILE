"""Hardware-facing schema groups: android build, ABI/SoC, screen, WebGL/WebGPU.

These models describe the *physical device* an Android profile impersonates.
The consistency resolver (see :mod:`novaprofile.core.consistency`) treats them as
one coupled unit: changing the SoC may invalidate the GPU renderer string, the
screen density, the UA and the client hints simultaneously.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    AndroidRelease,
    AndroidSdkInt,
    CanvasMode,
    CanvasTarget,
    ColorGamut,
    ColorScheme,
    ContrastPref,
    DynamicRange,
    ForcedColors,
    HoverCapability,
    OrientationAngle,
    OrientationType,
    PointerCapability,
    ReducedMotion,
    WebGlMode,
    WebGlVendor,
)

# --------------------------------------------------------------------------
# 1.1 Android build metadata
# --------------------------------------------------------------------------


class AndroidBuild(BaseModel):
    """Mirrors the fields Android exposes via ``android.os.Build``."""

    model_config = ConfigDict(frozen=True)

    version_release: AndroidRelease
    sdk_int: AndroidSdkInt
    security_patch: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    build_id: str
    display: str
    version_incremental: str
    model: str
    device: str
    product: str
    manufacturer: str
    brand: str
    hardware: str
    board: str
    bootloader: str
    fingerprint: str
    host: str
    user: str
    tags: str = "release-keys"
    type: str = "user"
    serial: str

    @field_validator("fingerprint")
    @classmethod
    def _fingerprint_shape(cls, v: str) -> str:
        # A real fingerprint has four colon-separated segments:
        #   brand/product/device : release/build_id.incremental : id : user/tags
        #   google/husky/husky:14/UQ1A.240105.004.10750268:11016:user/release-keys
        parts = v.split(":")
        if len(parts) < 4 or "/" not in parts[0]:
            raise ValueError(f"fingerprint {v!r} is not a valid Android build fingerprint")
        if not parts[1]:
            raise ValueError(f"fingerprint {v!r} is missing the build id segment")
        if not parts[2].isdigit():
            raise ValueError(
                f"fingerprint {v!r} must carry a numeric build id in the third segment"
            )
        if "/" not in parts[3]:
            raise ValueError(f"fingerprint {v!r} must end with a user/tags segment")
        return v


class AndroidAbiSoc(BaseModel):
    """CPU/ABI/SoC description.

    ``primary_abi`` must appear in ``supported_abis`` and the 64/32 bit lists
    must partition ``supported_abis`` exactly -- checked by the resolver.
    """

    model_config = ConfigDict(frozen=True)

    supported_abis: list[Literal["arm64-v8a", "armeabi-v7a", "armeabi", "x86_64", "x86"]]
    supported_64_bit_abis: list[Literal["arm64-v8a", "x86_64"]]
    supported_32_bit_abis: list[Literal["armeabi-v7a", "armeabi", "x86"]]
    primary_abi: str
    is_64_bit: bool
    soc_vendor: Literal["ARM", "Qualcomm", "MediaTek", "Samsung", "Google", "Unisoc", "HiSilicon"]
    soc_model: str
    cpu_cluster_layout: str
    gpu_architecture: str


# --------------------------------------------------------------------------
# 4. Screen / viewport / media queries
# --------------------------------------------------------------------------


class Screen(BaseModel):
    model_config = ConfigDict(frozen=True)

    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=320, le=4096)
    avail_width: int = Field(ge=0)
    avail_height: int = Field(ge=0)
    avail_left: int = Field(ge=0)
    avail_top: int = Field(ge=0)
    color_depth: Literal[24, 30]
    pixel_depth: Literal[24, 30, 48]
    # The device pixel ratios Android quantises to.  The platform rounds
    # the raw quotient of the pixel density by 160 to the nearest of
    # these, so a site reads one of them and never the arithmetic
    # result.  The list has to carry every bucket the fleet's real
    # panels land on: a device that reports a ratio outside the list
    # either cannot be modelled or has to be rounded to a wrong one.
    #
    # The 3.25 entry is the 520 dpi bucket.  It is the ratio of the
    # high-density panels that ship in the current flagship generation,
    # and the fleet carries devices with them.
    device_pixel_ratio: Literal[
        1.0,
        1.5,
        1.75,
        2.0,
        2.25,
        2.5,
        2.625,
        2.75,
        3.0,
        3.25,
        3.5,
        4.0,
    ]
    # The density buckets a panel reports.  Each entry is a real panel
    # density: the list has to cover the panels the fleet actually
    # ships, because a device whose density is not on the list either
    # cannot be modelled or has to be rounded to a wrong one.
    #
    # The buckets below the 160 line are the tablet densities.  A
    # tablet reports the same bucket vocabulary as a phone -- the
    # platform has one table, not two -- and the fleet carries tablets
    # alongside the handsets.
    density_dpi: Literal[
        120,
        160,
        213,
        240,
        280,
        320,
        360,
        400,
        411,
        420,
        440,
        480,
        520,
        525,
        545,
        560,
        640,
    ]

    @field_validator("avail_width", "avail_height")
    @classmethod
    def _avail_le_screen(cls, v: int, info) -> int:
        # ``info.data`` holds the fields declared before this one.
        limit = info.data.get("width" if info.field_name == "avail_width" else "height")
        if limit is not None and v > limit:
            raise ValueError(f"avail dimension {v} exceeds screen dimension {limit}")
        return v


class Orientation(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: OrientationType
    angle: OrientationAngle
    lock_supported: bool


class SafeAreaInsets(BaseModel):
    model_config = ConfigDict(frozen=True)

    top: int = Field(ge=0)
    right: int = Field(ge=0)
    bottom: int = Field(ge=0)
    left: int = Field(ge=0)


class Viewport(BaseModel):
    model_config = ConfigDict(frozen=True)

    inner_width: int = Field(ge=0)
    inner_height: int = Field(ge=0)
    outer_width: int = Field(ge=0)
    outer_height: int = Field(ge=0)
    screen_x: int = Field(ge=0)
    screen_y: int = Field(ge=0)
    visual_viewport_scale: Annotated[float, Field(gt=0)]
    safe_area_insets: SafeAreaInsets
    scrollbar_width: int = Field(ge=0)


class MediaQueries(BaseModel):
    model_config = ConfigDict(frozen=True)

    prefers_color_scheme: ColorScheme
    prefers_reduced_motion: ReducedMotion = "no-preference"
    prefers_contrast: ContrastPref = "no-preference"
    forced_colors: ForcedColors = "none"
    pointer: PointerCapability = "coarse"
    any_pointer: list[PointerCapability] = Field(default_factory=lambda: ["coarse"])
    hover: HoverCapability = "none"
    any_hover: list[HoverCapability] = Field(default_factory=lambda: ["none"])
    color_gamut: ColorGamut = "srgb"
    dynamic_range: DynamicRange = "standard"


# --------------------------------------------------------------------------
# 6. Canvas / WebGL / WebGPU
# --------------------------------------------------------------------------


class Canvas2DConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: CanvasMode = "noise"
    salt: str = Field(min_length=8, max_length=128)
    noise_strength: float = Field(default=0.0, ge=0.0, le=8.0)
    persist_across_restarts: bool = True
    targets: list[CanvasTarget] = Field(
        default_factory=lambda: ["toDataURL", "toBlob", "getImageData"]
    )


class ShaderPrecisionRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    range_min: int
    range_max: int
    precision: int


class ShaderPrecision(BaseModel):
    """``getShaderPrecisionFormat`` table.

    Chromium/ANGLE on Android GPUs expose the same three tiers for both
    stages; the resolver keeps the ranges physically consistent.
    """

    model_config = ConfigDict(frozen=True)

    vertex_shader: dict[str, ShaderPrecisionRange]
    fragment_shader: dict[str, ShaderPrecisionRange]


class WebGLImageNoise(BaseModel):
    """Deterministic pixel-level noise applied at the Skia/ANGLE readback.

    ``value`` is a 17-significant-digit decimal so that no rounding happens
    when the value is round-tripped through a C++ ``double`` and a JSON
    string.  It is derived from ``master_seed + unmasked_renderer +
    nudge_step`` -- see :class:`novaprofile.core.seed.SeedDeriver`.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    value: str = Field(min_length=17, max_length=24)

    @field_validator("value")
    @classmethod
    def _numeric(cls, v: str) -> str:
        try:
            float(v)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"webgl image noise value {v!r} is not numeric") from exc
        return v


class WebGLExtensions(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: list[str] = Field(default_factory=list)


class WebGLParameters(BaseModel):
    """``gl.getParameter`` surface for the impersonated GPU.

    Every value is a plain number/list so the C++ core can inject it directly
    into ANGLE's parameter table.
    """

    model_config = ConfigDict(frozen=True)

    max_texture_size: int = Field(ge=2048)
    max_cube_map_texture_size: int = Field(ge=512)
    max_3d_texture_size: int = Field(ge=256)
    max_array_texture_layers: int = Field(ge=256)
    max_renderbuffer_size: int = Field(ge=2048)
    max_viewport_dims: list[int]
    max_vertex_attribs: int = Field(ge=8)
    max_vertex_uniform_vectors: int = Field(ge=128)
    max_vertex_uniform_components: int = Field(ge=512)
    max_fragment_uniform_vectors: int = Field(ge=64)
    max_fragment_uniform_components: int = Field(ge=256)
    max_varying_vectors: int = Field(ge=15)
    max_combined_texture_image_units: int = Field(ge=32)
    max_vertex_texture_image_units: int = Field(ge=16)
    max_texture_image_units: int = Field(ge=16)
    max_color_attachments: int = Field(ge=4)
    max_draw_buffers: int = Field(ge=4)
    max_samples: int = Field(ge=0)
    max_uniform_buffer_bindings: int = Field(ge=24)
    aliased_line_width_range: list[float]
    aliased_point_size_range: list[float]
    sample_buffers: int = Field(ge=0)
    samples: int = Field(ge=0)
    max_texture_max_anisotropy_ext: int = Field(ge=1)


class WebGLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: WebGlMode = "noise"
    unmasked_vendor: WebGlVendor
    unmasked_renderer: str
    vendor: str
    renderer: str
    version: str
    shading_language_version: str
    image_noise: WebGLImageNoise
    #: The per-unit salt of the WebGL draw surface.
    #:
    #: The pixel-noise value alone carries about 56 bits, which at 100
    #: million profiles collides with a probability of about 6.7 percent.
    #: The salt is what makes the draw surface an axis that can actually
    #: hold the fleet: it is 128 bits wide and is what the native core
    #: keys the readback on, so two units never draw the same pixels.
    salt: str = ""
    read_pixels_sync: bool = True
    extensions: WebGLExtensions
    parameters: WebGLParameters
    shader_precision: ShaderPrecision


class WebGPUAdapter(BaseModel):
    model_config = ConfigDict(frozen=True)

    vendor: str
    architecture: str
    device: str
    description: str
    is_fallback_adapter: bool = False


class WebGPUConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    adapter: WebGPUAdapter
    features: list[str] = Field(default_factory=list)
    limits: dict[str, float] = Field(default_factory=dict)
    #: A per-device salt.  The adapter description and the limit table are
    #: properties of the *GPU model*, so they are shared by every unit that
    #: carries that GPU -- which is correct, because that is what real
    #: hardware does.  The salt is what identifies the individual unit
    #: within the family, so the WebGPU surface never links two profiles
    #: that differ in every other respect.
    salt: str = ""


__all__ = [
    "AndroidBuild",
    "AndroidAbiSoc",
    "Screen",
    "Orientation",
    "SafeAreaInsets",
    "Viewport",
    "MediaQueries",
    "Canvas2DConfig",
    "ShaderPrecisionRange",
    "ShaderPrecision",
    "WebGLImageNoise",
    "WebGLExtensions",
    "WebGLParameters",
    "WebGLConfig",
    "WebGPUAdapter",
    "WebGPUConfig",
]
