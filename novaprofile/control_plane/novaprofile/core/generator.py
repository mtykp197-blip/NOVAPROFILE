"""Profile generation.

Turns a device preset plus a master seed into a complete, internally
consistent :class:`ProfileConfig`.  Every value that is not dictated by the
device catalog is derived from the seed, which makes generation
deterministic: the same preset and the same seed always produce the same
profile, and two different seeds never produce the same fingerprint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from ..schema.hardware import (
    AndroidAbiSoc,
    AndroidBuild,
    Canvas2DConfig,
    MediaQueries,
    Orientation,
    Screen,
    ShaderPrecision,
    ShaderPrecisionRange,
    Viewport,
    WebGLConfig,
    WebGLExtensions,
    WebGLImageNoise,
    WebGLParameters,
    WebGPUAdapter,
    WebGPUConfig,
)
from ..schema.identity import (
    BrandVersion,
    Geolocation,
    Locale,
    Navigator,
    Permissions,
    PluginEntry,
    PluginMimeType,
    PluginsMime,
    Timezone,
    UserAgentData,
)
from ..schema.network import (
    Dns,
    GeoIntelligence,
    GeoSync,
    HttpHeaders,
    Proxy,
    ProxyRotation,
    WebRtc,
)
from ..schema.profile import ProfileConfig, ProfileIdentity, SeedConfig
from ..schema.signals import (
    AbsoluteOrientation,
    Audio,
    AudioInputDevice,
    AudioOutputDevice,
    Battery,
    BatterySpeechGamepad,
    Fonts,
    Gamepad,
    HeapSizeLimit,
    MathPrecision,
    MediaDecode,
    MediaDevices,
    Rects,
    RelativeOrientation,
    Sensors,
    SpeechVoice,
    StorageNetwork,
    Vector3D,
    VideoInputDevice,
)
from ..schema.version import BrowserVersion
from .devices import get_preset
from .font_catalog import fonts_for_device
from .geo_catalog import GEO_CATALOGUE
from .hardware_approximation import (
    approximate_device_memory,
    approximate_hardware_concurrency,
)
from .identity import DeviceIdentity, DeviceIdentityFactory
from .seed import Axis, SeedDeriver, new_master_seed
from .versions import (
    DEFAULT_REGISTRY,
    MAX_SUPPORTED_MAJOR,
    MIN_SUPPORTED_MAJOR,
    VERSION_TABLE,
    VersionCapabilities,
    _android_for_major,
)

#: The Chromium major this build falls back to when no version can be
#: derived from the device's Android release.
#:
#: This is the "the version this build defaults to" meaning and it is
#: deliberately *separate* from the version a freshly generated profile
#: picks.  A profile that draws its browser version from the seed gets a
#: different major on every seed (see :func:`_pick_chrome_major`), while
#: this constant stays the single answer for a caller that asks for the
#: build's default version.  Keeping the two apart means the fallback does
#: not silently become "the version every profile gets".
DEFAULT_BROWSER_MAJOR: int = 120

#: The smallest Chromium band the random pick is allowed to fill edge to
#: edge.
#:
#: A band of three or fewer majors has no interior to draw from, so it is
#: used whole.  Anything larger is trimmed at both ends (see
#: :func:`_pick_chrome_major`).
_BAND_EDGE_TRIM_THRESHOLD: int = 3

# --------------------------------------------------------------------------
# Locale / timezone catalog
# --------------------------------------------------------------------------

#: (locale, IANA timezone, country, currency, hour cycle)
#:
#: Kept as the five-field view of the catalogue for callers that only need
#: the anchor; the generator itself uses :data:`GEO_CATALOGUE`, which also
#: carries the secondary locales.
LOCALE_PRESETS: tuple[tuple[str, str, str, str, str], ...] = tuple(
    (locale, timezone, country, currency, cycle)
    for locale, timezone, country, currency, cycle, _secondaries in GEO_CATALOGUE
)

#: The base Android font set.  ``preserve_metric_clusters`` keeps the
#: metric-compatible families together.
ANDROID_FONT_PRESET: tuple[str, ...] = (
    "Roboto",
    "Roboto Condensed",
    "Noto Sans",
    "Noto Sans Display",
    "Noto Serif",
    "Noto Color Emoji",
    "Noto Sans Symbols",
    "Noto Sans Symbols 2",
    "Noto Sans CJK JP",
    "Noto Sans Devanagari",
    "Noto Sans Thai",
    "Droid Sans Mono",
    "Cutive Mono",
    "Coming Soon",
    "Dancing Script",
    "Carrois Gothic SC",
    "Droid Serif",
    "Eczar",
    "Sourc Code Pro",
    "Ubuntu Medium",
)

#: The WebGL extension sets that the catalog GPUs actually expose.
_WEBGL1_EXTENSIONS: tuple[str, ...] = (
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_polygon_offset_clamp",
    "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "EXT_texture_mirror_clamp_to_edge",
    "EXT_sRGB",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_float_linear",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "WEBGL_blend_func_extended",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_multi_draw",
)

_WEBGL2_EXTENSIONS: tuple[str, ...] = (
    "EXT_color_buffer_float",
    "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query",
    "EXT_float_blend",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "EXT_texture_mirror_clamp_to_edge",
    "EXT_texture_norm16",
    "OES_texture_float_linear",
    "WEBGL_blend_func_extended",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_multi_draw",
    "WEBGL_polygon_mode",
)

#: The smallest canvas noise strength a profile may carry.
#:
#: A strength of zero would leave the canvas untouched, and a strength in
#: the lowest tail of the distribution perturbs it so little that two
#: devices can still produce the same bytes.  At the scale this system
#: targets, that weakest tail is what decides how large the clusters of
#: indistinguishable profiles become.
CANVAS_NOISE_FLOOR: float = 0.05

#: Chromium's Android WebGL1 extension list.
_CHROMIUM_ANDROID_EXTENSIONS: tuple[str, ...] = (
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc",
    "EXT_texture_filter_anisotropic",
    "EXT_texture_mirror_clamp_to_edge",
    "EXT_sRGB",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_float_linear",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "WEBGL_blend_func_extended",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_multi_draw",
)


@dataclass(frozen=True)
class GeoTarget:
    """The geographic anchor of a profile."""

    locale: str
    timezone: str
    country_code: str
    currency: str
    hour_cycle: str
    languages: tuple[str, ...]


def _geo_target(
    locale: str,
    timezone: str,
    country: str,
    currency: str,
    cycle: str,
    secondary: tuple[str, ...] = (),
) -> GeoTarget:
    base = locale.split("-")[0]
    languages: list[str] = [locale]
    if base != locale and base not in languages:
        languages.append(base)
    for extra in secondary:
        if extra not in languages:
            languages.append(extra)
    return GeoTarget(
        locale=locale,
        timezone=timezone,
        country_code=country,
        currency=currency,
        hour_cycle=cycle,  # type: ignore[arg-type]
        languages=tuple(languages),
    )


# --------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------


class ProfileGenerator:
    """Builds complete profiles from a preset and a seed."""

    def __init__(self, resolver=None) -> None:
        from .consistency import ConsistencyResolver

        self._resolver = resolver or ConsistencyResolver()

        # The physical unit the profile is being built for.  These are set
        # at the start of :meth:`generate` and read by the builders that
        # need to know which device this is -- the font tier, for one.
        self._device_ram_gb = 4
        self._device_year = 2024
        self._device_release = "14"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        preset_id: str,
        *,
        name: str = "",
        master_seed: str | None = None,
        seed_epoch: int = 0,
        nudge_step: int = 0,
        geo: GeoTarget | None = None,
        proxy: Proxy | None = None,
        profile_id: str | None = None,
        browser_version: BrowserVersion | None = None,
    ) -> ProfileConfig:
        """Generate a complete profile.

        Raises
        ------
        KeyError
            When ``preset_id`` is unknown.
        """
        preset = get_preset(preset_id)
        if preset is None:
            raise KeyError(f"unknown device preset {preset_id!r}")

        seed_config = SeedConfig(
            master_seed=(master_seed or new_master_seed()).lower(),
            seed_epoch=seed_epoch,
            nudge_step=nudge_step,
            preset_id=preset.preset_id,
        )
        deriver = seed_config.deriver()

        geo_target = geo or self._pick_geo(deriver)

        identity = ProfileIdentity.create(
            name=name or f"{preset.model} #{seed_config.master_seed[:6]}",
            profile_id=profile_id,
        )

        # The device identity layer turns the family preset into a single
        # physical unit: its own serial, firmware build, bootloader, panel
        # and GPU.  Two profiles of the same model are therefore two
        # different devices as far as any observer can tell.
        device = DeviceIdentityFactory(deriver).build(preset)
        build = device.build
        abi = preset.abi

        # The RAM of this physical unit.  A phone does not report a random
        # amount of memory: it reports the amount the model was sold with,
        # and the model's list of options is what the catalogue knows.
        # Drawing from a flat set instead would let a 4 GB Redmi Note
        # report 16 GB, and ``navigator.deviceMemory`` is one of the
        # cheapest things for a script to compare between two visits.
        #
        # The value that reaches the page is *not* the physical memory.
        # Chromium rounds it to the nearest power of two and clamps it to
        # eight gigabytes on Android, so a 6 GB handset reports eight and
        # a 12 GB handset also reports eight.
        physical_ram_gb = int(
            deriver.derive_choice(Axis.NAVIGATOR, preset.ram_options, context="ram")
        )
        ram_gb = approximate_device_memory(physical_ram_gb)

        # The font tier follows the RAM and the model year, because those
        # two are what decide how much room the system partition leaves
        # for the optional font families.  The tier follows the *physical*
        # memory, because the fonts a handset ships with are decided by
        # the part that was soldered onto it.
        self._device_ram_gb = physical_ram_gb
        self._device_year = preset.year
        self._device_release = preset.release

        # The browser version is chosen by the caller (or derived from the
        # device's own Android release); the capability entry decides the
        # UA, the brands and the headers.
        #
        # When the caller does not ask for a specific version, the major
        # has to be one that actually shipped with the device's Android
        # release.  A profile that claims Android 13 and Chrome 120 is a
        # contradiction: Chromium 120 shipped with Android 14, and an
        # anti-bot vendor that checks the pair would single the profile out
        # immediately.
        #
        # The major is *drawn* from that band rather than fixed, because a
        # fleet where every profile of a given Android release reports the
        # same browser version is a fleet a vendor groups on for free: the
        # major is one of the few fields a device actually reveals about
        # how old its browser is.  The draw is derived from the seed, so
        # the profile stays reproducible.
        if browser_version is not None:
            major = browser_version.major
        else:
            major = _pick_chrome_major(deriver, build.version_release)

        caps = DEFAULT_REGISTRY.capabilities(major)

        if browser_version is None:
            version = BrowserVersion(major=caps.major, full_version=caps.full_version)
        elif not browser_version.full_version:
            version = browser_version.model_copy(update={"full_version": caps.full_version})
        else:
            if browser_version.full_version.split(".")[0] != str(browser_version.major):
                raise ValueError(
                    f"full_version {browser_version.full_version} does not belong to "
                    f"major {browser_version.major}"
                )
            version = browser_version

        navigator = self._build_navigator(
            deriver, build, geo_target, caps, preset.cpu_options, ram_gb
        )
        uad = self._build_user_agent_data(deriver, build, caps, version.full_version, abi)

        config = ProfileConfig(
            identity=identity,
            seed_config=seed_config,
            browser_version=version,
            android_build=build,
            android_abi_soc=abi,
            navigator=navigator,
            user_agent_data=uad,
            plugins_mime=self._build_plugins(),
            permissions=self._build_permissions(deriver),
            screen=device.screen,
            orientation=self._build_orientation(deriver),
            viewport=self._build_viewport(deriver, device.screen),
            media_queries=self._build_media_queries(deriver),
            timezone=self._build_timezone(geo_target),
            locale=self._build_locale(geo_target),
            geolocation=self._build_geolocation(deriver, geo_target),
            canvas_2d=self._build_canvas(deriver),
            webgl=self._build_webgl(deriver, device),
            webgpu=self._build_webgpu(deriver, device),
            audio=self._build_audio(deriver),
            rects=self._build_rects(deriver),
            fonts=self._build_fonts(deriver, geo_target.locale),
            media_devices=self._build_media_devices(deriver),
            storage_network=self._build_storage(deriver),
            battery_speech_gamepad=self._build_battery(deriver),
            sensors=self._build_sensors(deriver),
            math_precision=self._build_math_precision(deriver),
            heap_size_limit=self._build_heap_size_limit(deriver),
            media_decode=self._build_media_decode(deriver),
            proxy=proxy or Proxy(),
            proxy_rotation=ProxyRotation(),
            geo_intelligence=GeoIntelligence(
                country_code=geo_target.country_code,
                country_name=geo_target.country_code,
                checked_at=datetime.now(UTC),
            ),
            geo_sync=GeoSync(),
            webrtc=self._build_webrtc(deriver, proxy),
            dns=Dns(),
            http_headers=self._build_headers(navigator, uad, geo_target, caps),
        )
        return config

    def generate_many(
        self,
        preset_id: str,
        count: int,
        *,
        name_prefix: str = "",
        geo: GeoTarget | None = None,
    ) -> list[ProfileConfig]:
        """Generate ``count`` independent profiles."""
        profiles: list[ProfileConfig] = []
        for index in range(count):
            profile = self.generate(
                preset_id,
                name=f"{name_prefix or preset_id} {index + 1:04d}",
                geo=geo,
            )
            profiles.append(profile)
        return profiles

    # ------------------------------------------------------------------
    # Sub-builders
    # ------------------------------------------------------------------

    def _pick_geo(self, deriver: SeedDeriver) -> GeoTarget:
        locale, timezone, country, currency, cycle, secondaries = deriver.derive_choice(
            Axis.GEO_TIMEZONE, GEO_CATALOGUE, context="geo"
        )
        return _geo_target(locale, timezone, country, currency, cycle, secondaries)

    def _build_navigator(
        self,
        deriver: SeedDeriver,
        build: AndroidBuild,
        geo: GeoTarget,
        caps: VersionCapabilities,
        preset_cpu_options: tuple[int, ...] = (4, 6, 8, 12),
        device_memory_gb: int = 8,
    ) -> Navigator:
        model = build.model
        # The UA is built from the capability entry: from Chromium 110 on the
        # ``Build/`` token is gone and the version is frozen to ``.0.0.0``.
        build_token = (
            f" Build/{build.build_id}.{build.version_incremental}"
            if caps.ua_has_build_token
            else ""
        )
        ua = (
            f"Mozilla/5.0 (Linux; Android {build.version_release}; {model}{build_token}) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{caps.major}.0.0.0 Mobile Safari/537.36"
        )
        return Navigator(
            user_agent=ua,
            app_version=f"5.0 (Linux; Android {build.version_release})",
            app_name="Netscape",
            app_code_name="Mozilla",
            vendor="Google Inc.",
            vendor_sub="",
            product="Gecko",
            product_sub="20030107",
            platform=deriver.derive_choice(
                Axis.NAVIGATOR,
                ["Linux armv8l", "Linux aarch64", "Linux armv7l"],
                context="platform",
            ),
            hardware_concurrency=approximate_hardware_concurrency(
                int(deriver.derive_choice(Axis.NAVIGATOR, preset_cpu_options, context="cores"))
            ),
            # The *approximated* memory, not the physical one.  This is
            # the value Chromium would have produced on the same
            # hardware, and the native core hands it to the page
            # unchanged.
            device_memory=device_memory_gb,  # type: ignore[arg-type]
            max_touch_points=deriver.derive_choice(Axis.NAVIGATOR, (5, 10), context="touch"),  # type: ignore[arg-type]
            language=geo.locale,
            languages=list(geo.languages),
            cookie_enabled=True,
            do_not_track=None,
            global_privacy_control=None,
            pdf_viewer_enabled=True,
            webdriver=False,
            on_line=True,
            java_enabled=False,
        )

    def _build_user_agent_data(
        self,
        deriver: SeedDeriver,
        build: AndroidBuild,
        caps: VersionCapabilities,
        browser_full: str,
        abi: AndroidAbiSoc | None = None,
    ) -> UserAgentData:
        # The GREASE brand number is drawn per profile (Chromium randomises it
        # too).  The brand order comes from the capability entry, so it is
        # the same for every profile of a given major.
        not_a_brand = deriver.derive_int(Axis.CLIENT_HINTS, 8, 99, context="notabrand")
        greased = {
            "Not)A;Brand": (str(not_a_brand), f"{not_a_brand}.0.0.0"),
            "Chromium": (str(caps.major), browser_full),
            "Google Chrome": (str(caps.major), browser_full),
        }
        brands = [BrandVersion(brand=b, version=greased[b][0]) for b in caps.brand_order]

        # The full version list is a high-entropy hint: Chromium only
        # started sending it in major 116.  Before that the core reports
        # no full version list at all, so the profile must not carry one
        # either -- the value here is what the JS runtime hands to
        # ``navigator.userAgentData.getHighEntropyValues(['fullVersionList'])``
        # and what the ``Sec-CH-UA-Full-Version-List`` header is rendered
        # from.
        full_version_list: list[BrandVersion] = []
        if caps.has_client_hint("Sec-CH-UA-Full-Version-List"):
            full_version_list = [
                BrandVersion(brand=b, version=greased[b][1]) for b in caps.brand_order
            ]

        # The architecture and the bitness are part of the high-entropy
        # hints.  Chromium only started sending them in major 119; before
        # that they are absent, and a profile that reports them anyway is
        # claiming a capability the running core does not have.
        architecture = ""
        bitness = ""
        if abi is not None and caps.has_client_hint("Sec-CH-UA-Arch"):
            architecture = _architecture_for(abi)
            bitness = "64" if abi.is_64_bit else "32"

        # The model is a high-entropy hint too, and it follows the same
        # rule: ``navigator.userAgentData.model`` only exists from major
        # 119 on.  Before that the core reports no model at all, so the
        # profile must not carry one either -- the value here is what the
        # JS runtime hands to ``navigator.userAgentData.model`` and what
        # the ``Sec-CH-UA-Model`` header is rendered from, and a pre-119
        # core that names a model is claiming a capability it does not
        # have.
        model = build.model if caps.has_client_hint("Sec-CH-UA-Model") else ""

        return UserAgentData(
            brands=brands,
            full_version_list=full_version_list,
            mobile=True,
            platform="Android",
            platform_version=build.version_release,
            architecture=architecture,
            bitness=bitness,
            model=model,
            wow64=False,
        )

    def _build_plugins(self) -> PluginsMime:
        """Android Chromium exposes exactly one PDF-viewer plugin."""
        pdf = PluginMimeType(type="application/pdf", suffixes="pdf")
        return PluginsMime(
            plugins=[
                PluginEntry(
                    name="PDF Viewer",
                    filename="internal-pdf-viewer",
                    description="Portable Document Format",
                    mime_types=[pdf],
                )
            ],
            mime_types=[pdf],
        )

    def _build_permissions(self, deriver: SeedDeriver) -> Permissions:
        policies: dict[str, str] = {}
        # The realistic mix for a long-lived personal profile.
        policies["geolocation"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.6), ("prompt", 0.35), ("denied", 0.05)],
            context="perm.geolocation",
        )

        # The permission state has to hang together.  A profile that has
        # lived long enough to be a returning visitor has almost always
        # been asked for notifications at least once and has usually
        # accepted: a profile that denies *both* the location and the
        # notifications is the shape of a fresh disposable browser, and a
        # fleet of those is trivially groupable.  The notifications state
        # therefore follows the location one.
        if policies["geolocation"] == "denied":
            policies["notifications"] = deriver.derive_weighted_choice(
                Axis.NAVIGATOR,
                [("granted", 0.5), ("prompt", 0.4), ("denied", 0.1)],
                context="perm.notifications.denied-geo",
            )
        else:
            policies["notifications"] = deriver.derive_weighted_choice(
                Axis.NAVIGATOR,
                [("granted", 0.6), ("prompt", 0.32), ("denied", 0.08)],
                context="perm.notifications",
            )
        policies["camera"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("prompt", 0.7), ("denied", 0.3)],
            context="perm.camera",
        )
        policies["microphone"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("prompt", 0.7), ("denied", 0.3)],
            context="perm.microphone",
        )
        policies["clipboard-read"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.5), ("prompt", 0.4), ("denied", 0.1)],
            context="perm.clipboard-read",
        )
        policies["clipboard-write"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.85), ("prompt", 0.15)],
            context="perm.clipboard-write",
        )
        policies["midi"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            # Web MIDI is not implemented in Android Chrome at all, so the
            # permission is denied on virtually every device.  The small
            # remainder covers the WebView builds that carry a stub.
            [("denied", 0.97), ("prompt", 0.03)],
            context="perm.midi",
        )
        policies["persistent-storage"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.7), ("prompt", 0.3)],
            context="perm.persistent-storage",
        )
        # The motion sensors are reported by a phone that carries them,
        # so the permission is normally granted -- but a profile that has
        # switched the sensors off, or one that never asked, still reads
        # differently, and a fleet where all three are always granted is
        # a fleet that gives a vendor a free grouping.
        policies["accelerometer"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.85), ("prompt", 0.15)],
            context="perm.accelerometer",
        )
        policies["gyroscope"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.8), ("prompt", 0.2)],
            context="perm.gyroscope",
        )
        policies["magnetometer"] = deriver.derive_weighted_choice(
            Axis.NAVIGATOR,
            [("granted", 0.78), ("prompt", 0.22)],
            context="perm.magnetometer",
        )
        return Permissions(policies=policies)  # type: ignore[arg-type]

    def _build_orientation(self, deriver: SeedDeriver) -> Orientation:
        orientation_type = deriver.derive_choice(
            Axis.SCREEN_VIEWPORT,
            (
                "portrait-primary",
                "portrait-secondary",
                "landscape-primary",
                "landscape-secondary",
            ),
            context="orientation",
        )
        angle = {
            "portrait-primary": 0,
            "portrait-secondary": 180,
            "landscape-primary": 90,
            "landscape-secondary": 270,
        }[orientation_type]
        return Orientation(
            type=orientation_type,  # type: ignore[arg-type]
            angle=angle,  # type: ignore[arg-type]
            lock_supported=True,
        )

    def _build_viewport(self, deriver: SeedDeriver, screen: Screen) -> Viewport:
        # The browser chrome reduces the viewport by the status bar
        # height.  The chrome has to fit inside the *available* area: a
        # viewport taller than the area the browser says it has is a
        # contradiction a site sees by comparing the two numbers.
        chrome_height = min(
            deriver.derive_int(Axis.SCREEN_VIEWPORT, 48, 128, context="chrome"),
            max(0, screen.avail_height - 1),
        )

        # The safe area is the band the system bars and the camera
        # cutout take.  A phone with a punch hole reports a taller inset
        # than one without, and a flat panel reports none at all, so the
        # inset is drawn per unit rather than fixed.
        inset_top = deriver.derive_choice(
            Axis.SCREEN_VIEWPORT, (0, 24, 36, 48), context="safe-top"
        )
        inset_bottom = deriver.derive_choice(
            Axis.SCREEN_VIEWPORT, (0, 16, 24, 48), context="safe-bottom"
        )

        inner_width = screen.avail_width
        inner_height = screen.avail_height - chrome_height
        return Viewport(
            inner_width=inner_width,
            inner_height=inner_height,
            outer_width=inner_width,
            outer_height=inner_height + chrome_height,
            screen_x=0,
            screen_y=0,
            visual_viewport_scale=1.0,
            safe_area_insets={
                "top": inset_top,
                "right": 0,
                "bottom": inset_bottom,
                "left": 0,
            },  # type: ignore[arg-type]
            scrollbar_width=0,
        )

    def _build_media_queries(self, deriver: SeedDeriver) -> MediaQueries:
        return MediaQueries(
            prefers_color_scheme=deriver.derive_choice(
                Axis.SCREEN_VIEWPORT, ("light", "dark"), context="color-scheme"
            ),  # type: ignore[arg-type]
            prefers_reduced_motion=deriver.derive_weighted_choice(
                Axis.SCREEN_VIEWPORT,
                [("no-preference", 0.9), ("reduce", 0.1)],
                context="reduced-motion",
            ),  # type: ignore[arg-type]
            prefers_contrast="no-preference",
            forced_colors="none",
            pointer="coarse",
            any_pointer=["coarse"],
            hover="none",
            any_hover=["none"],
            color_gamut=deriver.derive_weighted_choice(
                Axis.SCREEN_VIEWPORT,
                [("srgb", 0.85), ("p3", 0.15)],
                context="color-gamut",
            ),  # type: ignore[arg-type]
            dynamic_range=deriver.derive_weighted_choice(
                Axis.SCREEN_VIEWPORT,
                [("standard", 0.9), ("high", 0.1)],
                context="dynamic-range",
            ),  # type: ignore[arg-type]
        )

    def _build_timezone(self, geo: GeoTarget) -> Timezone:
        offset = _zoneinfo_offset(geo.timezone)
        dst = _zoneinfo_dst(geo.timezone)
        return Timezone(
            iana_name=geo.timezone,
            offset_minutes=offset,
            dst_active=dst,
            abbreviation=_zoneinfo_abbreviation(geo.timezone),
        )

    def _build_locale(self, geo: GeoTarget) -> Locale:
        return Locale(
            primary=geo.locale,
            intl_locale=geo.locale,
            numbering_system="latn",
            calendar="gregory",
            hour_cycle=geo.hour_cycle,  # type: ignore[arg-type]
            currency=geo.currency,
        )

    def _build_geolocation(self, deriver: SeedDeriver, geo: GeoTarget) -> Geolocation:
        if not deriver.derive_bool(Axis.GEO_TIMEZONE, 0.45, context="geo.enabled"):
            return Geolocation(mode="prompt")

        # The coordinate belongs to the place the time zone names.
        #
        # The old implementation drew the latitude and the longitude
        # uniformly from the whole globe, so a profile that claimed
        # ``de-AT`` / ``Europe/Vienna`` reported a position in Central
        # Asia.  A site that maps the exit IP to Austria and then reads
        # the GPS coordinate sees two different countries, and the
        # profile is worse than an honest one.
        #
        # The coordinate is the city itself plus a small jitter: a real
        # person is not standing on the reference point of their city,
        # and the accuracy radius has to cover the jitter, or the two
        # contradict each other.
        from .geo_coordinates import coordinates_for

        base_lat, base_lng = coordinates_for(geo.timezone)
        # The jitter stays inside a few kilometres, which is what a
        # city-level geolocation fix looks like.
        jitter_km = deriver.derive_float(Axis.GEO_TIMEZONE, 0.0, 8.0, context="geo.jitter_km")
        lat_jitter = deriver.derive_float(
            Axis.GEO_TIMEZONE, -jitter_km, jitter_km, context="geo.lat_jitter"
        )
        lng_jitter = deriver.derive_float(
            Axis.GEO_TIMEZONE, -jitter_km, jitter_km, context="geo.lng_jitter"
        )
        # One degree of latitude is about 111 km; at the equator a
        # degree of longitude is the same, and it shrinks towards the
        # poles.
        latitude = base_lat + lat_jitter / 111.0
        longitude_scale = max(0.2, abs(_cos_degrees(base_lat)))
        longitude = base_lng + lng_jitter / (111.0 * longitude_scale)

        # The accuracy radius has to cover the jitter, otherwise the
        # reported fix is more precise than the position it describes.
        accuracy = round(
            max(
                deriver.derive_float(Axis.GEO_TIMEZONE, 50.0, 20000.0, "acc"),
                abs(jitter_km) * 1000.0 * 1.4,
            ),
            1,
        )

        return Geolocation(
            mode="allow_custom",
            latitude=round(latitude, 6),
            longitude=round(longitude, 6),
            accuracy=accuracy,
            altitude=None,
            altitude_accuracy=None,
            heading=None,
            speed=None,
            timestamp_jitter_ms=deriver.derive_int(
                Axis.GEO_TIMEZONE, 0, 3000, context="geo.jitter"
            ),
        )

    def _build_canvas(self, deriver: SeedDeriver) -> Canvas2DConfig:
        # The canvas salt is derived from the CANVAS_2D axis, which is
        # re-keyed by the nudge step.  That means the nudge stage of the
        # collision guard can move a canvas collision as well as a WebGL
        # one -- otherwise a canvas clash could never be resolved.
        salt = deriver.derive_bytes(Axis.CANVAS_2D, 16, context="salt").hex()

        # The noise strength has a floor.  A strength of zero leaves the
        # canvas untouched, and a strength near zero perturbs it so little
        # that the renderings of two different devices can still land on
        # the same bytes.  At a hundred million profiles the weakest tail
        # of the distribution is what decides how large the clusters are,
        # so the floor keeps every profile's canvas apart from every other.
        raw = deriver.derive_float(Axis.CANVAS_2D, 0.0, 1.0, context="strength")
        strength = round(min(max(raw, CANVAS_NOISE_FLOOR), 1.0), 6)

        return Canvas2DConfig(
            mode="noise",
            salt=salt,
            noise_strength=strength,
            persist_across_restarts=True,
            targets=["toDataURL", "toBlob", "getImageData"],
        )

    def _build_webgl(self, deriver: SeedDeriver, identity: DeviceIdentity) -> WebGLConfig:
        gpu = identity.gpu
        image_value = deriver.derive_webgl_image_value(gpu.unmasked_renderer)
        # The pixel-noise value only carries about 56 bits, which collides
        # at 100 million profiles with a probability of about 6.7 percent.
        # The draw surface therefore gets its own 128-bit per-unit salt,
        # which is what the native core keys the readback on.
        salt = deriver.derive_bytes(Axis.WEBGL_PIXEL, 16, context="salt").hex()
        return WebGLConfig(
            mode="noise",
            unmasked_vendor=gpu.unmasked_vendor,  # type: ignore[arg-type]
            unmasked_renderer=gpu.unmasked_renderer,
            vendor=gpu.vendor,
            renderer=gpu.renderer,
            version=gpu.version,
            shading_language_version=gpu.shading_language_version,
            image_noise=WebGLImageNoise(enabled=True, value=image_value),
            salt=salt,
            read_pixels_sync=True,
            extensions=WebGLExtensions(supported=list(_CHROMIUM_ANDROID_EXTENSIONS)),
            parameters=WebGLParameters(
                max_texture_size=gpu.max_texture_size,
                max_cube_map_texture_size=gpu.max_cube_map_texture_size,
                max_3d_texture_size=gpu.max_3d_texture_size,
                max_array_texture_layers=gpu.max_array_texture_layers,
                max_renderbuffer_size=gpu.max_renderbuffer_size,
                max_viewport_dims=list(gpu.max_viewport_dims),
                max_vertex_attribs=gpu.max_vertex_attribs,
                max_vertex_uniform_vectors=gpu.max_vertex_uniform_vectors,
                max_vertex_uniform_components=gpu.max_vertex_uniform_components,
                max_fragment_uniform_vectors=gpu.max_fragment_uniform_vectors,
                max_fragment_uniform_components=gpu.max_fragment_uniform_components,
                max_varying_vectors=gpu.max_varying_vectors,
                max_combined_texture_image_units=gpu.max_combined_texture_image_units,
                max_vertex_texture_image_units=gpu.max_vertex_texture_image_units,
                max_texture_image_units=gpu.max_texture_image_units,
                max_color_attachments=gpu.max_color_attachments,
                max_draw_buffers=gpu.max_draw_buffers,
                max_samples=gpu.max_samples,
                max_uniform_buffer_bindings=gpu.max_uniform_buffer_bindings,
                aliased_line_width_range=list(gpu.aliased_line_width_range),
                aliased_point_size_range=list(gpu.aliased_point_size_range),
                sample_buffers=gpu.sample_buffers,
                samples=gpu.samples,
                max_texture_max_anisotropy_ext=gpu.max_texture_max_anisotropy_ext,
            ),
            shader_precision=self._shader_precision(),
        )

    def _shader_precision(self) -> ShaderPrecision:
        """The precision table Chromium reports on Android GPUs."""
        high_float = ShaderPrecisionRange(range_min=127, range_max=127, precision=23)
        medium_float = ShaderPrecisionRange(range_min=15, range_max=15, precision=10)
        low_float = ShaderPrecisionRange(range_min=7, range_max=7, precision=8)
        high_int = ShaderPrecisionRange(range_min=31, range_max=31, precision=0)
        medium_int = ShaderPrecisionRange(range_min=15, range_max=15, precision=0)
        low_int = ShaderPrecisionRange(range_min=7, range_max=7, precision=0)
        return ShaderPrecision(
            vertex_shader={
                "LOW_FLOAT": low_float,
                "MEDIUM_FLOAT": medium_float,
                "HIGH_FLOAT": high_float,
                "LOW_INT": low_int,
                "MEDIUM_INT": medium_int,
                "HIGH_INT": high_int,
            },
            fragment_shader={
                "LOW_FLOAT": low_float,
                "MEDIUM_FLOAT": medium_float,
                "HIGH_FLOAT": high_float,
                "LOW_INT": low_int,
                "MEDIUM_INT": medium_int,
                "HIGH_INT": high_int,
            },
        )

    def _build_webgpu(self, deriver: SeedDeriver, identity: DeviceIdentity) -> WebGPUConfig:
        # WebGPU is not enabled on the majority of Android devices in the
        # wild; it stays off unless the seed selects it.
        enabled = deriver.derive_bool(Axis.WEBGPU, 0.25, context="enabled")
        adapter = WebGPUAdapter(
            vendor=identity.gpu.unmasked_vendor,
            architecture=identity.abi.gpu_architecture,
            device=identity.abi.soc_model,
            description=identity.gpu.unmasked_renderer,
            is_fallback_adapter=False,
        )
        # The adapter description and the limit table belong to the GPU
        # *model*, so they are shared by every unit that carries that GPU.
        # The salt identifies the individual unit within the family, which
        # keeps the WebGPU surface from linking two profiles that differ
        # everywhere else.
        salt = deriver.derive_bytes(Axis.WEBGPU, 16, context="salt").hex()

        if not enabled:
            return WebGPUConfig(
                enabled=False, adapter=adapter, features=[], limits={}, salt=salt
            )

        # An adapter that reports no limits and no features is not something
        # a real GPU produces: every WebGPU implementation exposes a limit
        # table.  The values below follow the device's GPU, so the adapter
        # agrees with the WebGL parameters of the same device.
        gpu = identity.gpu
        limits: dict[str, float] = {
            "maxTextureDimension1D": float(gpu.max_texture_size),
            "maxTextureDimension2D": float(gpu.max_texture_size),
            "maxTextureDimension3D": float(gpu.max_3d_texture_size),
            "maxTextureArrayLayers": float(gpu.max_array_texture_layers),
            "maxBindGroups": 4.0,
            "maxBindingsPerBindGroup": 1000.0,
            "maxDynamicUniformBuffersPerPipelineLayout": 8.0,
            "maxDynamicStorageBuffersPerPipelineLayout": 4.0,
            "maxSampledTexturesPerShaderStage": float(gpu.max_combined_texture_image_units),
            "maxSamplersPerShaderStage": 16.0,
            "maxStorageBuffersPerShaderStage": 8.0,
            "maxStorageTexturesPerShaderStage": 4.0,
            "maxUniformBuffersPerShaderStage": 12.0,
            "maxUniformBufferBindingSize": 65536.0,
            "maxStorageBufferBindingSize": 134217728.0,
            "minUniformBufferOffsetAlignment": 256.0,
            "minStorageBufferOffsetAlignment": 256.0,
            "maxVertexBuffers": 8.0,
            "maxBufferSize": 268435456.0,
            "maxVertexAttributes": float(gpu.max_vertex_attribs),
            "maxVertexBufferArrayStride": 2048.0,
            "maxInterStageShaderVariables": 16.0,
            "maxColorAttachments": float(gpu.max_color_attachments),
            "maxColorAttachmentBytesPerSample": 32.0,
            "maxComputeWorkgroupStorageSize": 16384.0,
            "maxComputeInvocationsPerWorkgroup": 256.0,
            "maxComputeWorkgroupSizeX": 256.0,
            "maxComputeWorkgroupSizeY": 256.0,
            "maxComputeWorkgroupSizeZ": 64.0,
            "maxComputeWorkgroupsPerDimension": 65535.0,
        }

        # The feature set is what a mobile GPU of this class supports.
        #
        # The texture compression formats are the part that matters: a
        # GPU cannot support both the desktop BC formats and the mobile
        # ASTC formats, and a site that reads ``adapter.features`` sees
        # the pair.  The formats therefore follow the GPU family: the
        # Adreno parts carry BC, the ARM and Imagination parts carry
        # ASTC and ETC2.
        renderer = gpu.unmasked_renderer.lower()
        is_desktop_class = "adreno" in renderer
        is_mobile_class = "mali" in renderer or "immortalis" in renderer

        features: list[str] = [
            "depth-clip-control",
            "depth32float-stencil8",
            "timestamp-query",
            "indirect-first-instance",
        ]
        if is_desktop_class:
            features.append("texture-compression-bc")
        elif is_mobile_class:
            features.append("texture-compression-astc")
            features.append("texture-compression-etc2")
        if deriver.derive_bool(Axis.WEBGPU, 0.5, context="shader-f16"):
            features.append("shader-f16")

        return WebGPUConfig(
            enabled=True,
            adapter=adapter,
            features=features,
            limits=limits,
            salt=salt,
        )

    def _build_audio(self, deriver: SeedDeriver) -> Audio:
        salt = deriver.derive_bytes(Axis.AUDIO, 16, context="salt").hex()
        sample_rate = deriver.derive_choice(Axis.AUDIO, (48000, 44100), context="sample-rate")
        # Chromium's Android audio latency buckets.
        base_latency = deriver.derive_choice(
            Axis.AUDIO,
            (0.005333333333333333, 0.01, 0.010666666666666666),
            context="base-latency",
        )
        output_latency = deriver.derive_choice(
            Axis.AUDIO,
            (0.0, 0.02, 0.04, 0.2),
            context="output-latency",
        )
        channel_count = deriver.derive_choice(Axis.AUDIO, (1, 2), context="channels")
        max_channel_count = 2 if channel_count == 2 else 2
        return Audio(
            mode="native_quantized",
            salt=salt,
            sample_rate=sample_rate,  # type: ignore[arg-type]
            base_latency=base_latency,
            output_latency=output_latency,
            channel_count=channel_count,
            max_channel_count=max_channel_count,
            respect_core_quantization=True,
        )

    def _build_rects(self, deriver: SeedDeriver) -> Rects:
        salt = deriver.derive_bytes(Axis.RECTS, 16, context="salt").hex()

        # The perturbation has to actually perturb something.  Drawing
        # every delta independently from ``[-1, 1]`` leaves all four of
        # them at zero about one time in eighty, and a profile whose
        # geometry is declared as perturbed but is not is a profile that
        # disagrees with itself.
        deltas: list[int] = []
        for context in ("dx", "dy", "dw", "dh"):
            deltas.append(deriver.derive_int(Axis.RECTS, -1, 1, context=context))
        if all(delta == 0 for delta in deltas):
            # Redraw the offsets until at least one of them moves.  The
            # loop is bounded by the draw counter, so it always ends.
            index = 0
            while all(delta == 0 for delta in deltas):
                index += 1
                deltas[index % 4] = deriver.derive_int(
                    Axis.RECTS, -1, 1, context=f"retry-{index}"
                )

        return Rects(
            mode="noise",
            salt=salt,
            delta_x=deltas[0],
            delta_y=deltas[1],
            delta_width=deltas[2],
            delta_height=deltas[3],
            targets=[
                "getBoundingClientRect",
                "getClientRects",
                "Range.getBoundingClientRect",
                "Range.getClientRects",
            ],
        )

    def _build_fonts(self, deriver: SeedDeriver, locale: str) -> Fonts:
        salt = deriver.derive_bytes(Axis.FONTS, 16, context="salt").hex()
        # The font set follows two independent things: the locale, which
        # decides which scripts the user can read, and the device tier,
        # which decides how much room the system partition leaves for the
        # optional families.  A budget phone and a flagship in the same
        # country report different font lists, and that difference is what
        # a script comparing this device with a device it saw yesterday
        # is looking at.
        available = fonts_for_device(
            locale,
            ANDROID_FONT_PRESET,
            ram_gb=self._device_ram_gb,
            year=self._device_year,
            release=self._device_release,
        )
        count = deriver.derive_int(Axis.FONTS, min(8, len(available)), len(available), "count")
        installed = list(deriver.derive_shuffled(Axis.FONTS, available, context="shuffle"))[:count]
        # Roboto and Noto Color Emoji are always present on Android.
        for mandatory in ("Roboto", "Noto Color Emoji"):
            if mandatory not in installed:
                installed.append(mandatory)
        return Fonts(
            mode="android_preset",
            installed=sorted(installed),
            salt=salt,
            preserve_metric_clusters=True,
        )

    def _build_media_devices(self, deriver: SeedDeriver) -> MediaDevices:
        salt = deriver.derive_bytes(Axis.MEDIA_DEVICES, 16, context="salt").hex()
        origin = deriver.derive_bytes(Axis.MEDIA_DEVICES, 8, context="origin").hex()

        # A handset that reports no microphone and no camera at all is
        # not a device anybody sells: the overwhelming majority of the
        # phones in the wild carry both.  The rare exceptions are kept
        # because they are real, but they are rare -- a fleet where a
        # quarter of the profiles have no camera is a fleet a vendor can
        # group on.
        has_audio_in = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.97, "audio-in")
        has_audio_out = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.98, "audio-out")
        has_video = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.92, "video-in")

        audio_inputs: list[AudioInputDevice] = []
        if has_audio_in:
            audio_inputs.append(
                AudioInputDevice(
                    device_id=f"audio_input_{origin}_1",
                    group_id=f"group_{origin}_1",
                    label="Default",
                )
            )
        audio_outputs: list[AudioOutputDevice] = []
        if has_audio_out:
            audio_outputs.append(
                AudioOutputDevice(
                    device_id=f"audio_output_{origin}_1",
                    group_id=f"group_{origin}_1",
                    label="Default",
                )
            )
        video_inputs: list[VideoInputDevice] = []
        if has_video:
            facing = deriver.derive_choice(
                Axis.MEDIA_DEVICES, ("environment", "user"), context="facing"
            )
            video_inputs.append(
                VideoInputDevice(
                    device_id=f"video_input_{origin}_1",
                    group_id=f"group_{origin}_1",
                    label="Camera",
                    facing_mode=facing,  # type: ignore[arg-type]
                )
            )
        return MediaDevices(
            mode="deterministic_mock",
            id_scope="per_profile_and_origin",
            salt=salt,
            audio_inputs=audio_inputs,
            audio_outputs=audio_outputs,
            video_inputs=video_inputs,
        )

    def _build_storage(self, deriver: SeedDeriver) -> StorageNetwork:
        # The per-unit salt of the storage surface.  The quota is one of
        # a handful of round numbers and the usage is a fraction of it,
        # so together they carry barely a dozen bits -- nowhere near
        # enough to tell a hundred million units apart.  The salt is
        # what carries the uniqueness.
        salt = deriver.derive_bytes(Axis.STORAGE_QUOTA, 16, context="salt").hex()

        quota = deriver.derive_choice(
            Axis.STORAGE_QUOTA,
            (
                3_221_225_472,
                6_442_450_944,
                12_884_901_888,
            25_769_803_776,
            ),
            context="quota",
        )
        usage = int(quota * deriver.derive_float(Axis.STORAGE_QUOTA, 0.01, 0.45, "usage"))
        connection = deriver.derive_weighted_choice(
            Axis.BATTERY_NETWORK,
            [("wifi", 0.75), ("cellular", 0.25)],
            context="connection-type",
        )

        # The effective connection type and the measured link have to
        # agree with each other.  A 3g link that reports 8 Mbit/s is a
        # contradiction any vendor can read straight off
        # ``navigator.connection``, so the effective type is drawn first
        # and the downlink and the round trip are drawn *inside* the band
        # that the type can actually deliver.
        #
        # A wifi connection never reports 2g or 3g: the effective type
        # describes the *link*, and a wifi link is a wifi link whatever
        # the speed.  On a cellular connection the type follows the
        # radio technology.
        if connection == "wifi":
            effective_type = "4g"
            downlink_low, downlink_high = 5.0, 40.0
            rtt_low, rtt_high = 10, 60
        else:
            effective_type = deriver.derive_weighted_choice(
                Axis.BATTERY_NETWORK,
                [("4g", 0.7), ("3g", 0.25), ("2g", 0.05)],
                context="effective-type",
            )
            if effective_type == "2g":
                downlink_low, downlink_high = 0.07, 0.7
                rtt_low, rtt_high = 300, 700
            elif effective_type == "3g":
                downlink_low, downlink_high = 0.75, 3.0
                rtt_low, rtt_high = 100, 300
            else:
                # A cellular 4g link tops out well below what a wifi
                # link can carry; the Network Information API reports
                # the *radio* speed on a mobile network.
                downlink_low, downlink_high = 3.0, 10.0
                rtt_low, rtt_high = 30, 120

        downlink = round(
            deriver.derive_float(
                Axis.BATTERY_NETWORK, downlink_low, downlink_high, "downlink"
            ),
            2,
        )
        rtt = deriver.derive_int(Axis.BATTERY_NETWORK, rtt_low, rtt_high, "rtt")

        return StorageNetwork(
            quota_bytes=quota,
            usage_bytes=usage,
            persisted_default=deriver.derive_bool(Axis.STORAGE_QUOTA, 0.6, "persisted"),
            effective_type=effective_type,  # type: ignore[arg-type]
            rtt=rtt,
            downlink=downlink,
            save_data=deriver.derive_bool(Axis.BATTERY_NETWORK, 0.1, "save-data"),
            connection_type=connection,  # type: ignore[arg-type]
            salt=salt,
        )

    def _build_battery(self, deriver: SeedDeriver) -> BatterySpeechGamepad:
        charging = deriver.derive_bool(Axis.BATTERY_NETWORK, 0.25, "charging")
        level = round(
            deriver.derive_float(
                Axis.BATTERY_NETWORK,
                0.75 if charging else 0.12,
                1.0 if charging else 0.95,
                "level",
            ),
            2,
        )
        battery = Battery(
            enabled=True,
            charging=charging,
            level=level,
            charging_time=(
                deriver.derive_int(Axis.BATTERY_NETWORK, 1800, 7200, "ct") if charging else 0
            ),
            discharging_time=(
                0 if charging else deriver.derive_int(Axis.BATTERY_NETWORK, 3600, 28800, "dt")
            ),
        )
        voices = [
            SpeechVoice(
                name="Google US English",
                lang="en-US",
                default=True,
                local_service=True,
                voice_uri="urn:google:voice:en-us",
            )
        ]
        gamepad = Gamepad(enabled=False, devices=[])
        return BatterySpeechGamepad(battery=battery, speech_voices=voices, gamepad=gamepad)

    def _build_sensors(self, deriver: SeedDeriver) -> Sensors:
        profile = deriver.derive_choice(
            Axis.SENSORS,
            ("stationary_micro_jitter", "walking_subtle"),
            context="motion",
        )
        jitter = 0.01 if profile == "stationary_micro_jitter" else 0.08

        # The gravity reading is not a constant.  Earth pulls at about
        # 9.81 m/s^2 everywhere, but a real phone's sensor never reports
        # exactly that: the reading drifts with the calibration of the
        # unit, and the drift is what a site could group on.  It is
        # therefore drawn per unit, inside the narrow band that a real
        # accelerometer can produce.
        gravity_z = round(
            9.80665 + deriver.derive_float(Axis.SENSORS, -0.04, 0.04, "gravity"),
            6,
        )

        # The linear acceleration is the part of the accelerometer signal
        # that is not gravity.  The two are related exactly the way a
        # real sensor reports them: the accelerometer reads the sum.
        linear_x = round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "lx"), 6)
        linear_y = round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "ly"), 6)
        linear_z = round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "lz"), 6)

        # The per-unit salt of the motion surface.  The readings above
        # carry only a few tens of bits -- the gravity reading is nearly
        # the same on every device on Earth -- so the salt is what makes
        # this surface able to tell a hundred million units apart.
        salt = deriver.derive_bytes(Axis.SENSORS, 16, context="salt").hex()

        return Sensors(
            enabled=True,
            # The sampling rate is a property of the unit's sensor hub,
            # not of the platform: 60 Hz is the commonest, but 100 Hz and
            # 200 Hz hubs ship too.  A fleet where every profile reports
            # 60 Hz is a fleet a vendor can group on for free.
            sampling_hz=deriver.derive_choice(Axis.SENSORS, (60, 100, 200), context="hz"),
            motion_profile=profile,  # type: ignore[arg-type]
            accelerometer=Vector3D(
                x=round(linear_x, 6),
                y=round(linear_y, 6),
                z=round(gravity_z + linear_z, 6),
            ),
            gyroscope=Vector3D(
                x=round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "gx"), 6),
                y=round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "gy"), 6),
                z=round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "gz"), 6),
            ),
            linear_acceleration=Vector3D(x=linear_x, y=linear_y, z=linear_z),
            gravity=Vector3D(x=0.0, y=0.0, z=gravity_z),
            salt=salt,
            absolute_orientation=AbsoluteOrientation(
                alpha=round(deriver.derive_float(Axis.SENSORS, 0.0, 360.0, "al"), 4),
                beta=round(deriver.derive_float(Axis.SENSORS, -180.0, 180.0, "be"), 4),
                gamma=round(deriver.derive_float(Axis.SENSORS, -90.0, 90.0, "ga"), 4),
            ),
            relative_orientation=RelativeOrientation(
                # The relative orientation is the device's attitude against
                # its own reference frame.  A phone lying flat on a desk
                # reports zero on all three axes, and that is by far the
                # commonest posture -- but it is not the only one, and a
                # fleet where every profile reads zero is a fleet a vendor
                # can group on for free.  The reading is therefore drawn
                # per unit around the flat posture.
                alpha=round(deriver.derive_float(Axis.SENSORS, 0.0, 5.0, "ral"), 4),
                beta=round(deriver.derive_float(Axis.SENSORS, -5.0, 5.0, "rbe"), 4),
                gamma=round(deriver.derive_float(Axis.SENSORS, -5.0, 5.0, "rga"), 4),
            ),
        )

    def _build_math_precision(self, deriver: SeedDeriver) -> MathPrecision:
        """Build the floating-point precision surface.

        ``Math.acos(0.5)`` returns a double whose last bits come from
        the C library of the platform, not from the mathematical
        definition.  On x86-64 with glibc the value is
        ``1.0471975511965979`` -- one ulp above the correctly rounded
        result -- and a site that compares the exact bit pattern against
        a table of known engines can tell a real browser from a DOM
        that was faked in JavaScript.

        The reference values here are the x86-64 ones.  The drift is
        drawn per unit so that a fleet does not report the very same
        last bits everywhere.

        The band has to satisfy two constraints at once, and they pull
        in opposite directions:

        * **Plausibility.**  A value that is off by more than a few ulp
          is a *different* engine, not a different unit, so the band
          cannot be wide.
        * **Capacity.**  One ulp of a double near 1.0 is 2.22e-16, so a
          band of one ulp holds exactly three distinct doubles.  A fleet
          of a hundred million profiles would collapse into three
          buckets here, and the axis would carry no entropy at all.

        The band is therefore sixteen ulps wide -- wide enough for
        roughly a hundred thousand distinct values per function, narrow
        enough that the result still round-trips to a value a real
        C library produces.  Six functions at sixteen ulps each give the
        axis about a hundred bits, which is what a fleet needs.
        """
        salt = deriver.derive_bytes(Axis.MATH_PRECISION, 16, context="salt").hex()

        # One ulp of a number near 1.0 is about 2.22e-16.  The band is
        # sixteen of them, so the reachable values are the ones a real
        # platform library differs by.
        ulp = 2.220446049250313e-16
        epsilon = ulp * 16

        def _drift(name: str) -> float:
            # A deterministic wobble per function, inside the band.  The
            # step is one ulp, so the value always lands on a double
            # that is reachable from the reference.
            steps = deriver.derive_int(Axis.MATH_PRECISION, -8, 8, name)
            return steps * ulp

        return MathPrecision(
            enabled=True,
            acos=1.0471975511965979 + _drift("acos"),
            asin=0.5235987755982989 + _drift("asin"),
            sinh=0.5210953054937474 + _drift("sinh"),
            cosh=1.1276259652063807 + _drift("cosh"),
            tanh=0.46211715726000974 + _drift("tanh"),
            atan=0.4636476090008061 + _drift("atan"),
            epsilon=epsilon,
            salt=salt,
        )

    def _build_heap_size_limit(self, deriver: SeedDeriver) -> HeapSizeLimit:
        """Build the ``performance.memory.jsHeapSizeLimit`` surface.

        Chromium reports the V8 heap ceiling, and the value comes from
        the physical memory of the machine, rounded down to a power of
        two.  A phone with 4 GB of RAM gets a 2 GB ceiling; a machine
        with 16 GB gets 4 GB.  Reporting the desktop default on a
        handset is a signal, and so is reporting the *same* ceiling on
        every unit of a large fleet.

        The tier is therefore drawn per unit from the set of ceilings a
        phone can actually have.
        """
        salt = deriver.derive_bytes(Axis.HEAP_SIZE, 16, context="salt").hex()

        # The ceilings Chromium derives from the physical memory.
        tiers = (2147483648, 4294967296, 8589934592)
        tier = deriver.derive_choice(Axis.HEAP_SIZE, tiers, context="tier")

        return HeapSizeLimit(
            enabled=True,
            js_heap_size_limit=tier,
            memory_tier_limit=tier,
            salt=salt,
        )

    def _build_media_decode(self, deriver: SeedDeriver) -> MediaDecode:
        """Build the media-decode support surface.

        ``MediaRecorder.isTypeSupported`` answers whether the platform
        can *decode* a container/codec pair, and the answer follows the
        media stack of the device.  Every Android build since the
        platform shipped the common H.264 and VP9 codecs, so the set is
        close to fixed -- but ThumbmarkJS still probes it, and a device
        that claims a codec its build cannot play gives itself away.

        The list is therefore the Android baseline.  Anything outside it
        falls through to the real engine, so an unusual codec still
        reports the truth.
        """
        salt = deriver.derive_bytes(Axis.MEDIA_DECODE, 16, context="salt").hex()

        return MediaDecode(
            enabled=True,
            supported=[
                'video/mp4;codecs="avc1.42E01E"',
                'video/mp4;codecs="avc1.4D401E"',
                'video/mp4;codecs="avc1.640028"',
                'video/mp4;codecs="avc1.42E01F"',
                'video/webm;codecs="vp8, vorbis"',
                'video/webm;codecs="vp9"',
                'video/webm;codecs="vp8"',
                'audio/webm;codecs="opus"',
                'audio/mp4;codecs="mp4a.40.2"',
                'audio/ogg;codecs="vorbis"',
            ],
            salt=salt,
        )

    def _build_webrtc(self, deriver: SeedDeriver, proxy: Proxy | None = None) -> WebRtc:
        """Build the WebRTC configuration for the profile.

        The configuration has to follow the profile's own proxy state.  A
        profile that is *not* proxied must look like an ordinary phone: if
        WebRTC were locked down to ``relay`` with the non-proxied UDP
        blocked, ``RTCPeerConnection`` would produce no usable candidate at
        all, which is a far louder bot signal than any IP leak -- real
        handsets expose host candidates and reach a STUN server over UDP.

        A profile that *is* proxied has to be strict: ``relay`` only, the
        non-proxied UDP blocked and the mDNS obfuscation on, so that no
        candidate ever carries the host's own address.
        """
        proxied = bool(proxy and proxy.enabled and not proxy.is_direct)
        if not proxied:
            return WebRtc(
                mode="direct",
                spoofed_public_ip_v4="",
                spoofed_public_ip_v6="",
                spoofed_local_ip_v4="",
                mdns_enabled=True,
                block_non_proxied_udp=False,
                ice_transport_policy="all",
            )
        return WebRtc(
            mode="proxy_only",
            spoofed_public_ip_v4="",
            spoofed_public_ip_v6="",
            spoofed_local_ip_v4="",
            mdns_enabled=True,
            block_non_proxied_udp=True,
            ice_transport_policy="relay",
        )

    def _build_headers(
        self,
        navigator: Navigator,
        uad: UserAgentData,
        geo: GeoTarget,
        caps: VersionCapabilities,
    ) -> HttpHeaders:
        base = geo.locale.split("-")[0]
        accept_language = f"{geo.locale},{base};q=0.9" if base != geo.locale else geo.locale
        sec_ch_ua = ", ".join(f'"{b.brand}";v="{b.version}"' for b in uad.brands)

        # The full version list only exists from Chromium 116 onwards.
        # Sending it on an older core claims a hint the core cannot produce.
        full_list = ""
        if caps.has_client_hint("Sec-CH-UA-Full-Version-List"):
            full_list = ", ".join(f'"{b.brand}";v="{b.version}"' for b in uad.full_version_list)

        # The architecture, the bitness and the model are high-entropy hints
        # as well; before Chromium 119 they are not sent at all.
        arch = uad.architecture if caps.has_client_hint("Sec-CH-UA-Arch") else ""
        bitness = uad.bitness if caps.has_client_hint("Sec-CH-UA-Bitness") else ""
        model = uad.model if caps.has_client_hint("Sec-CH-UA-Model") else ""

        return HttpHeaders(
            accept_language=accept_language,
            dnt=None,
            sec_ch_ua=sec_ch_ua,
            sec_ch_ua_mobile="?1",
            sec_ch_ua_platform='"Android"',
            sec_ch_ua_platform_version=uad.platform_version,
            sec_ch_ua_arch=arch,
            sec_ch_ua_bitness=bitness,
            sec_ch_ua_model=model,
            sec_ch_ua_full_version_list=full_list,
            custom_headers={},
        )


# --------------------------------------------------------------------------
# Zoneinfo helpers
# --------------------------------------------------------------------------


def _release_number(release: str) -> int:
    """The integer Android release number of a release string.

    ``"14"`` -> ``14``, ``"14.0.1"`` -> ``14``.  An unparsable release
    falls back to Android 13, the oldest release the catalogue covers.
    """

    try:
        return int(release.split(".")[0])
    except (ValueError, AttributeError):
        return 13


@lru_cache(maxsize=64)
def _chromium_band_for_android_release(release: str) -> tuple[int, int]:
    """The Chromium majors that shipped with an Android release.

    A profile that claims a browser version the operating system never
    shipped is a contradiction a vendor can read straight off the
    User-Agent.  The band is therefore not written down twice: it is
    *derived* from the version registry, which is the single source of
    truth for what each Chromium major looks like.

    The derivation inverts :func:`novaprofile.core.versions._android_for_major`:
    every supported major is asked which Android release it belongs to,
    and the majors of the requested release are collected.  For the
    shipped table that yields

    ==========  ===================
    Android      Chromium
    ==========  ===================
    13           100 .. 113
    14           114 .. 127
    15           128 .. 155
    ==========  ===================

    A release the registry does not know about inherits the band of the
    newest release that shipped at or below it, which is what a device
    running a newer Android would be carrying.
    """

    number = _release_number(release)

    # The release the registry would attribute a Chromium major to.  The
    # registry maps a major onto the *newest* Android release that shipped
    # with it, so the release of a known major is a good anchor.
    known = sorted(
        {
            _android_for_major(major)[0]
            for major in sorted(VERSION_TABLE)
        }
    )

    # The release this device claims: the newest known release that is not
    # newer than the claimed one.
    anchor = known[0]
    for candidate in known:
        if _release_number(candidate) <= number:
            anchor = candidate

    majors = [major for major in sorted(VERSION_TABLE) if _android_for_major(major)[0] == anchor]
    if not majors:
        # Defensive: the registry always attributes at least one major to
        # some release, so this cannot happen unless the table is emptied.
        return (MIN_SUPPORTED_MAJOR, MAX_SUPPORTED_MAJOR)

    return (majors[0], majors[-1])


def _pick_chrome_major(deriver: SeedDeriver, release: str) -> int:
    """Draw the Chromium major a profile claims, from the seed.

    The draw is a pure function of the profile's seed, so the same seed
    always yields the same browser version and the profile survives a
    restart with an identical fingerprint.

    The edges of the band are trimmed off first.  A phone that shipped
    with Android 14 is usually a few releases into its update cycle, not
    on the very first build, so the very first and the very last major of
    the band are the least plausible picks.  A band of three or fewer
    majors has no interior to draw from and is used whole.
    """

    low, high = _chromium_band_for_android_release(release)
    if high - low + 1 > _BAND_EDGE_TRIM_THRESHOLD:
        low += 1
        high -= 1

    span = high - low + 1
    if span <= 0:
        # A two-entry band trimmed down to nothing: use the band whole.
        low, high = _chromium_band_for_android_release(release)
        span = high - low + 1
    if span == 1:
        return low

    return low + deriver.derive_int(Axis.CLIENT_HINTS, 0, span - 1, context="chrome-major")


def _major_for_android_release(release: str) -> int:
    """The Chromium major that shipped with an Android release.

    This is the *representative* major of the band -- the one a caller
    that asks for "the version this build defaults to" gets.  It is not
    the version a generated profile draws: that is
    :func:`_pick_chrome_major`, which varies per profile.

    The mapping is the one the version registry uses:

    ==========  ===================
    Android      Chromium
    ==========  ===================
    13           100 .. 113
    14           114 .. 127
    15           128 .. 155
    ==========  ===================

    The middle of the band is chosen rather than its edges, because the
    edges are the ones a vendor checks against: a phone that shipped
    with Android 14 is usually a few releases into its update cycle, not
    on the very first build.
    """

    low, high = _chromium_band_for_android_release(release)
    return (low + high) // 2


def _architecture_for(abi: AndroidAbiSoc) -> str:
    """The ``Sec-CH-UA-Arch`` value that matches a device's ABI.

    Chromium reports the *architecture* of the CPU, not the ABI name: an
    ``arm64-v8a`` device reports ``arm``, an ``x86_64`` device reports
    ``x86``.
    """

    primary = abi.primary_abi.lower()
    if primary in {"x86_64", "x86"}:
        return "x86"
    return "arm"


@lru_cache(maxsize=512)
def _cos_degrees(degrees: float) -> float:
    """The cosine of an angle given in degrees."""

    return math.cos(math.radians(degrees))


def _zoneinfo(name: str):
    """Return the ``ZoneInfo`` of a timezone, or ``None``.

    The result is cached: ``ZoneInfo`` reads the timezone database through
    ``importlib.resources`` on every construction, which made the lookup the
    single most expensive step of profile generation.  The cache is bounded
    because the catalogue of timezones is small and fixed.
    """

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return None


#: Fixed reference instant for timezone facts.  Using the wall clock would
#: make a profile's offset/DST/abbreviation change with the run date, which
#: breaks the "same seed -> same profile" persistence guarantee.
_TZ_REFERENCE_INSTANT = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


@lru_cache(maxsize=512)
def _zoneinfo_offset(name: str) -> int:
    zone = _zoneinfo(name)
    if zone is None:
        return 0
    offset = _TZ_REFERENCE_INSTANT.astimezone(zone).utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0


@lru_cache(maxsize=512)
def _zoneinfo_dst(name: str) -> bool:
    zone = _zoneinfo(name)
    if zone is None:
        return False
    dst = _TZ_REFERENCE_INSTANT.astimezone(zone).dst()
    return bool(dst and dst.total_seconds() != 0)


@lru_cache(maxsize=512)
def _zoneinfo_abbreviation(name: str) -> str:
    zone = _zoneinfo(name)
    if zone is None:
        return "UTC"
    return _TZ_REFERENCE_INSTANT.astimezone(zone).tzname() or "UTC"


__all__ = [
    "ProfileGenerator",
    "GeoTarget",
    "LOCALE_PRESETS",
    "ANDROID_FONT_PRESET",
    "DEFAULT_BROWSER_MAJOR",
]
