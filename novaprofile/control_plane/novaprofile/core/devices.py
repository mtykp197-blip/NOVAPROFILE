"""Catalog of real Android devices.

Every entry couples the pieces of a device that must agree with each other:
the SoC determines the GPU renderer string, the screen resolution and density
determine the viewport and the device pixel ratio, and the OS release
determines the SDK level and the WebView/Chromium version.

The consistency resolver validates a generated profile against this catalog,
so an entry that is not physically accurate becomes a bug rather than a
silent fingerprint anomaly.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema.hardware import AndroidAbiSoc, AndroidBuild, Screen

# --------------------------------------------------------------------------
# GPU families
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GpuProfile:
    """A GPU and the vendor/renderer strings Chromium reports for it."""

    unmasked_vendor: str
    unmasked_renderer: str
    vendor: str
    renderer: str
    version: str
    shading_language_version: str = "WebGL GLSL ES 3.00 (OpenGL ES 3.0 Chromium)"
    max_texture_size: int = 16384
    max_cube_map_texture_size: int = 16384
    max_3d_texture_size: int = 2048
    max_array_texture_layers: int = 2048
    max_renderbuffer_size: int = 16384
    max_viewport_dims: tuple[int, int] = (16384, 16384)
    max_vertex_attribs: int = 16
    max_vertex_uniform_vectors: int = 256
    max_vertex_uniform_components: int = 1024
    max_fragment_uniform_vectors: int = 224
    max_fragment_uniform_components: int = 896
    max_varying_vectors: int = 15
    max_combined_texture_image_units: int = 32
    max_vertex_texture_image_units: int = 16
    max_texture_image_units: int = 16
    max_color_attachments: int = 4
    max_draw_buffers: int = 4
    max_samples: int = 4
    max_uniform_buffer_bindings: int = 24
    aliased_line_width_range: tuple[float, float] = (1.0, 1.0)
    aliased_point_size_range: tuple[float, float] = (1.0, 1024.0)
    sample_buffers: int = 0
    samples: int = 0
    max_texture_max_anisotropy_ext: int = 16


MALI_G715 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G715 MC10",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
)

MALI_G57 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G57 MC2",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=8192,
    max_cube_map_texture_size=8192,
    max_renderbuffer_size=8192,
    max_viewport_dims=(8192, 8192),
    max_texture_max_anisotropy_ext=16,
)

MALI_G68 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G68 MC4",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

ADRENO_740 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 740",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

ADRENO_730 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 730",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
    max_samples=4,
)

ADRENO_642 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 642",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=8192,
    max_cube_map_texture_size=8192,
    max_renderbuffer_size=8192,
    max_viewport_dims=(8192, 8192),
)

ADRENO_660 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 660",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# Google Tensor GPU used in Pixel devices.
GOOGLE_MALI = GpuProfile(
    unmasked_vendor="Google Inc. (ARM)",
    unmasked_renderer="Mali-G78 MP20",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

GOOGLE_IMAGINATION = GpuProfile(
    unmasked_vendor="Google Inc. (Qualcomm)",
    unmasked_renderer="Adreno (TM) 642L",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=8192,
    max_cube_map_texture_size=8192,
    max_renderbuffer_size=8192,
    max_viewport_dims=(8192, 8192),
)

# The GPU that ships with the Tensor G2 in the Pixel 7.  The Pixel 7
# carries a Mali, not an Adreno: the ``GOOGLE_IMAGINATION`` entry above
# is a Tensor G1 part and belongs to the Pixel 6a.
MALI_G710 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G710 MC10",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Exynos 1380 in the Galaxy A54.
MALI_G68 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G68 MC4",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Helio G99 in the Galaxy A14.
MALI_G57 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G57 MC2",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Snapdragon 8 Gen 2 in the Galaxy S23.
ADRENO_740 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 740",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Snapdragon 8 Gen 3 in the Galaxy S24 Ultra.
ADRENO_750 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 750",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Dimensity 9200 in the Vivo X90.
IMMORTALIS_G715 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Immortalis-G715 MC10",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Snapdragon 8+ Gen 1 in the Nothing Phone (2).
ADRENO_742 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 742",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Snapdragon 4 Gen 1 in the entry level
# Redmi models.
ADRENO_619 = GpuProfile(
    unmasked_vendor="Qualcomm",
    unmasked_renderer="Adreno (TM) 619",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=8192,
    max_cube_map_texture_size=8192,
    max_renderbuffer_size=8192,
    max_viewport_dims=(8192, 8192),
)

# The GPU that ships with the Exynos 2200 in the Galaxy S22.
XCLIPSE_920 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Xclipse 920",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Kirin 9000S in the Huawei P60 Pro.
MALEON_910 = GpuProfile(
    unmasked_vendor="Huawei",
    unmasked_renderer="Maleoon 910",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)

# The GPU that ships with the Dimensity 8200 in the OnePlus Nord 3.
MALI_G610 = GpuProfile(
    unmasked_vendor="ARM",
    unmasked_renderer="Mali-G610 MC6",
    vendor="WebKit",
    renderer="WebKit WebGL",
    version="WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    max_texture_size=16384,
    max_cube_map_texture_size=16384,
)


# --------------------------------------------------------------------------
# Device presets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DevicePreset:
    """A physically coherent Android device."""

    preset_id: str
    manufacturer: str
    brand: str
    model: str
    device: str
    product: str
    hardware: str
    board: str
    bootloader: str
    fingerprint_product: str
    gpu: GpuProfile
    screen: Screen
    abi: AndroidAbiSoc
    release: str
    sdk_int: int
    security_patch: str
    build_id: str
    version_incremental: str
    ram_options: tuple[int, ...] = (4, 6, 8)
    cpu_options: tuple[int, ...] = (6, 8)
    dpr_options: tuple[float, ...] = (2.625, 2.75, 3.0)
    density_options: tuple[int, ...] = (420, 440, 480)
    year: int = 2024

    # ---------------- derived helpers ----------------

    @property
    def host(self) -> str:
        return "android-build"

    @property
    def user(self) -> str:
        return "builduser"

    @property
    def tags(self) -> str:
        return "release-keys"

    @property
    def type(self) -> str:
        return "user"

    @property
    def display(self) -> str:
        return f"{self.build_id}.{self.version_incremental}"

    @property
    def build_id_number(self) -> str:
        """The numeric build id used in the fingerprint's third segment."""
        # Derive a stable 5-digit build number from the preset id so that the
        # fingerprint looks like a real Android build (e.g. "11016").
        digest = 0
        for char in self.preset_id:
            digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
        return f"{(digest % 90000) + 10000}"

    @property
    def serial(self) -> str:
        """The device serial reported by ``Build.SERIAL``.

        ``hash()`` must not be used here: CPython randomises the string hash
        per process, so a ``hash()``-derived serial would change on every
        restart and the device would look like a different physical unit each
        time it launched.  A profile's fingerprint has to be stable, so the
        value is derived from a fixed FNV-1a digest of the preset id.
        """
        digest = 0xCBF29CE484222325
        for byte in self.preset_id.encode("utf-8"):
            digest ^= byte
            digest = (digest * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return f"0x{digest & 0xFFFFFFFF:08x}"

    def build_fingerprint(self) -> str:
        """Build the RFC-shaped Android fingerprint for this device."""
        return (
            f"{self.brand.lower()}/{self.product}/{self.device}:"
            f"{self.release}/{self.build_id}.{self.version_incremental}:"
            f"{self.build_id_number}:user/{self.tags}"
        )

    def android_build(self) -> AndroidBuild:
        return AndroidBuild(
            version_release=self.release,  # type: ignore[arg-type]
            sdk_int=self.sdk_int,  # type: ignore[arg-type]
            security_patch=self.security_patch,
            build_id=self.build_id,
            display=self.display,
            version_incremental=self.version_incremental,
            model=self.model,
            device=self.device,
            product=self.product,
            manufacturer=self.manufacturer,
            brand=self.brand,
            hardware=self.hardware,
            board=self.board,
            bootloader=self.bootloader,
            fingerprint=self.build_fingerprint(),
            host=self.host,
            user=self.user,
            tags=self.tags,
            type=self.type,
            serial=self.serial,
        )


def _screen(
    width: int,
    height: int,
    dpr: float,
    density: int,
    color_depth: int = 24,
) -> Screen:
    return Screen(
        width=width,
        height=height,
        avail_width=width,
        avail_height=height - 48,
        avail_left=0,
        avail_top=0,
        color_depth=color_depth,  # type: ignore[arg-type]
        pixel_depth=color_depth,  # type: ignore[arg-type]
        device_pixel_ratio=dpr,  # type: ignore[arg-type]
        density_dpi=density,  # type: ignore[arg-type]
    )


def _abi(
    vendor: str,
    model: str,
    layout: str,
    gpu_arch: str,
    primary: str = "arm64-v8a",
) -> AndroidAbiSoc:
    return AndroidAbiSoc(
        supported_abis=["arm64-v8a", "armeabi-v7a"],
        supported_64_bit_abis=["arm64-v8a"],
        supported_32_bit_abis=["armeabi-v7a"],
        primary_abi=primary,
        is_64_bit=True,
        soc_vendor=vendor,  # type: ignore[arg-type]
        soc_model=model,
        cpu_cluster_layout=layout,
        gpu_architecture=gpu_arch,
    )


# The catalog.  Devices are grouped by manufacturer and cover the SoC/GPU
# combinations that actually ship together in the wild.
DEVICE_PRESETS: tuple[DevicePreset, ...] = (
    DevicePreset(
        preset_id="pixel-8-pro-android14",
        manufacturer="Google",
        brand="google",
        model="Pixel 8 Pro",
        device="husky",
        product="husky",
        hardware="husky",
        board="husky",
        bootloader="husky-1.0-0000007",
        fingerprint_product="husky",
        gpu=GOOGLE_MALI,
        screen=_screen(1344, 2992, 2.625, 420),
        abi=_abi("Google", "Tensor G3", "1x3x4 Cortex-X3/A715/A510", "Mali-G78 MP20"),
        release="14",
        sdk_int=34,
        security_patch="2024-06-05",
        build_id="UQ1A.240105.004",
        version_incremental="10750268",
        ram_options=(8, 12),
        cpu_options=(8, 12),
        dpr_options=(2.625, 3.0),
        density_options=(420, 480),
        year=2024,
    ),
    DevicePreset(
        preset_id="pixel-7-android14",
        manufacturer="Google",
        brand="google",
        model="Pixel 7",
        device="panther",
        product="panther",
        hardware="husky",
        board="panther",
        bootloader="panther-1.0-0000001",
        fingerprint_product="panther",
        gpu=MALI_G710,
        screen=_screen(1080, 2400, 2.625, 420),
        abi=_abi("Google", "Tensor G2", "1x2x2 Cortex-X1/A78/A55", "Mali-G710 MC10"),
        release="14",
        sdk_int=34,
        security_patch="2024-02-05",
        build_id="UQ1A.230901.001",
        version_incremental="10199132",
        ram_options=(8,),
        cpu_options=(8,),
        dpr_options=(2.625, 3.0),
        density_options=(420, 440),
        year=2023,
    ),
    DevicePreset(
        preset_id="galaxy-s24-ultra-android14",
        manufacturer="Samsung",
        brand="samsung",
        model="SM-S928B",
        device="q5q",
        product="q5q",
        hardware="q5q",
        board="q5q",
        bootloader="q5q-1.0-20240201",
        fingerprint_product="q5q",
        gpu=ADRENO_750,
        screen=_screen(1440, 3120, 3.0, 480),
        abi=_abi("Qualcomm", "Snapdragon 8 Gen 3", "1x2x2 Cortex-X4/A720/A640", "Adreno 750"),
        release="14",
        sdk_int=34,
        security_patch="2024-03-01",
        build_id="UP1A.231005.007",
        version_incremental="10818074",
        ram_options=(8, 12),
        cpu_options=(8, 12),
        dpr_options=(2.625, 3.0),
        density_options=(480,),
        year=2024,
    ),
    DevicePreset(
        preset_id="galaxy-s23-android14",
        manufacturer="Samsung",
        brand="samsung",
        model="SM-S911B",
        device="dm1q",
        product="dm1q",
        hardware="dm1q",
        board="dm1q",
        bootloader="dm1q-1.0-230501",
        fingerprint_product="dm1q",
        gpu=ADRENO_740,
        screen=_screen(1080, 2340, 3.0, 440),
        abi=_abi("Qualcomm", "Snapdragon 8 Gen 2", "1x2x2 Cortex-X3/A710/A510", "Adreno 740"),
        release="14",
        sdk_int=34,
        security_patch="2024-01-10",
        build_id="UP1A.231005.007",
        version_incremental="10650147",
        ram_options=(8,),
        cpu_options=(8,),
        dpr_options=(2.625, 3.0),
        density_options=(440,),
        year=2023,
    ),
    DevicePreset(
        preset_id="galaxy-a54-android13",
        manufacturer="Samsung",
        brand="samsung",
        model="SM-A546B",
        device="q2q",
        product="q2q",
        hardware="q2q",
        board="q2q",
        bootloader="q2q-1.0-20230301",
        fingerprint_product="q2q",
        gpu=MALI_G68,
        screen=_screen(1080, 2340, 2.625, 440),
        abi=_abi(
            "Samsung",
            "Exynos 1380",
            "1x3x4 Cortex-A78/A55/A55",
            "Mali-G68 MC4",
        ),
        release="13",
        sdk_int=33,
        security_patch="2023-11-01",
        build_id="TP1A.220624.014",
        version_incremental="9189376",
        ram_options=(4, 6, 8),
        cpu_options=(6, 8),
        dpr_options=(2.625, 3.0),
        density_options=(440, 480),
        year=2023,
    ),
    DevicePreset(
        preset_id="oneplus-11-android13",
        manufacturer="OnePlus",
        brand="OnePlus",
        model="CPH2449",
        device="CPH2449",
        product="CPH2449",
        hardware="CPH2449",
        board="qcom",
        bootloader="CPH2449-1.0-20230201",
        fingerprint_product="CPH2449",
        gpu=ADRENO_740,
        screen=_screen(1440, 3216, 3.0, 480),
        abi=_abi(
            "Qualcomm",
            "Snapdragon 8 Gen 2",
            "1x2x2 Cortex-X3/A710/A510",
            "Adreno 740",
        ),
        release="13",
        sdk_int=33,
        security_patch="2023-12-01",
        build_id="TKQ1.220808.001",
        version_incremental="9051955",
        ram_options=(8, 12, 16),
        cpu_options=(8, 12),
        dpr_options=(2.625, 3.0),
        density_options=(480,),
        year=2023,
    ),
    DevicePreset(
        preset_id="xiaomi-13-android14",
        manufacturer="Xiaomi",
        brand="Xiaomi",
        model="2211133C",
        device="fuxi",
        product="fuxi",
        hardware="qcom",
        board="qcom",
        bootloader="fuxi-1.0-20230301",
        fingerprint_product="fuxi",
        gpu=ADRENO_740,
        screen=_screen(1200, 2670, 3.0, 480),
        abi=_abi(
            "Qualcomm",
            "Snapdragon 8 Gen 2",
            "1x2x2 Cortex-X3/A710/A510",
            "Adreno 740",
        ),
        release="14",
        sdk_int=34,
        security_patch="2024-01-05",
        build_id="UKQ1.230804.001",
        version_incremental="10818074",
        ram_options=(8, 12),
        cpu_options=(8,),
        dpr_options=(2.625, 3.0),
        density_options=(480,),
        year=2023,
    ),
    DevicePreset(
        preset_id="redmi-note-12-android13",
        manufacturer="Xiaomi",
        brand="Redmi",
        model="23021RAAEG",
        device="sunny",
        product="sunny",
        hardware="qcom",
        board="qcom",
        bootloader="sunny-1.0-20230601",
        fingerprint_product="sunny",
        gpu=ADRENO_642,
        screen=_screen(1080, 2400, 2.75, 440),
        abi=_abi(
            "Qualcomm",
            "Snapdragon 4 Gen 1",
            "1x3x4 Cortex-A78/A55/A55",
            "Adreno 642",
        ),
        release="13",
        sdk_int=33,
        security_patch="2023-09-01",
        build_id="TP1A.220624.014",
        version_incremental="9023803",
        ram_options=(4, 6, 8),
        cpu_options=(8,),
        dpr_options=(2.75, 3.0),
        density_options=(440, 480),
        year=2023,
    ),
    DevicePreset(
        preset_id="oppo-find-x6-android14",
        manufacturer="OPPO",
        brand="OPPO",
        model="PJZ110",
        device="PJZ110",
        product="PJZ110",
        hardware="qcom",
        board="qcom",
        bootloader="PJZ110-1.0-20230701",
        fingerprint_product="PJZ110",
        gpu=ADRENO_740,
        screen=_screen(1264, 2780, 3.0, 480),
        abi=_abi(
            "Qualcomm",
            "Snapdragon 8 Gen 2",
            "1x2x2 Cortex-X3/A710/A510",
            "Adreno 740",
        ),
        release="14",
        sdk_int=34,
        security_patch="2024-02-01",
        build_id="UKQ1.230924.001",
        version_incremental="10818074",
        ram_options=(12, 16),
        cpu_options=(8, 12),
        dpr_options=(2.625, 3.0),
        density_options=(480,),
        year=2024,
    ),
    DevicePreset(
        preset_id="vivo-x90-android14",
        manufacturer="vivo",
        brand="vivo",
        model="V2247A",
        device="V2247A",
        product="V2247A",
        hardware="qcom",
        board="qcom",
        bootloader="V2247A-1.0-20230701",
        fingerprint_product="V2247A",
        gpu=IMMORTALIS_G715,
        screen=_screen(1260, 2800, 3.0, 480),
        abi=_abi(
            "MediaTek",
            "Dimensity 9200",
            "1x3x4 Cortex-X3/A720/A640",
            "Immortalis-G715 MC10",
        ),
        release="14",
        sdk_int=34,
        security_patch="2024-01-15",
        build_id="UP1A.231005.007",
        version_incremental="10314917",
        ram_options=(8, 12),
        cpu_options=(8, 12),
        dpr_options=(2.625, 3.0),
        density_options=(480,),
        year=2024,
    ),
    DevicePreset(
        preset_id="samsung-galaxy-a14-android13",
        manufacturer="Samsung",
        brand="samsung",
        model="SM-A145F",
        device="a14",
        product="a14",
        hardware="a14",
        board="qcom",
        bootloader="a14-1.0-20230801",
        fingerprint_product="a14",
        gpu=MALI_G57,
        screen=_screen(1080, 2400, 2.625, 400),
        abi=_abi(
            "MediaTek",
            "Helio G99",
            "2x2 Cortex-A76/A55",
            "Mali-G57 MC2",
        ),
        release="13",
        sdk_int=33,
        security_patch="2023-08-01",
        build_id="TP1A.220624.014",
        version_incremental="8608992",
        ram_options=(4, 6),
        cpu_options=(8,),
        dpr_options=(2.625, 3.0),
        density_options=(400, 440),
        year=2023,
    ),
)


def _extended_presets() -> tuple[DevicePreset, ...]:
    """The additional devices, loaded on demand.

    The extended catalogue lives in its own module because it imports
    the GPU profiles from here; importing it back would be circular.
    The import therefore happens the first time the catalogue is asked
    for, which is once per process.
    """

    global _EXTENDED_CACHE
    if _EXTENDED_CACHE is None:
        from .device_catalog_extended import EXTENDED_PRESETS

        _EXTENDED_CACHE = EXTENDED_PRESETS
    return _EXTENDED_CACHE


_EXTENDED_CACHE: tuple[DevicePreset, ...] | None = None


def all_presets() -> tuple[DevicePreset, ...]:
    """Every device the engine knows about.

    This is the reference catalogue plus the extended one.  The
    reference devices are the ones the test suite is written against;
    the extended devices widen the fleet so that a vendor grouping on
    the model, the board or the build identifier has far more than a
    handful of buckets to separate.
    """

    return DEVICE_PRESETS + _extended_presets()


def get_preset(preset_id: str) -> DevicePreset | None:
    """Look up a preset by its identifier."""
    for preset in all_presets():
        if preset.preset_id == preset_id:
            return preset
    return None


def list_presets() -> list[DevicePreset]:
    """All known device presets."""
    return list(all_presets())


__all__ = [
    "GpuProfile",
    "DevicePreset",
    "DEVICE_PRESETS",
    "all_presets",
    "get_preset",
    "list_presets",
    "MALI_G715",
    "MALI_G710",
    "MALI_G610",
    "MALI_G57",
    "MALI_G68",
    "IMMORTALIS_G715",
    "MALEON_910",
    "XCLIPSE_920",
    "ADRENO_740",
    "ADRENO_750",
    "ADRENO_742",
    "ADRENO_730",
    "ADRENO_660",
    "ADRENO_642",
    "ADRENO_619",
    "GOOGLE_MALI",
    "GOOGLE_IMAGINATION",
]
