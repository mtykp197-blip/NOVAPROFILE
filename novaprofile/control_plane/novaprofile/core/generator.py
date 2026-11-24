"""Profile generation.

Turns a device preset plus a master seed into a complete, internally
consistent :class:`ProfileConfig`.  Every value that is not dictated by the
device catalog is derived from the seed, which makes generation
deterministic: the same preset and the same seed always produce the same
profile, and two different seeds never produce the same fingerprint.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from ..schema.hardware import (
    AndroidAbiSoc,
    AndroidBuild,
    Canvas2DConfig,
    CanvasReadbackTiming,
    GpuParameterSet,
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
    Tls,
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
    FontTimingShield,
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
from .devices import get_preset, get_preset_by_model, inferred_touch_points
from .font_catalog import (
    MANDATORY_FONTS,
    SCRIPT_FONTS,
    device_tier,
    fonts_for_device,
)
from .geo_catalog import GEO_CATALOGUE
from .gpu_params import tier_for
from .hardware_approximation import (
    approximate_device_memory,
    approximate_hardware_concurrency,
)
from .identity import DeviceIdentity, DeviceIdentityFactory, panel_space
from .seed import Axis, SeedDeriver, new_master_seed
from .tls_profiles import profile_for_major
from .versions import (
    DEFAULT_REGISTRY,
    MAX_SUPPORTED_MAJOR,
    MIN_SUPPORTED_MAJOR,
    VERSION_TABLE,
    VersionCapabilities,
    _android_for_major,
    build_range_for,
    full_version_for_build,
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

# --------------------------------------------------------------------------
# The speech synthesis voice catalogue
# --------------------------------------------------------------------------
#
# ``speechSynthesis.getVoices()`` is a fingerprinting surface, and a site
# that reads it learns three things at once: which languages the handset
# can *speak*, which locale the handset is *set* to, and how much room the
# device has for the optional TTS language packs.  Getting any one of the
# three wrong is enough to separate the profile from a real device.
#
# The rules below are taken from the Chromium source, not guessed:
#
# * ``content/public/android/java/src/org/chromium/content/browser/
#   TtsPlatformImpl.java`` builds the Android voice list by walking
#   ``Locale.getAvailableLocales()`` and keeping every locale that the
#   platform TTS engine reports as available.  The *name* of such a voice
#   is ``locale.getDisplayLanguage()`` plus, when the locale has a
#   country, ``" " + locale.getDisplayCountry()``; the *lang* is
#   ``locale.toString()``.  A Vietnamese handset therefore exposes a voice
#   named "Vietnamese Vietnam" and no Japanese voice at all, because the
#   Japanese TTS pack is not installed.
#
# * ``content/browser/speech/speech_synthesis_impl.cc`` copies that list
#   into the Blink binding.  It sets ``voice_uri`` to the *same string* as
#   ``name``, ``is_local_service`` to ``!voice.remote`` and ``is_default``
#   to ``(index == 0)`` -- the first voice of the list is the default one.
#
# * ``content/browser/speech/tts_win.cc`` shows the desktop rule for
#   comparison: the voice name is the SAPI token description ("Microsoft
#   David Desktop - English (United States)") and the lang is the LCID
#   resolved to a BCP-47 locale name.
#
# This project builds Android profiles, so the Android rule is the one
# that applies: a voice is a *locale*, named after the locale's display
# name in the device language.

#: The display name of every locale the catalogue can produce a voice for.
#:
#: The key is the locale as it appears in the geo catalogue (``vi-VN``),
#: the value is the pair Chromium would report for it: the display
#: language, and the display country when the locale names one.
#:
#: The names follow ``java.util.Locale.getDisplayLanguage()`` and
#: ``getDisplayCountry()`` with an English display locale, because that is
#: what an Android handset with the English system language reports.
#: Locales whose language is not in this table fall back to the bare
#: language subtag, which is what ``getDisplayLanguage()`` returns for a
#: language the platform has no translation for.
VOICE_DISPLAY_NAMES: dict[str, tuple[str, str]] = {
    # -- the languages a Vietnamese handset usually carries ---------------
    "vi-VN": ("Vietnamese", "Vietnam"),
    "vi": ("Vietnamese", ""),
    "en-US": ("English", "United States"),
    "en-GB": ("English", "United Kingdom"),
    "en-AU": ("English", "Australia"),
    "en-CA": ("English", "Canada"),
    "en-IN": ("English", "India"),
    "en-SG": ("English", "Singapore"),
    "en-NZ": ("English", "New Zealand"),
    "en-ZA": ("English", "South Africa"),
    "en-IE": ("English", "Ireland"),
    "en": ("English", ""),
    "fr-FR": ("French", "France"),
    "fr-CA": ("French", "Canada"),
    "fr-BE": ("French", "Belgium"),
    "fr-CH": ("French", "Switzerland"),
    "fr-DZ": ("French", "Algeria"),
    "fr-MA": ("French", "Morocco"),
    "fr-SN": ("French", "Senegal"),
    "fr-CI": ("French", "Cote d'Ivoire"),
    "fr": ("French", ""),
    "zh-CN": ("Chinese", "China"),
    "zh-TW": ("Chinese", "Taiwan"),
    "zh-HK": ("Chinese", "Hong Kong"),
    "zh": ("Chinese", ""),
    "ja-JP": ("Japanese", "Japan"),
    "ja": ("Japanese", ""),
    "ko-KR": ("Korean", "South Korea"),
    "ko-KP": ("Korea", "North Korea"),
    "ko": ("Korean", ""),
    "th-TH": ("Thai", "Thailand"),
    "th": ("Thai", ""),
    "km-KH": ("Khmer", "Cambodia"),
    "km": ("Khmer", ""),
    "lo-LA": ("Lao", "Laos"),
    "lo": ("Lao", ""),
    "my-MM": ("Burmese", "Myanmar"),
    "my": ("Burmese", ""),
    "id-ID": ("Indonesian", "Indonesia"),
    "id": ("Indonesian", ""),
    "ms-MY": ("Malay", "Malaysia"),
    "ms": ("Malay", ""),
    "de-DE": ("German", "Germany"),
    "de-CH": ("German", "Switzerland"),
    "de-AT": ("German", "Austria"),
    "de": ("German", ""),
    "ru-RU": ("Russian", "Russia"),
    "ru": ("Russian", ""),
    "es-ES": ("Spanish", "Spain"),
    "es-MX": ("Spanish", "Mexico"),
    "es-AR": ("Spanish", "Argentina"),
    "es-CL": ("Spanish", "Chile"),
    "es-PE": ("Spanish", "Peru"),
    "es-PR": ("Spanish", "Puerto Rico"),
    "es-US": ("Spanish", "United States"),
    "es": ("Spanish", ""),
    "it-IT": ("Italian", "Italy"),
    "it": ("Italian", ""),
    "pt-BR": ("Portuguese", "Brazil"),
    "pt-PT": ("Portuguese", "Portugal"),
    "pt": ("Portuguese", ""),
    "nl-NL": ("Dutch", "Netherlands"),
    "nl-BE": ("Dutch", "Belgium"),
    "nl": ("Dutch", ""),
    "pl-PL": ("Polish", "Poland"),
    "pl": ("Polish", ""),
    "cs-CZ": ("Czech", "Czechia"),
    "cs": ("Czech", ""),
    "sk-SK": ("Slovak", "Slovakia"),
    "sk": ("Slovak", ""),
    "hu-HU": ("Hungarian", "Hungary"),
    "hu": ("Hungarian", ""),
    "ro-RO": ("Romanian", "Romania"),
    "ro": ("Romanian", ""),
    "bg-BG": ("Bulgarian", "Bulgaria"),
    "bg": ("Bulgarian", ""),
    "el-GR": ("Greek", "Greece"),
    "el": ("Greek", ""),
    "tr-TR": ("Turkish", "Turkey"),
    "tr": ("Turkish", ""),
    "uk-UA": ("Ukrainian", "Ukraine"),
    "uk": ("Ukrainian", ""),
    "da-DK": ("Danish", "Denmark"),
    "da": ("Danish", ""),
    "sv-SE": ("Swedish", "Sweden"),
    "sv": ("Swedish", ""),
    "nb-NO": ("Norwegian Bokmal", "Norway"),
    "nb": ("Norwegian Bokmal", ""),
    "fi-FI": ("Finnish", "Finland"),
    "fi": ("Finnish", ""),
    "is-IS": ("Icelandic", "Iceland"),
    "is": ("Icelandic", ""),
    "ar-EG": ("Arabic", "Egypt"),
    "ar-SA": ("Arabic", "Saudi Arabia"),
    "ar-AE": ("Arabic", "United Arab Emirates"),
    "ar-IL": ("Arabic", "Israel"),
    "ar": ("Arabic", ""),
    "he-IL": ("Hebrew", "Israel"),
    "he": ("Hebrew", ""),
    "fa-IR": ("Persian", "Iran"),
    "fa": ("Persian", ""),
    "hi-IN": ("Hindi", "India"),
    "hi": ("Hindi", ""),
    "bn-IN": ("Bengali", "India"),
    "bn-BD": ("Bengali", "Bangladesh"),
    "bn": ("Bengali", ""),
    "ta-IN": ("Tamil", "India"),
    "ta": ("Tamil", ""),
    "te-IN": ("Telugu", "India"),
    "te": ("Telugu", ""),
    "mr-IN": ("Marathi", "India"),
    "mr": ("Marathi", ""),
    "gu-IN": ("Gujarati", "India"),
    "gu": ("Gujarati", ""),
    "ur-PK": ("Urdu", "Pakistan"),
    "ur": ("Urdu", ""),
    "ne-NP": ("Nepali", "Nepal"),
    "ne": ("Nepali", ""),
    "si-LK": ("Sinhala", "Sri Lanka"),
    "si": ("Sinhala", ""),
    "sw-KE": ("Swahili", "Kenya"),
    "sw": ("Swahili", ""),
    "am-ET": ("Amharic", "Ethiopia"),
    "am": ("Amharic", ""),
}

#: The number of TTS language packs a device of each tier carries.
#:
#: The tier is the same one the font catalogue uses (see
#: :func:`novaprofile.core.font_catalog.device_tier`): the RAM and the
#: model year decide how much room the system partition has, and the
#: optional TTS language packs are the first thing a manufacturer drops
#: when the partition is tight.  A budget handset ships with the system
#: language and English; a flagship carries a noticeably longer list.
#:
#: The number is a *cap*, not a constant.  The device's own locale always
#: gets a voice, and the remaining slots are filled from the locales the
#: device carries.
VOICE_TIER_BUDGET: int = 3
VOICE_TIER_MID: int = 5
VOICE_TIER_FLAGSHIP: int = 8

#: The locales that a device always carries a voice for, whatever its tier.
#:
#: The system language and English are the two that are never absent: the
#: system language because the TTS engine always ships it, and English
#: because the Google TTS engine bundles it on every device.
VOICE_ALWAYS_LOCALES: tuple[str, ...] = ("en",)

#: The TTS language packs that a device is likely to carry *in addition*
#: to its own system language, grouped by the region of the world the
#: handset lives in.
#:
#: Android installs the optional TTS language packs on demand, from the
#: Play Store or from the "Text-to-speech output" settings screen, and
#: the packs that get downloaded follow the region: a Vietnamese handset
#: that carries a second language carries Chinese or Japanese, because
#: that is the language of the trade it is in, while a Brazilian handset
#: carries Spanish or English.  A French handset carrying a Vietnamese
#: voice would be a device nobody owns.
#:
#: The value is the *pool* the extra voices are drawn from.  The tier
#: decides how many of them the device carries, so two devices of
#: different tiers draw from the same shelf and end up with different
#: lists.
#:
#: The pack locales are the regional forms, because that is the form the
#: engine reports when the regional pack is installed.
VOICE_PACK_POOL_BY_REGION: dict[str, tuple[str, ...]] = {
    # -- Vietnam and the rest of mainland South-East Asia ------------------
    "vn": (
        "vi-VN",
        "zh-CN",
        "zh-TW",
        "ja-JP",
        "ko-KR",
        "km-KH",
        "th-TH",
        "my-MM",
        "en-US",
    ),
    # -- the rest of South-East Asia --------------------------------------
    "sea": ("en-US", "en-GB", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH", "vi-VN", "ms-MY"),
    # -- East Asia --------------------------------------------------------
    "ea": ("en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "en-GB"),
    # -- South Asia -------------------------------------------------------
    "sa": (
        "en-US",
        "en-GB",
        "hi-IN",
        "bn-IN",
        "ta-IN",
        "te-IN",
        "mr-IN",
        "ur-PK",
        "ne-NP",
        "si-LK",
    ),
    # -- the Middle East and Central Asia ---------------------------------
    "me": ("en-US", "ar-EG", "ar-SA", "ar-AE", "he-IL", "fa-IR", "tr-TR", "ru-RU"),
    # -- Africa -----------------------------------------------------------
    "af": ("en-US", "en-GB", "en-ZA", "fr-FR", "ar-EG", "sw-KE", "am-ET"),
    # -- Western Europe ---------------------------------------------------
    "we": ("en-US", "en-GB", "fr-FR", "de-DE", "es-ES", "it-IT", "pt-PT", "nl-NL", "pl-PL"),
    # -- Eastern and Central Europe ---------------------------------------
    "ee": (
        "en-US",
        "en-GB",
        "ru-RU",
        "uk-UA",
        "pl-PL",
        "cs-CZ",
        "sk-SK",
        "hu-HU",
        "ro-RO",
        "bg-BG",
    ),
    # -- North America ----------------------------------------------------
    "na": ("en-US", "en-CA", "es-US", "es-MX", "fr-CA", "en-GB"),
    # -- Latin America ----------------------------------------------------
    "la": ("es-ES", "es-MX", "es-AR", "es-CL", "es-PE", "es-PR", "pt-BR", "en-US", "fr-FR"),
    # -- the English-speaking world ---------------------------------------
    "en": ("en-US", "en-GB", "en-AU", "en-CA", "en-IN", "en-SG", "en-NZ", "en-ZA", "en-IE"),
}

#: The region each country of the geo catalogue belongs to.
#:
#: The country code is the anchor: it is what the geo catalogue already
#: knows for every entry, and it is stable across the whole catalogue.
VOICE_REGION_BY_COUNTRY: dict[str, str] = {
    # -- Vietnam and the rest of mainland South-East Asia ------------------
    "VN": "vn",
    "KH": "sea",
    "LA": "sea",
    "MM": "sea",
    "TH": "sea",
    "ID": "sea",
    "MY": "sea",
    "PH": "sea",
    "SG": "sea",
    # -- East Asia --------------------------------------------------------
    "CN": "ea",
    "TW": "ea",
    "HK": "ea",
    "JP": "ea",
    "KR": "ea",
    "KP": "ea",
    "MN": "ea",
    # -- South Asia -------------------------------------------------------
    "IN": "sa",
    "BD": "sa",
    "PK": "sa",
    "NP": "sa",
    "LK": "sa",
    # -- the Middle East and Central Asia ---------------------------------
    "AE": "me",
    "SA": "me",
    "IL": "me",
    "IR": "me",
    "IQ": "me",
    "JO": "me",
    "LB": "me",
    "TR": "me",
    "QA": "me",
    "KZ": "me",
    "UZ": "me",
    # -- Africa -----------------------------------------------------------
    "ZA": "af",
    "EG": "af",
    "MA": "af",
    "DZ": "af",
    "SN": "af",
    "CI": "af",
    "NG": "af",
    "KE": "af",
    "ET": "af",
    # -- Western Europe ---------------------------------------------------
    "GB": "we",
    "IE": "we",
    "FR": "we",
    "DE": "we",
    "AT": "we",
    "CH": "we",
    "ES": "we",
    "PT": "we",
    "IT": "we",
    "NL": "we",
    "BE": "we",
    "LU": "we",
    "DK": "we",
    "SE": "we",
    "NO": "we",
    "FI": "we",
    "IS": "we",
    # -- Eastern and Central Europe ---------------------------------------
    "RU": "ee",
    "UA": "ee",
    "BY": "ee",
    "PL": "ee",
    "CZ": "ee",
    "SK": "ee",
    "HU": "ee",
    "RO": "ee",
    "BG": "ee",
    "GR": "ee",
    "HR": "ee",
    "RS": "ee",
    "LT": "ee",
    "LV": "ee",
    "EE": "ee",
    # -- North America ----------------------------------------------------
    "US": "na",
    "CA": "na",
    "MX": "la",
    # -- Latin America ----------------------------------------------------
    "BR": "la",
    "AR": "la",
    "CL": "la",
    "PE": "la",
    "PR": "la",
    "CO": "la",
    "VE": "la",
    "EC": "la",
    "BO": "la",
    "PY": "la",
    "UY": "la",
    "CR": "la",
    "PA": "la",
    "DO": "la",
    "GT": "la",
    "HN": "la",
    "SV": "la",
    "NI": "la",
    "CU": "la",
    "JM": "la",
    # -- the English-speaking world ---------------------------------------
    "AU": "en",
    "NZ": "en",
    "IE2": "en",
    "IN2": "en",
}

#: The region used when the country of the geo target is not in
#: :data:`VOICE_REGION_BY_COUNTRY`.  The Western European pool is the
#: safest fallback: it is the one whose packs are installed on the
#: largest number of devices.
VOICE_REGION_FALLBACK: str = "we"

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


#: The fallback used when a model is not in the catalogue at all.
#:
#: A handset of an unknown model reports five touch points, which is what
#: the overwhelming majority of the handsets in the field report.
_TOUCH_FALLBACK = None


def _memory_report_options(preset: Any) -> tuple[int, ...]:
    """The memory readings the hardware of ``preset`` is able to report.

    ``navigator.deviceMemory`` is not the physical memory: Chromium
    rounds it to the nearest power of two and clamps it to the platform
    range, which on Android is one through eight gigabytes.  A handset
    with six gigabytes reports four, and one with twelve reports eight.

    The range is therefore the set of the *rounded* values, so that the
    core draws a value the hardware could actually have reported -- and
    so that a site cannot catch the fleet reporting a memory size no
    handset of that model ever had.
    """

    raw = tuple(getattr(preset, "ram_options", ()) or ())
    if not raw:
        return (8,)
    rounded: list[int] = []
    for value in raw:
        reported = approximate_device_memory(int(value))
        if reported not in rounded:
            rounded.append(reported)
    return tuple(sorted(rounded))


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


def _voice_budget(tier: str) -> int:
    """Return the number of TTS language packs a device of ``tier`` carries."""

    return {
        "budget": VOICE_TIER_BUDGET,
        "mid": VOICE_TIER_MID,
        "flagship": VOICE_TIER_FLAGSHIP,
    }[tier]


def _voice(locale: str, index: int) -> SpeechVoice:
    """Return the voice of ``locale`` at position ``index`` of the list.

    Chromium sets ``voice_uri`` to the same string as ``name``
    (``speech_synthesis_impl.cc``) and marks the first voice of the
    list as the default one.
    """

    name = _voice_display_name(locale)
    return SpeechVoice(
        name=name,
        lang=locale,
        default=index == 0,
        local_service=True,
        voice_uri=name,
    )


def _voice_mandatory_locales(geo: GeoTarget) -> list[str]:
    """Return the locales a device carries whatever its tier.

    These are the packs the TTS engine is guaranteed to have: the system
    locale, the bare language of that locale, the locales the user has
    downloaded as the secondary ones of the geo target, and English.
    """

    selected: list[str] = [geo.locale]

    primary = geo.locale.split("-")[0]
    if primary != geo.locale:
        selected.append(primary)

    for secondary in geo.languages:
        if secondary != geo.locale and secondary != primary:
            selected.append(secondary)

    for always in VOICE_ALWAYS_LOCALES:
        if always not in selected:
            selected.append(always)

    return selected


def _select_voice_locales(
    geo: GeoTarget,
    tier: str,
    extra: Sequence[str] = (),
) -> list[str]:
    """Return the locales of the voice list, in the order of the engine.

    This is the single implementation of the selection rules; the
    generator and :func:`voices_for_device` both call it, so a test of
    the catalogue tests the list the generator really produces.

    The rules are:

    * the device's own locale comes first -- it is the pack the engine
      ships with and the voice it picks for the system language,
    * the bare language of that locale follows, because the engine
      reports the language-only variant when the language is installed
      but the regional pack is not,
    * the secondary locales of the geo target follow, because they are
      the packs the user has actually downloaded,
    * English follows, because the Google TTS engine bundles it on every
      device,
    * the extra slots are filled from the region pool, whose size the
      tier decides.

    ``extra`` names the packs the device carries beyond the ones the
    locale and the geo target imply.  The generator draws them from the
    seed; the catalogue seam passes them in.
    """

    budget = _voice_budget(tier)

    selected = _voice_mandatory_locales(geo)

    for pack in extra:
        if pack not in selected:
            selected.append(pack)

    # Trim to the tier's budget, but never drop the device's own
    # language: a handset that cannot speak its system language is not
    # a handset.
    selected = selected[: max(2, budget)]
    if geo.locale not in selected:
        selected[0] = geo.locale

    # The order of the list decides which voice is the default one, and
    # the default voice is the one the engine picks for the system
    # language.  The device's own locale therefore comes first, and the
    # rest keeps the catalogue order.
    return [geo.locale] + [loc for loc in selected if loc != geo.locale]


def voices_for_device(
    locale: str,
    *,
    country_code: str,
    ram_gb: int,
    year: int,
    languages: tuple[str, ...] = (),
) -> list[SpeechVoice]:
    """Return the voice list of a device with these properties.

    This is the deterministic seam of the voice catalogue: it takes the
    physical properties of the handset and returns exactly the list its
    TTS engine would expose.  The generator calls the same rules, so a
    test can check the tier rules without having to draw a seed.

    The parameters are the same ones the generator uses:

    * ``locale`` is the system locale of the device,
    * ``country_code`` decides the region the device lives in, and so
      the pool of the extra language packs,
    * ``ram_gb`` and ``year`` decide the tier, and so the number of the
      language packs,
    * ``languages`` are the secondary locales the user has downloaded.
    """

    tier = device_tier(ram_gb, year)
    budget = _voice_budget(tier)

    geo = GeoTarget(
        locale=locale,
        timezone="UTC",
        country_code=country_code,
        currency="USD",
        hour_cycle="h23",
        languages=tuple(languages) or (locale,),
    )

    # The extra slots are filled from the region pool.  The generator
    # draws them from the seed; the order here is the catalogue order,
    # which is enough to check the shape of the list.
    extra: list[str] = []
    for pack in _voice_pack_pool(geo):
        if len(extra) >= budget:
            break
        extra.append(pack)

    selected = _select_voice_locales(geo, tier, extra)
    return [_voice(loc, index) for index, loc in enumerate(selected)]


def _voice_pack_pool(geo: GeoTarget) -> tuple[str, ...]:
    """Return the TTS language packs a device in ``geo`` could carry.

    The pool is the one of the device's region, so the extra voices are
    the languages that are actually spoken around it.  The device's own
    locale is put first, because the pack of the system language is the
    one that is certainly installed.
    """

    region = VOICE_REGION_BY_COUNTRY.get(geo.country_code, VOICE_REGION_FALLBACK)
    pool = VOICE_PACK_POOL_BY_REGION.get(region, VOICE_PACK_POOL_BY_REGION["we"])

    ordered = [geo.locale] + [pack for pack in pool if pack != geo.locale]
    return tuple(ordered)


def _voice_display_name(locale: str) -> str:
    """Return the name Chromium gives the TTS voice of ``locale``.

    ``TtsPlatformImpl.java`` builds the name from
    ``locale.getDisplayLanguage()`` and, when the locale names a
    country, ``" " + locale.getDisplayCountry()``.  A locale that is not
    in :data:`VOICE_DISPLAY_NAMES` falls back to its language subtag,
    which is what ``getDisplayLanguage()`` returns for a language the
    platform has no translation for.
    """

    display = VOICE_DISPLAY_NAMES.get(locale)
    if display is not None:
        language, country = display
    else:
        language, country = locale.split("-")[0], ""
    if not country:
        return language
    return f"{language} {country}"


# --------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------


class ProfileGenerator:
    """Builds complete profiles from a preset and a seed."""

    def __init__(self, resolver=None, *, precise_geolocation: bool = True) -> None:
        from .consistency import ConsistencyResolver

        self._resolver = resolver or ConsistencyResolver()

        # Whether every profile carries a real coordinate.
        #
        # A geolocation fix is only a *permission* on a real handset --
        # the user has to grant it, and most do not -- so a draw that
        # leaves it out half the time is the honest one.  It is also the
        # one that breaks the fleet: the geographic anchor is what tells a
        # site that two profiles are one person, and a profile without a
        # coordinate only carries a timezone, so at a hundred million
        # profiles every profile of one timezone is one person to the
        # site.  The coordinate is drawn from the seed and is stable for
        # the lifetime of the profile, so it separates a hundred million
        # people who all live in the same city.
        self._precise_geolocation = precise_geolocation

        # The physical unit the profile is being built for.  These are set
        # at the start of :meth:`generate` and read by the builders that
        # need to know which device this is -- the font tier, for one.
        self._device_ram_gb = 4
        self._device_year = 2024
        self._device_release = "14"
        self._device_locale_primary = ""
        self._stratum: int | None = None
        self._geo_province: int | None = None

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
        stratum: int | None = None,
        geo_province: int | None = None,
    ) -> ProfileConfig:
        """Generate a complete profile.

        Parameters
        ----------
        stratum:
            Which unit of the device family this profile is.  Two
            profiles of the same model that draw from the same slice
            of the choice space land on the same browser major and the
            same chip count, and a site that reads those two readings
            groups them together.  The stratum puts each unit in its
            own slice, so the fleet of one model spreads across the
            whole choice space instead of clumping in it.

            ``None`` means the profile stands alone, and every choice
            is drawn independently -- the right behaviour for a single
            handset that is not part of a fleet.

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
            stratum=None if stratum is None else int(stratum),
        )
        deriver = seed_config.deriver()

        # The stratum is part of the identity of the unit, not a
        # drawing parameter, so it is folded into the generator's
        # state before anything is derived from it.
        self._stratum = seed_config.stratum

        # The province the profile is placed in, when the caller pins
        # one.  It is the collision guard's way of moving a profile off
        # a province that another profile already occupies.
        self._geo_province = geo_province

        geo_target = geo or self._pick_geo(deriver)

        identity = ProfileIdentity.create(
            name=name or f"{preset.model} #{seed_config.master_seed[:6]}",
            profile_id=profile_id,
        )

        # The device identity layer turns the family preset into a single
        # physical unit: its own serial, firmware build, bootloader, panel
        # and GPU.  Two profiles of the same model are therefore two
        # different devices as far as any observer can tell.
        # The RAM, the Android release and the model code of this
        # physical unit are drawn **first**, and together.
        #
        # A phone does not report a random amount of memory: it reports
        # the amount the model was sold with, and the model's list of
        # options is what the catalogue knows.  Drawing from a flat set
        # instead would let a 4 GB Redmi Note report 16 GB, and
        # `navigator.deviceMemory` is one of the cheapest things for a
        # script to compare between two visits.
        #
        # The value that reaches the page is *not* the physical memory.
        # Chromium rounds it to the nearest power of two and clamps it to
        # eight gigabytes on Android, so a 6 GB handset reports four and
        # a 12 GB handset reports eight.
        #
        # The three are walked through one mixed radix -- and so are the
        # panel and the density, because a site groups on the
        # *combination*: the resolution, the density, the system
        # version, the core count, the memory and the model, read out
        # of one telemetry record.  A memory drawn on its own, a
        # release drawn on its own and a model code drawn on its own
        # each land on their own slice while two units still land on
        # the same combination of slices; and two radices -- one for
        # the memory and the release, another for the panel -- fold
        # onto each other and put two units back on the same
        # combination.  One index over the whole space of the device
        # makes the combination repeat only when it is genuinely
        # exhausted.
        release_options = tuple(
            variant[0]
            for variant in (preset.release_variants or ((preset.release, preset.sdk_int),))
        )
        model_options = tuple(preset.model_variants or (preset.model,))
        panel_options, density_options = panel_space(preset)

        # The three narrow readings, and the spaces they are drawn from.
        #
        # The core count is a property of the chipset rather than of the
        # handset, and a handset is sold with more than one chipset, so
        # the catalogue carries the counts of the chipsets the model
        # shipped with.
        core_options = tuple(preset.cpu_options) or (8,)
        # The memory the *platform* reports is the dimension, not the
        # physical memory: the platform rounds it to a power of two and
        # clamps it at eight, so a line whose physical memory is eight,
        # twelve or sixteen reports eight for all of them.  Drawing the
        # physical memory would make the dimension look wide while every
        # unit of it reported the same number -- the platform's answer
        # is what a site reads.
        memory_report_options = _memory_report_options(preset)
        # The physical memory that goes with each reported value.  The
        # tier of the optional fonts follows the physical memory, so
        # the two have to be kept together.
        #
        # The *smallest* physical memory of each reported value is the
        # one kept: a line sold with three gigabytes reports two, and
        # the three is the smallest part that reports two.  The larger
        # parts report the same number, and the tier of the fonts
        # follows the part that was soldered on, so the smallest one is
        # the safe answer.
        physical_by_report: dict[int, int] = {}
        for value in preset.ram_options or (8,):
            reported = approximate_device_memory(int(value))
            physical = int(value)
            if reported not in physical_by_report or physical < physical_by_report[reported]:
                physical_by_report[reported] = physical
        touch_options = inferred_touch_points(
            get_preset_by_model(preset.model) or _TOUCH_FALLBACK
        )

        if self._stratum is not None:
            # The order of the dimensions decides how fast each one
            # changes.  The mixed radix folds the index through the
            # dimensions from the outside in, so the outermost
            # dimension changes with every unit and the innermost one
            # changes last.
            #
            # The release comes first.  It is the loudest reading on
            # the device -- it is in the user agent, in the platform
            # version of the client hints and in every header -- and it
            # is also the reading that *permits* the browser major, so
            # a family that only ever reports one release also only
            # ever reports the majors of that release.
            #
            # The panel and the density come next: they are the
            # readings with the most values, so they are the ones
            # that keep the *combination* apart for the longest.  The
            # order is not a matter of taste -- the mixed radix repeats
            # a combination only when the space in front of it is
            # exhausted, so the dimensions with the most values have
            # to come first or the combination repeats long before the
            # space is.
            #
            # The model code is drawn **inside** the same radix, as the
            # dimension nearest the core.  It changes slowly, and that
            # is right: a family bought in one country does carry one
            # code for a long time.  What has to differ between two
            # units is the *combination* -- and the combination is kept
            # apart by the release, the panel and the density.
            #
            # The three narrow readings -- the core count, the physical
            # memory and the number of touch points -- are dimensions of
            # the *same* radix.  They used to be drawn outside it, on
            # their own slices, and that was the hole in the fleet: two
            # units could each land inside their own slice and still
            # land on the same combination, and a site reads the
            # combination rather than the slices.
            #
            # The order of the dimensions is not a matter of taste.  The
            # mixed radix reaches its last dimension only after the
            # space in front of it is exhausted, so the dimensions with
            # the most values have to come first or the combination
            # repeats long before the space is.
            (
                release_index,
                panel_index,
                density_index,
                core_index,
                ram_index,
                touch_index,
                model_index,
            ) = deriver.derive_mixed_radix_indices(
                Axis.NAVIGATOR,
                (
                    len(release_options),
                    len(panel_options),
                    len(density_options),
                    len(core_options),
                    len(memory_report_options),
                    len(touch_options),
                    len(model_options),
                ),
                "device",
                self._stratum,
            )
            release_name = release_options[release_index]
            chosen_panel = panel_options[panel_index]
            chosen_density = density_options[density_index]
            model_code = model_options[model_index]
            reported_ram_gb = int(memory_report_options[ram_index])
            physical_ram_gb = physical_by_report.get(reported_ram_gb, reported_ram_gb)
            unit_cores = int(core_options[core_index])
            unit_touch = int(touch_options[touch_index])
        else:
            # The memory is drawn from the space the *platform* reports,
            # for the same reason it is a dimension of the radix above:
            # the platform's answer is what a site reads.  The physical
            # memory that goes with it is then looked up, because the
            # tier of the optional fonts follows the physical memory.
            reported_ram_gb = int(
                deriver.derive_choice(
                    Axis.NAVIGATOR, memory_report_options, context="memory"
                )
            )
            physical_ram_gb = physical_by_report.get(reported_ram_gb, reported_ram_gb)
            release_name = release_options[
                deriver.derive_int(
                    Axis.CLIENT_HINTS, 0, len(release_options) - 1, context="android-release"
                )
            ]
            model_code = model_options[
                deriver.derive_int(
                    Axis.NAVIGATOR, 0, len(model_options) - 1, context="model-code"
                )
            ]
            chosen_panel = panel_options[
                deriver.derive_int(
                    Axis.SCREEN_VIEWPORT, 0, len(panel_options) - 1, context="panel"
                )
            ]
            chosen_density = density_options[
                deriver.derive_int(
                    Axis.SCREEN_VIEWPORT,
                    0,
                    len(density_options) - 1,
                    context="panel_density",
                )
            ]
            unit_cores = int(
                deriver.derive_choice(Axis.NAVIGATOR, core_options, context="cores")
            )
            unit_touch = int(
                deriver.derive_choice(Axis.NAVIGATOR, touch_options, context="touch")
            )
        ram_gb = approximate_device_memory(physical_ram_gb)
        device = DeviceIdentityFactory(
            deriver,
            self._stratum,
            chosen_release=release_name,
            chosen_model=model_code,
            chosen_panel=chosen_panel,
            chosen_density=chosen_density,
        ).build(preset)
        build = device.build
        abi = preset.abi

        # The font tier follows the RAM and the model year, because those
        # two are what decide how much room the system partition leaves
        # for the optional font families.  The tier follows the *physical*
        # memory, because the fonts a handset ships with are decided by
        # the part that was soldered onto it.
        self._device_ram_gb = physical_ram_gb
        self._device_year = preset.year
        self._device_release = preset.release
        self._device_locale_primary = geo_target.locale.split("-")[0].lower()

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
            major = _pick_chrome_major(deriver, build.version_release, self._stratum)

        caps = DEFAULT_REGISTRY.capabilities(major)

        # The full version carries the *build* number, and the build
        # number is what separates two handsets on the same major.
        #
        # Chrome updates through the Play Store on its own schedule and
        # ships a new build of a major every six weeks or so, so two
        # phones on the same major are very often on different builds.
        # A profile that reports the registry's representative build
        # therefore tells a site that every profile of that major runs
        # the same build -- which no real fleet of phones does.
        #
        # The build number is drawn from the range the major actually
        # spans, so the version stays one a real device could be
        # running, and it is a function of the seed, so the profile
        # keeps the same one across restarts.
        profile_full = self._pick_full_version(deriver, caps)

        if browser_version is None:
            version = BrowserVersion(major=caps.major, full_version=profile_full)
        elif not browser_version.full_version:
            version = browser_version.model_copy(update={"full_version": profile_full})
        else:
            if browser_version.full_version.split(".")[0] != str(browser_version.major):
                raise ValueError(
                    f"full_version {browser_version.full_version} does not belong to "
                    f"major {browser_version.major}"
                )
            version = browser_version

        navigator = self._build_navigator(
            deriver,
            build,
            geo_target,
            caps,
            preset.cpu_options,
            ram_gb,
            # The memory the *platform* reports is the physical memory
            # rounded to a power of two and clamped to eight, so the
            # range the model can report is the set of those rounded
            # values.  A model with twelve gigabytes therefore reports
            # eight, and the range says eight.
            _memory_report_options(preset),
            inferred_touch_points(get_preset_by_model(build.model) or _TOUCH_FALLBACK),
            # The three narrow readings were drawn *above*, inside the
            # one radix that walks the whole space of the device.  They
            # are handed down rather than drawn again, because a second
            # draw would land on a different value than the radix did
            # and the combination would fall apart.
            chosen_cores=unit_cores,
            chosen_touch=unit_touch,
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
            battery_speech_gamepad=self._build_battery(deriver, geo_target),
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
            tls=self._build_tls(deriver, version.major),
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
        """Generate ``count`` independent profiles.

        The profiles are the units of one device family, so each of
        them is given its own stratum.  That is what keeps two handsets
        of the same model from reporting the same browser major and the
        same chip count, which is the reading a site takes first.
        """

        profiles: list[ProfileConfig] = []
        for index in range(count):
            profile = self.generate(
                preset_id,
                name=f"{name_prefix or preset_id} {index + 1:04d}",
                geo=geo,
                stratum=index,
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
        memory_options: tuple[int, ...] = (2, 4, 8),
        touch_options: tuple[int, ...] = (5,),
        chosen_cores: int | None = None,
        chosen_touch: int | None = None,
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
        # The three narrow readings are drawn *together*, as one
        # combination, and not one after the other.
        #
        # Drawn one after the other, each on its own, two units can each
        # land inside their own slice and still land on the same
        # *combination* -- and a site reads the combination.  It reads
        # the physical resolution, the density, the system version, the
        # core count, the memory and the model out of one telemetry
        # record, and two profiles that agree on all of them are one
        # device to that site, whatever their canvas and their seed
        # say.
        #
        # The mixed radix is the way to walk the space of the
        # combinations without repeating one.  The index is the
        # stratum, folded through the dimensions the way a number is
        # folded through its digits: dividing by the size of the
        # previous dimension folds the combinations onto each other, and
        # giving every dimension its own step misses the combinations
        # where two steps happen to agree.
        #
        # The stratum is the unit's place in the *fleet*, not in the
        # preset.  The place in the preset would repeat across the
        # catalogue -- the first unit of one model and the first unit of
        # another would both be stratum zero and would walk the same
        # first combination of their own dimensions, and their models
        # are the same model in the eyes of a site that only reads the
        # combination.
        # The values were drawn by the caller, inside the one radix that
        # walks the whole space of the device.  They are taken as they
        # are rather than drawn again: a second draw would land on a
        # different value than the radix did -- the draw counter is
        # part of the derivation, so the second call would answer
        # differently -- and the combination would fall apart.
        if chosen_cores is not None:
            cores = int(chosen_cores)
        else:
            cores = int(
                deriver.derive_choice(Axis.NAVIGATOR, preset_cpu_options, context="cores")
            )
        if chosen_touch is not None:
            touch_points = int(chosen_touch)
        else:
            touch_points = int(
                deriver.derive_choice(Axis.NAVIGATOR, touch_options, context="touch")
            )
        memory_gb = int(device_memory_gb)

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
            hardware_concurrency=approximate_hardware_concurrency(cores),
            # The *approximated* memory, not the physical one.  This is
            # the value Chromium would have produced on the same
            # hardware, and the native core hands it to the page
            # unchanged.
            device_memory=memory_gb,  # type: ignore[arg-type]
            # The touch-point count follows the digitiser of the
            # handset, not a coin flip: it is the number the platform
            # would answer through JNI on that physical unit.  A fleet
            # that mixed five and ten across otherwise identical
            # handsets would hand the site a free grouping key.
            max_touch_points=touch_points,  # type: ignore[arg-type]
            # The range the model could report.  The core draws the
            # reported value out of these lists, out of the profile's
            # own seed, so that two units of the same model differ from
            # one another without either of them reporting something
            # the hardware could not.
            #
            # The value above is one of the list's own members, so the
            # control plane and the browser agree about what the family
            # is able to report.
            hardware_concurrency_options=[int(value) for value in preset_cpu_options],
            device_memory_options=[int(value) for value in memory_options],
            max_touch_points_options=[int(value) for value in touch_options],
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

    def _pick_full_version(self, deriver: SeedDeriver, caps: VersionCapabilities) -> str:
        """Draw the full version -- the build number -- of this profile.

        The major is the loud reading; the build number is the quiet
        one.  It is what ``navigator.userAgentData``'s full version
        list carries and what the ``Sec-CH-UA-Full-Version-List``
        header carries, and it is what separates two handsets that run
        the same major.

        The draw is stratified for the same reason the browser major
        is: the build number of a major spans only a few dozen values
        -- a major lives about six months -- and an independent draw
        lets two handsets of one model land on the same build often
        enough to group them.
        """

        low, high = build_range_for(caps.major)
        # The patch level is the fourth field.  It changes with every
        # build of a branch, but it carries very little information:
        # a handful of values per build number.
        if self._stratum is not None:
            build = deriver.derive_stratified(
                Axis.CLIENT_HINTS, list(range(low, high + 1)), "chrome-build", self._stratum
            )
        else:
            build = deriver.derive_int(
                Axis.CLIENT_HINTS, low, high, context="chrome-build"
            )
        patch = deriver.derive_int(Axis.CLIENT_HINTS, 0, 220, context="chrome-patch")
        return full_version_for_build(caps.major, build, patch)

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

        # The full version of the browser, as the high-entropy hint
        # ``uaFullVersion`` carries it.
        #
        # The hint arrived in major 101, two years before the full
        # version *list* did, and the two are separate: a core that
        # answers ``uaFullVersion`` does not necessarily send the
        # ``Sec-CH-UA-Full-Version-List`` header.  The rule is the same
        # as for the other high-entropy hints -- before the major that
        # has it, the core reports nothing, and a profile that carries
        # one anyway is claiming a capability the running core does not
        # have.
        ua_full_version = (
            browser_full if caps.has_feature("ua_data_full_version") else ""
        )

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
            ua_full_version=ua_full_version,
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
        #
        # The branch for a denied location keeps the notifications
        # granted or pending, and never denied: the two denials
        # together are the shape of a disposable browser, and the
        # consistency resolver reads such a profile as atypical.
        if policies["geolocation"] == "denied":
            policies["notifications"] = deriver.derive_weighted_choice(
                Axis.NAVIGATOR,
                [("granted", 0.55), ("prompt", 0.45)],
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
        inset_top = deriver.derive_choice(Axis.SCREEN_VIEWPORT, (0, 24, 36, 48), context="safe-top")
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
        if not self._precise_geolocation and not deriver.derive_bool(
            Axis.GEO_TIMEZONE, 0.45, context="geo.enabled"
        ):
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
        from .geo_vietnam import (
            VIETNAM_COORDINATES,
            VIETNAM_LOCALE,
            VIETNAM_TIMEZONE,
        )

        if geo.timezone == VIETNAM_TIMEZONE:
            # A Vietnamese profile is placed in a *province*, not in the
            # middle of the reference point of Ho Chi Minh City.
            #
            # The fleet is large and the country is not: a hundred
            # million profiles cannot all live in one province, and a
            # site that sees them all in one province knows it is
            # looking at a farm.  The province is therefore drawn from
            # the table, and the jitter inside the province separates
            # two profiles that landed in the same one.
            #
            # The caller may pin the province -- that is how the
            # collision guard moves a profile off a province another
            # profile already occupies.
            if self._geo_province is None:
                province_index = deriver.derive_int(
                    Axis.GEO_TIMEZONE,
                    0,
                    len(VIETNAM_COORDINATES) - 1,
                    context="geo.province",
                )
            else:
                province_index = self._geo_province % len(VIETNAM_COORDINATES)
            _province, base_lat, base_lng = VIETNAM_COORDINATES[province_index]
            # The jitter is wider than in the generic path: a province
            # is a hundred kilometres across, so a fix that is precise
            # to the metre would contradict the province it claims.
            jitter_km = deriver.derive_float(
                Axis.GEO_TIMEZONE, 0.0, 45.0, context="geo.jitter_km"
            )
        else:
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

        # The readback timing.  The delay is drawn from its own axis so
        # that it is independent of the canvas noise: two profiles that
        # happen to draw the same noise strength still report different
        # readback times.
        #
        # The delay is *fixed* for the profile -- it is a constant of
        # the unit, not of the call -- so two reads of the same canvas
        # take the same time.  A defence that jittered per call would
        # be worse than no defence at all: a site that reads the canvas
        # twice and compares the two timings would find them unequal,
        # and that is a signal no real device gives.
        delay_ms = round(
            deriver.derive_float(Axis.CANVAS_TIMING, 0.5, 3.0, context="canvas_delay"),
            3,
        )
        read_pixels_delay_ms = round(
            deriver.derive_float(Axis.CANVAS_TIMING, 0.5, 3.0, context="read_pixels_delay"),
            3,
        )

        return Canvas2DConfig(
            mode="noise",
            salt=salt,
            noise_strength=strength,
            persist_across_restarts=True,
            targets=["toDataURL", "toBlob", "getImageData"],
            timing=CanvasReadbackTiming(
                enabled=True,
                delay_ms=delay_ms,
                read_pixels_delay_ms=read_pixels_delay_ms,
            ),
        )

    def _build_webgl(self, deriver: SeedDeriver, identity: DeviceIdentity) -> WebGLConfig:
        gpu = identity.gpu
        image_value = deriver.derive_webgl_image_value(gpu.unmasked_renderer)
        # The pixel-noise value only carries about 56 bits, which collides
        # at 100 million profiles with a probability of about 6.7 percent.
        # The draw surface therefore gets its own 128-bit per-unit salt,
        # which is what the native core keys the readback on.
        salt = deriver.derive_bytes(Axis.WEBGL_PIXEL, 16, context="salt").hex()

        # The parameter table.  The limits of a card are read off the
        # *host* machine, so a fleet built on one build server reports
        # one identical table -- and a site that reads a dozen of those
        # limits groups the whole fleet for free.
        #
        # The fix is not a random limit; it is a limit the model really
        # produces.  Every GPU model has a table of internally
        # consistent variants -- the stock one, the half-memory one,
        # and the high-memory one where the model has a SKU above its
        # catalogue entry -- and the unit draws one of them.  The whole
        # set moves together, because the memory that bounds the
        # texture bounds the cube map, the renderbuffer and the
        # viewport with it.
        #
        # The tier was drawn by the identity layer, so that the WebGPU
        # limit table -- which describes the same physical GPU -- gets
        # the same one.
        tier = tier_for(gpu, identity.gpu_tier)
        limits = tier.limits
        param_salt = deriver.derive_bytes(Axis.GPU_PARAMS, 16, context="salt").hex()

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
                max_texture_size=limits.max_texture_size,
                max_cube_map_texture_size=limits.max_cube_map_texture_size,
                max_3d_texture_size=limits.max_3d_texture_size,
                max_array_texture_layers=limits.max_array_texture_layers,
                max_renderbuffer_size=limits.max_renderbuffer_size,
                max_viewport_dims=list(limits.max_viewport_dims),
                max_vertex_attribs=limits.max_vertex_attribs,
                max_vertex_uniform_vectors=limits.max_vertex_uniform_vectors,
                max_vertex_uniform_components=limits.max_vertex_uniform_components,
                max_fragment_uniform_vectors=limits.max_fragment_uniform_vectors,
                max_fragment_uniform_components=limits.max_fragment_uniform_components,
                max_varying_vectors=limits.max_varying_vectors,
                max_combined_texture_image_units=limits.max_combined_texture_image_units,
                max_vertex_texture_image_units=limits.max_vertex_texture_image_units,
                max_texture_image_units=limits.max_texture_image_units,
                max_color_attachments=limits.max_color_attachments,
                max_draw_buffers=limits.max_draw_buffers,
                max_samples=limits.max_samples,
                max_uniform_buffer_bindings=limits.max_uniform_buffer_bindings,
                aliased_line_width_range=list(limits.aliased_line_width_range),
                aliased_point_size_range=list(limits.aliased_point_size_range),
                sample_buffers=limits.sample_buffers,
                samples=limits.samples,
                max_texture_max_anisotropy_ext=limits.max_texture_max_anisotropy_ext,
            ),
            shader_precision=self._shader_precision(),
            parameter_set=GpuParameterSet(
                tier=tier.index,
                source_renderer=gpu.unmasked_renderer,
                salt=param_salt,
            ),
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
            return WebGPUConfig(enabled=False, adapter=adapter, features=[], limits={}, salt=salt)

        # An adapter that reports no limits and no features is not something
        # a real GPU produces: every WebGPU implementation exposes a limit
        # table.  The values below follow the device's GPU, so the adapter
        # agrees with the WebGL parameters of the same device.
        #
        # The *tier* is the same one the WebGL builder applied: the two
        # surfaces describe the same physical card, and a site that
        # compares the WebGPU texture limit with the WebGL one and
        # finds them different has found a contradiction.
        gpu = tier_for(identity.gpu, identity.gpu_tier).limits
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
        # The perturbation of the rendered samples.  It has to be small
        # enough to stay inside the tolerance of the vendors that compare
        # a render against a reference render, and large enough to move the
        # last bits of the fingerprint, which is where it lives.
        noise_strength = deriver.derive_choice(
            Axis.AUDIO,
            (0.0, 0.25, 0.5, 0.75, 1.0),
            context="noise-strength",
        )
        return Audio(
            mode="native_quantized",
            salt=salt,
            sample_rate=sample_rate,  # type: ignore[arg-type]
            base_latency=base_latency,
            output_latency=output_latency,
            channel_count=channel_count,
            max_channel_count=max_channel_count,
            respect_core_quantization=True,
            noise_strength=noise_strength,
        )

    def _build_tls(self, deriver: SeedDeriver, major: int) -> Tls:
        """The TLS ClientHello of the profile.

        The handshake is produced by the network service, in C++, before
        any script runs.  It is therefore the one surface that a
        JavaScript-level defence cannot reach at all -- and the one that
        a TLS-fingerprinting site reads first.

        The shape of the handshake is taken from the table of the Chrome
        release the profile claims to be, so that the JA3 the site sees
        belongs to the same Chrome as the user agent string and the
        client hints.  A profile that claimed Chrome 131 but sent the
        handshake of Chrome 100 would disagree with itself in the two
        places that are cheapest to cross-check.

        The salt is the per-unit part.  Two units of the same Chrome
        release send the *same* ClientHello on a real device -- the
        handshake is a property of the build, not of the handset -- so
        the salt is what separates them here.  It is the axis that the
        collision guard watches.
        """
        salt = deriver.derive_bytes(Axis.TLS_HANDSHAKE, 16, context="salt").hex()
        table = profile_for_major(major)
        data = table.as_dict()
        return Tls(
            mode="spoof",
            salt=salt,
            **data,  # type: ignore[arg-type]
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
                deltas[index % 4] = deriver.derive_int(Axis.RECTS, -1, 1, context=f"retry-{index}")

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

        # A manufacturer's system image does not carry every optional
        # family the platform ships.  The vendor picks the subset that
        # fits the partition it ships, and a Google system update
        # prunes a family here and there.  Two handsets of the same
        # model bought from the same shop can therefore report
        # different font lists, and a fleet where the whole model
        # reports one list hands a site a grouping key for free.
        pruned = self._pruned_fonts(deriver, available)
        if pruned is not available:
            available = pruned

        count = deriver.derive_int(Axis.FONTS, min(8, len(available)), len(available), "count")
        installed = list(deriver.derive_shuffled(Axis.FONTS, available, context="shuffle"))[:count]
        # Roboto and Noto Color Emoji are always present on Android.
        for mandatory in ("Roboto", "Noto Color Emoji"):
            if mandatory not in installed:
                installed.append(mandatory)

        # The font timing shield.  The delay is drawn from its own
        # axis, so it is independent of the font list: two profiles
        # that ship the same families still report different probe
        # timings.
        #
        # The shield is what makes the timing *uniform*: every probe of
        # this profile costs the same, whether the family it asks for
        # is on the device or not.  A site that times a probe for a
        # family it knows is absent learns nothing from the time.
        probe_delay = round(
            deriver.derive_float(Axis.FONT_TIMING, 0.2, 2.0, context="font_probe_delay"),
            3,
        )
        measure_delay = round(
            deriver.derive_float(Axis.FONT_TIMING, 0.2, 2.0, context="font_measure_delay"),
            3,
        )

        return Fonts(
            mode="android_preset",
            installed=sorted(installed),
            salt=salt,
            preserve_metric_clusters=True,
            timing=FontTimingShield(
                enabled=True,
                delay_ms=probe_delay,
                measure_delay_ms=measure_delay,
            ),
        )

    def _pruned_fonts(self, deriver: SeedDeriver, available: list[str]) -> list[str]:
        """Drop the optional families this unit's system image does not ship.

        Only the families the *mandatory* set does not protect may go,
        and only a few of them, and only on a minority of the units.
        The shape of the result is the shape of a real handset: the
        system's own families and the user's script are always there,
        while the decorative extras come and go with the vendor's
        partition budget and with the age of the system image.
        """

        optional = [
            name
            for name in available
            if name not in MANDATORY_FONTS
            and name not in SCRIPT_FONTS.get(self._device_locale_primary, ())
        ]
        if len(optional) < 3:
            return available

        # Most units ship the full set; a minority has had a family or
        # two pruned from it.
        if not deriver.derive_bool(Axis.FONTS, 0.55, context="font.pruned"):
            return available

        drop_count = deriver.derive_int(Axis.FONTS, 1, min(4, len(optional) - 1), "font.drop_count")
        dropped = set(
            deriver.derive_shuffled(Axis.FONTS, optional, context="font.drop_pick")[:drop_count]
        )
        return [name for name in available if name not in dropped]

    def _build_media_devices(self, deriver: SeedDeriver) -> MediaDevices:
        salt = deriver.derive_bytes(Axis.MEDIA_DEVICES, 16, context="salt").hex()

        # A handset that reports no microphone and no camera at all is
        # not a device anybody sells: the overwhelming majority of the
        # phones in the wild carry both.  The rare exceptions are kept
        # because they are real, but they are rare -- a fleet where a
        # quarter of the profiles have no camera is a fleet a vendor can
        # group on.
        has_audio_in = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.97, "audio-in")
        has_audio_out = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.98, "audio-out")
        has_video = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.92, "video-in")

        # The three draws are independent, and their combination would
        # produce a handset with neither a microphone nor a camera: a
        # device that no shop sells.  The independent draw is kept for
        # the three cases that *are* real -- a tablet without a camera,
        # a handset without a front camera -- but the combination that
        # leaves the profile with no capture device at all is repaired
        # here, because the consistency resolver reads such a profile as
        # a contradiction and a site can group on it.
        if not has_audio_in and not has_video:
            has_audio_in = True

        # The device identifier is not a readable name.  Chromium salts
        # the origin and the device's own information and hashes the
        # result into thirty-two bytes, which reach the page as
        # sixty-four hex characters.  The rule is in
        # ``third_party/blink/common/mediastream/media_device_id.cc``:
        #
        #   constexpr size_t hash_size = 64;  // 32 bytes * 2 char/byte
        #   return std::ranges::all_of(device_id, [](const char& c) {
        #     return base::IsAsciiLower(c) || base::IsAsciiDigit(c);
        #   });
        #
        # So the identifier is exactly sixty-four characters of lower
        # case hex.  A profile that reports "video_input_abc_1" is not
        # a camera, it is a script that read the wrong thing.
        def make_device_id(kind: str, index: int) -> str:
            digest = deriver.derive_bytes(
                Axis.MEDIA_DEVICES, 32, context=f"device-id:{kind}:{index}"
            )
            return digest.hex()

        def make_group_id(kind: str) -> str:
            """The group of a device.

            A group is the physical unit: the front camera and the back
            camera of one handset are two devices in two *different*
            groups, because they are two separate sensors.  The
            microphone and the speaker of one handset share a group,
            because they are one unit.
            """
            digest = deriver.derive_bytes(Axis.MEDIA_DEVICES, 32, context=f"group-id:{kind}")
            return digest.hex()

        audio_inputs: list[AudioInputDevice] = []
        if has_audio_in:
            audio_inputs.append(
                AudioInputDevice(
                    device_id=make_device_id("audio-input", 0),
                    group_id=make_group_id("audio"),
                    label="Default",
                )
            )
        audio_outputs: list[AudioOutputDevice] = []
        if has_audio_out:
            audio_outputs.append(
                AudioOutputDevice(
                    device_id=make_device_id("audio-output", 0),
                    group_id=make_group_id("audio"),
                    label="Default",
                )
            )

        video_inputs: list[VideoInputDevice] = []
        if has_video:
            # The overwhelming majority of the handsets carry a rear
            # camera; most of them also carry a front one.  A handset
            # with a single camera is the cheap one, and it is the
            # exception rather than the rule.
            has_front_camera = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.88, "front-camera")
            has_rear_camera = deriver.derive_bool(Axis.MEDIA_DEVICES, 0.97, "rear-camera")

            index = 0
            if has_rear_camera:
                # The rear camera of a handset is the one with the
                # higher resolution, and the label Android gives it
                # names the facing direction.
                rear_width, rear_height = deriver.derive_choice(
                    Axis.MEDIA_DEVICES,
                    ((1920, 1080), (3840, 2160), (4000, 3000), (1920, 1440)),
                    context="rear-resolution",
                )
                video_inputs.append(
                    VideoInputDevice(
                        device_id=make_device_id("video-input", index),
                        group_id=make_group_id("rear-camera"),
                        label="camera2 0, facing back",
                        facing_mode="environment",
                        width=rear_width,
                        height=rear_height,
                        frame_rate=30.0,
                    )
                )
                index += 1

            if has_front_camera:
                # The front camera is always the lower resolution of the
                # two, and it is the one a video call uses.
                front_width, front_height = deriver.derive_choice(
                    Axis.MEDIA_DEVICES,
                    ((640, 480), (1280, 720), (1920, 1080)),
                    context="front-resolution",
                )
                facing = deriver.derive_choice(
                    Axis.MEDIA_DEVICES,
                    ("user", "left", "right"),
                    context="front-facing",
                )
                video_inputs.append(
                    VideoInputDevice(
                        device_id=make_device_id("video-input", index),
                        group_id=make_group_id("front-camera"),
                        label=f"camera2 {index}, facing {facing}",
                        facing_mode=facing,  # type: ignore[arg-type]
                        width=front_width,
                        height=front_height,
                        frame_rate=30.0,
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
            # The quotas a browser reports, in bytes.
            #
            # Every one of them fits in a signed 32-bit integer, and
            # that is not an accident: the browser core reads the
            # value out of a JSON document with a 32-bit number
            # reader, so a larger quota is not merely unusual, it
            # makes the whole profile unreadable -- the core refuses
            # to start and every surface falls back to the host's
            # values.
            #
            # The three gigabytes, the six gigabytes and the twelve
            # gigabytes are the sizes Chrome reports on a desktop, on
            # a tablet and on a phone with a large storage.
            (
                1_073_741_824,
                1_500_000_000,
                2_000_000_000,
                2_147_483_647,
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
            deriver.derive_float(Axis.BATTERY_NETWORK, downlink_low, downlink_high, "downlink"),
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

    def _build_battery(self, deriver: SeedDeriver, geo: GeoTarget) -> BatterySpeechGamepad:
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
        voices = self._build_speech_voices(deriver, geo)
        gamepad = Gamepad(enabled=False, devices=[])
        return BatterySpeechGamepad(battery=battery, speech_voices=voices, gamepad=gamepad)

    def _build_speech_voices(self, deriver: SeedDeriver, geo: GeoTarget) -> list[SpeechVoice]:
        """Build the voice list the device's TTS engine would expose.

        Three rules drive the list, and all three come from the Chromium
        source:

        * A voice is a *locale*.  ``TtsPlatformImpl.java`` walks the
          locales the platform TTS engine reports as available, so a
          handset that only has the Vietnamese and the English packs
          exposes exactly those two and nothing else.  The voice name is
          the locale's display name -- "Vietnamese Vietnam" -- and the
          ``lang`` is the locale itself.

        * ``speech_synthesis_impl.cc`` copies ``voice_uri`` from the voice
          *name*, so the two are the same string, and it marks the first
          voice of the list as the default one.

        * The *number* of voices follows the device tier.  The optional TTS
          language packs live in the system partition, so a budget
          handset carries a couple of them and a flagship carries a
          noticeably longer list.  Two devices of the same country and
          different tiers therefore expose different voice lists, and
          that difference is what a fingerprinting script compares when it
          asks whether this is the device that registered an account
          yesterday.
        """

        tier = device_tier(self._device_ram_gb, self._device_year)
        budget = _voice_budget(tier)

        # The locales the handset carries, in the order the engine reports
        # them.
        #
        # The device's own locale always comes first: the system language
        # is the pack the TTS engine ships with, and it is the voice the
        # engine picks for the system language.  The bare language of
        # that locale follows, because the engine reports the
        # language-only variant when the language is installed but the
        # regional pack is not.
        #
        # The secondary locales of the geo target are the packs a user in
        # that country has actually downloaded -- a Vietnamese handset
        # with a Chinese secondary locale really does carry the Chinese
        # TTS pack.  English is the one pack the Google TTS engine
        # bundles on every device, so it is present whatever the locale
        # and whatever the tier.
        #
        # The remaining slots are filled from the pool of the language
        # packs the device carries.  The pool is the one of the device's
        # own region, so the extra voices are the languages that are
        # actually spoken around it -- a Vietnamese handset gets Chinese
        # and Japanese, not Swedish.  Which ones it carries is drawn from
        # the seed, but the *number* of them is fixed by the tier, so the
        # count is the part a script can compare between two devices of
        # the same country.
        extra_slots = budget - len(_voice_mandatory_locales(geo))
        extra: list[str] = []
        if extra_slots > 0:
            pool = [
                pack for pack in _voice_pack_pool(geo) if pack not in _voice_mandatory_locales(geo)
            ]
            if pool:
                extra = deriver.derive_shuffled(Axis.BATTERY_NETWORK, pool, context="voice-packs")[
                    :extra_slots
                ]

        selected = _select_voice_locales(geo, tier, extra)
        return [_voice(loc, index) for index, loc in enumerate(selected)]

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
        #
        # Every ceiling fits in a signed 32-bit integer, and that is
        # not an accident: the browser core reads the value out of a
        # JSON document with a 32-bit number reader, so a larger
        # ceiling is not merely unusual, it makes the whole profile
        # unreadable -- the core refuses to start and every surface
        # falls back to the host's values.
        #
        # A phone with 4 GB of RAM gets a 2 GB ceiling; a machine with
        # 16 GB gets 4 GB.
        tiers = (536_870_912, 1_073_741_824, 2_000_000_000)
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
    """The Chromium majors a device on this Android release can be running.

    A profile that claims a browser version the operating system never
    shipped is a contradiction a vendor can read straight off the
    User-Agent.  The band is therefore not written down twice: it is
    *derived* from the version registry, which is the single source of
    truth for what each Chromium major looks like.

    The band is **open at the top**.  The first Chromium major that
    shipped with a release is the oldest one the release can be found
    running; the newest is the newest major that exists.  That is what
    a real fleet looks like: a handset on Android 14 is running the
    Chrome it shipped with, or a version a few updates later, or the
    one from this month -- Chrome updates through the Play Store on its
    own schedule and is not held back by the operating system.

    The closed band -- one major per release -- was the wrong shape.
    It said that every handset on Android 14 runs one of fourteen
    browsers, which is not how the fleet is distributed, and it made
    the family of one model run out of browser versions long before
    it ran out of handsets.

    For the shipped table the bands are

    ==========  ==========================
    Android      Chromium
    ==========  ==========================
    13           103 .. 160
    14           114 .. 160
    15           128 .. 160
    16           156 .. 160
    ==========  ==========================

    A release the registry does not know about inherits the band of the
    newest release that shipped at or below it, which is what a device
    running a newer Android would be carrying.
    """

    number = _release_number(release)

    # The release the registry would attribute a Chromium major to.  The
    # registry maps a major onto the *newest* Android release that shipped
    # with it, so the release of a known major is a good anchor.
    known = sorted({_android_for_major(major)[0] for major in sorted(VERSION_TABLE)})

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

    # The oldest major the release shipped with, and the newest major
    # that exists.
    return (majors[0], MAX_SUPPORTED_MAJOR)


def _pick_chrome_major(deriver: SeedDeriver, release: str, stratum: int | None = None) -> int:
    """Draw the Chromium major a profile claims, from the seed.

    The draw is a pure function of the profile's seed, so the same seed
    always yields the same browser version and the profile survives a
    restart with an identical fingerprint.

    The edges of the band are trimmed off first.  A phone that shipped
    with Android 14 is usually a few releases into its update cycle, not
    on the very first build, so the very first and the very last major of
    the band are the least plausible picks.  A band of three or fewer
    majors has no interior to draw from and is used whole.

    The draw is *stratified* by the unit's place in the family.  The
    browser major is the loudest reading on the device -- it is in the
    user agent, in the client hints and in every header -- and an
    independent draw lets two handsets of the same model report the
    same one often enough for a site to group them on it.  Giving each
    unit its own slice of the band spreads the family over the band
    instead of clumping in it.
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

    if stratum is not None:
        return deriver.derive_stratified(
            Axis.CLIENT_HINTS,
            list(range(low, high + 1)),
            "chrome-major",
            stratum,
        )
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
