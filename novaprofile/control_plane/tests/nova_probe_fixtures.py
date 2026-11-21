"""Shared test fixtures for the NOVA probe and launcher test-suites.

The real ``ProfileConfig`` lives in ``novaprofile.schema.profile`` which is
written in parallel by the lead agent.  Until (and unless) it becomes
importable, a faithful local stand-in is used so that the probe and the
launcher can be tested independently and deterministically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Profile config stand-in
# --------------------------------------------------------------------------


@dataclass
class _Identity:
    id: str = "01930000-0000-7000-8000-000000000001"
    name: str = "Test Device"
    group_id: str | None = None
    folder_id: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    status: str = "ready"
    created_at: str = "2026-01-01T00:00:00Z"
    updated_at: str = "2026-01-01T00:00:00Z"
    last_started_at: str | None = None
    last_closed_at: str | None = None
    total_runtime_ms: int = 0
    launch_count: int = 0


@dataclass
class _SeedConfig:
    master_seed: str = "a" * 64
    seed_epoch: int = 0
    nudge_step: int = 0
    hkdf_version: str = "hkdf-sha256-v1"
    preset_id: str = "pixel-7"
    core_version: str = "1.0.0"


@dataclass
class _Navigator:
    user_agent: str = (
        "Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/AP1A.240505.004) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 "
        "Mobile Safari/537.36"
    )
    app_version: str = "5.0 (Linux; Android 14)"
    app_name: str = "Netscape"
    app_code_name: str = "Mozilla"
    vendor: str = "Google Inc."
    vendor_sub: str = ""
    product: str = "Gecko"
    product_sub: str = "20030107"
    platform: str = "Linux armv8l"
    hardware_concurrency: int = 8
    device_memory: int = 8
    max_touch_points: int = 5
    language: str = "en-US"
    languages: list[str] = field(default_factory=lambda: ["en-US", "en"])
    cookie_enabled: bool = True
    do_not_track: str | None = None
    global_privacy_control: bool | None = None
    pdf_viewer_enabled: bool = True
    webdriver: bool = False
    on_line: bool = True
    java_enabled: bool = False


@dataclass
class _BrandVersion:
    brand: str = "Chromium"
    version: str = "126"


@dataclass
class _UserAgentData:
    brands: list[_BrandVersion] = field(default_factory=lambda: [_BrandVersion("Chromium", "126")])
    full_version_list: list[_BrandVersion] = field(
        default_factory=lambda: [_BrandVersion("Chromium", "126.0.6478.71")]
    )
    mobile: bool = True
    platform: str = "Android"
    platform_version: str = "14.0.0"
    architecture: str = "arm"
    bitness: str = "64"
    model: str = "Pixel 7"
    wow64: bool = False


@dataclass
class _Screen:
    width: int = 1080
    height: int = 2400
    avail_width: int = 1080
    avail_height: int = 2400
    avail_left: int = 0
    avail_top: int = 0
    color_depth: int = 24
    pixel_depth: int = 24
    device_pixel_ratio: float = 2.625
    density_dpi: int = 420


@dataclass
class _Viewport:
    inner_width: int = 412
    inner_height: int = 915
    outer_width: int = 412
    outer_height: int = 915
    screen_x: int = 0
    screen_y: int = 0
    visual_viewport_scale: float = 1.0
    safe_area_insets: dict[str, int] = field(
        default_factory=lambda: {"top": 0, "right": 0, "bottom": 0, "left": 0}
    )
    scrollbar_width: int = 0


@dataclass
class _MediaQueries:
    prefers_color_scheme: str = "dark"
    prefers_reduced_motion: str = "no-preference"
    prefers_contrast: str = "no-preference"
    forced_colors: str = "none"
    pointer: str = "coarse"
    any_pointer: list[str] = field(default_factory=lambda: ["coarse"])
    hover: str = "none"
    any_hover: list[str] = field(default_factory=lambda: ["none"])
    color_gamut: str = "srgb"
    dynamic_range: str = "standard"


@dataclass
class _Timezone:
    iana_name: str = "America/New_York"
    offset_minutes: int = -240
    dst_active: bool = True
    abbreviation: str = "EDT"


@dataclass
class _Locale:
    primary: str = "en-US"
    intl_locale: str = "en-US"
    numbering_system: str = "latn"
    calendar: str = "gregory"
    hour_cycle: str = "h23"
    currency: str = "USD"


@dataclass
class _WebGLParameters:
    max_texture_size: int = 4096
    max_cube_map_texture_size: int = 4096
    max_3d_texture_size: int = 2048
    max_array_texture_layers: int = 2048
    max_renderbuffer_size: int = 4096
    max_viewport_dims: list[int] = field(default_factory=lambda: [4096, 4096])
    max_vertex_attribs: int = 16
    max_vertex_uniform_vectors: int = 256
    max_vertex_uniform_components: int = 1024
    max_fragment_uniform_vectors: int = 256
    max_fragment_uniform_components: int = 1024
    max_varying_vectors: int = 15
    max_combined_texture_image_units: int = 32
    max_vertex_texture_image_units: int = 16
    max_texture_image_units: int = 16
    max_color_attachments: int = 8
    max_draw_buffers: int = 8
    max_samples: int = 4
    max_uniform_buffer_bindings: int = 24
    aliased_line_width_range: list[float] = field(default_factory=lambda: [1.0, 1.0])
    aliased_point_size_range: list[float] = field(default_factory=lambda: [1.0, 1024.0])
    sample_buffers: int = 4
    samples: int = 4
    max_texture_max_anisotropy_ext: int = 16


@dataclass
class _WebGLConfig:
    mode: str = "noise"
    unmasked_vendor: str = "Google Inc. (ARM)"
    unmasked_renderer: str = "ANGLE (ARM, Mali-G78, Mali-G78, OpenGL ES 3.2)"
    vendor: str = "WebKit"
    renderer: str = "WebKit WebGL"
    version: str = "WebGL 2.0 (OpenGL ES 3.0 Chromium)"
    shading_language_version: str = "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)"
    image_noise: dict[str, Any] = field(
        default_factory=lambda: {"enabled": True, "value": "0.12345678901234567"}
    )
    read_pixels_sync: bool = True
    extensions: dict[str, Any] = field(default_factory=lambda: {"supported": []})
    parameters: _WebGLParameters = field(default_factory=_WebGLParameters)
    shader_precision: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Proxy:
    enabled: bool = True
    protocol: str = "http"
    host: str = "198.51.100.7"
    port: int = 8080
    username: str = "user"
    password: str = "secret"
    auth_method: str = "userpass"
    connect_timeout_ms: int = 30000
    read_timeout_ms: int = 60000


@dataclass
class _WebRtc:
    mode: str = "proxy_only"
    spoofed_public_ip_v4: str = "203.0.113.9"
    spoofed_public_ip_v6: str = ""
    spoofed_local_ip_v4: str = "192.168.1.5"
    mdns_enabled: bool = False
    block_non_proxied_udp: bool = True
    ice_transport_policy: str = "relay"


@dataclass
class _Dns:
    mode: str = "proxy_remote"
    servers: list[str] = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    doh_url: str = ""
    dot_host: str = ""
    prevent_dns_leak: bool = True


@dataclass
class _HttpHeaders:
    accept_language: str = "en-US,en;q=0.9"
    dnt: str | None = None
    sec_ch_ua: str = '"Chromium";v="126", "Not:A-Brand";v="24"'
    sec_ch_ua_mobile: str = "?1"
    sec_ch_ua_platform: str = '"Android"'
    sec_ch_ua_platform_version: str = "14.0.0"
    sec_ch_ua_arch: str = "arm"
    sec_ch_ua_bitness: str = "64"
    sec_ch_ua_model: str = "Pixel 7"
    sec_ch_ua_full_version_list: str = ""
    custom_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _StorageNetwork:
    quota_bytes: int = 3_221_225_472
    usage_bytes: int = 1024
    persisted_default: bool = False
    effective_type: str = "4g"
    rtt: int = 50
    downlink: float = 10.0
    save_data: bool = False
    connection_type: str = "wifi"


@dataclass
class _Fonts:
    mode: str = "android_preset"
    installed: list[str] = field(default_factory=lambda: ["Roboto", "Noto Sans"])
    salt: str = "abcdefgh"
    preserve_metric_clusters: bool = True


@dataclass
class _ProfileConfig:
    """A minimal stand-in for ``novaprofile.schema.profile.ProfileConfig``."""

    identity: _Identity = field(default_factory=_Identity)
    seed_config: _SeedConfig = field(default_factory=_SeedConfig)
    navigator: _Navigator = field(default_factory=_Navigator)
    user_agent_data: _UserAgentData = field(default_factory=_UserAgentData)
    screen: _Screen = field(default_factory=_Screen)
    viewport: _Viewport = field(default_factory=_Viewport)
    media_queries: _MediaQueries = field(default_factory=_MediaQueries)
    timezone: _Timezone = field(default_factory=_Timezone)
    locale: _Locale = field(default_factory=_Locale)
    webgl: _WebGLConfig = field(default_factory=_WebGLConfig)
    proxy: _Proxy = field(default_factory=_Proxy)
    webrtc: _WebRtc = field(default_factory=_WebRtc)
    dns: _Dns = field(default_factory=_Dns)
    http_headers: _HttpHeaders = field(default_factory=_HttpHeaders)
    storage_network: _StorageNetwork = field(default_factory=_StorageNetwork)
    fonts: _Fonts = field(default_factory=_Fonts)

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        """A pydantic-like dump so the launcher can serialise the config."""

        from dataclasses import asdict

        return asdict(self)


def make_config(**overrides: Any) -> _ProfileConfig:
    """Build a stand-in profile config, applying ``overrides``.

    The override keys are dotted paths, e.g. ``"navigator.user_agent"``.
    """

    config = _ProfileConfig()
    for dotted, value in overrides.items():
        target: Any = config
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        setattr(target, parts[-1], value)
    return config


# --------------------------------------------------------------------------
# The real ProfileConfig, when it is available
# --------------------------------------------------------------------------

try:  # pragma: no cover - depends on the parallel lead work
    from novaprofile.schema.profile import ProfileConfig  # type: ignore[import-not-found]

    HAS_REAL_PROFILE_CONFIG = True
except Exception:  # pragma: no cover - the common case during parallel work
    ProfileConfig = _ProfileConfig  # type: ignore[assignment,misc]
    HAS_REAL_PROFILE_CONFIG = False


# --------------------------------------------------------------------------
# Helpers (plain functions -- the tests define their own pytest fixtures so
# that this module stays importable without pytest collection side effects)
# --------------------------------------------------------------------------


def live_probe_enabled() -> bool:
    """Whether the opt-in live browser test is enabled."""

    return os.environ.get("NOVA_PROBE_LIVE", "").strip() in {"1", "true", "TRUE", "yes"}


def isolation_home(tmp_path: Path) -> Path:
    """Create the temporary NOVA home directory used by the tests."""

    home = tmp_path / "nova-home"
    home.mkdir(parents=True, exist_ok=True)
    return home


__all__ = [
    "HAS_REAL_PROFILE_CONFIG",
    "ProfileConfig",
    "isolation_home",
    "live_probe_enabled",
    "make_config",
]
