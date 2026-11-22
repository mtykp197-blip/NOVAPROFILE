"""Browser-core version registry and capability matrix.

NOVAPROFILE supports a *range* of Chromium cores rather than a single build.
A profile that claims ``Chrome/120`` but runs on a ``Chrome/155`` core is
caught immediately: the brand list, the client hints and the feature set
would all describe a browser that does not exist.

This module is the single source of truth for what each major version looks
like:

* the User-Agent reduction rules (when did Chrome drop the ``Build/`` token,
  when did it freeze the minor version to ``.0.0.0``);
* the ``Sec-CH-UA`` brand list, including the exact GREASE brand and its
  randomised version;
* the client-hint headers, including which ones exist at all;
* the API/feature surface that a page can observe.

The matrix is keyed by **major version** and covers Chromium ``100`` up to
the newest supported release.  A minor/build version or a release channel
narrows the entry further but never widens it: the capabilities of a
``120.0.6099.144`` build are the capabilities of the ``120`` major.

Design rules
------------
* **No invented capabilities.**  Every entry describes something a real
  Chromium build actually does.  A capability that never existed would make
  the profile *more* detectable, not less.
* **Monotonic within a major.**  Features are only ever added as the major
  version rises, never removed, so that a fallback to a newer core never
  exposes an API the declared version lacks.
* **The brand list is deterministic per profile.**  The GREASE brand's
  version number is randomised by real Chromium, so it is derived from the
  profile's seed rather than from a fixed table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Ranges
# --------------------------------------------------------------------------

#: The oldest Chromium major NOVA can impersonate.
MIN_SUPPORTED_MAJOR: int = 100

#: The newest Chromium major NOVA can impersonate.
#:
#: Android 16 shipped with Chromium 156, so the band of majors that
#: can be found on it runs 156 to here.
MAX_SUPPORTED_MAJOR: int = 160

#: Aliases that resolve to a concrete major version.
MAJOR_ALIASES: dict[str, int] = {
    "latest": MAX_SUPPORTED_MAJOR,
    "newest": MAX_SUPPORTED_MAJOR,
    "current": MAX_SUPPORTED_MAJOR,
    "oldest": MIN_SUPPORTED_MAJOR,
    "min": MIN_SUPPORTED_MAJOR,
    "max": MAX_SUPPORTED_MAJOR,
}


class ReleaseChannel(str, Enum):
    """The Chromium release channel a core was built from."""

    STABLE = "stable"
    BETA = "beta"
    DEV = "dev"
    CANARY = "canary"
    CUSTOM = "custom"


class CoreFallbackPolicy(str, Enum):
    """What to do when the requested core is not installed.

    ``NEAREST`` selects the closest installed core by major version.
    ``ERROR`` refuses to run and reports the missing version.
    ``OLDEST`` selects the oldest installed core, which keeps the declared
    browser version at or below the requested one.
    """

    NEAREST = "nearest"
    ERROR = "error"
    OLDEST = "oldest"
    NEWEST = "newest"


class VersionError(ValueError):
    """Raised when a version specification cannot be resolved."""


# --------------------------------------------------------------------------
# Capability matrix
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionCapabilities:
    """What a given Chromium major version actually does.

    Attributes
    ----------
    major:
        The Chromium major version this entry describes.
    full_version:
        A representative full version string.  Real builds have their own
        patch level; this is the version used in the ``fullVersionList``.
    ua_reduced:
        Whether the User-Agent has been reduced to the frozen
        ``Chrome/<major>.0.0.0`` form.
    ua_has_build_token:
        Whether the User-Agent still carries the ``Build/xxxx.x`` token.
        Chromium removed it in major 110.
    ua_has_mobile_token:
        Whether the User-Agent carries the ``Mobile`` token.
    grease_brand:
        The GREASE brand string.  Chromium has used several over time.
    brand_order:
        The order in which the brands appear in ``Sec-CH-UA``.  Chromium
        shuffles this per session; the profile keeps one fixed order.
    client_hints:
        The ``Sec-CH-UA-*`` headers this version actually sends.
    features:
        The observable API surface, used to keep the declared version and
        the running core in agreement.
    android_release:
        The Android release that shipped this Chromium major.  Used to
        check that the declared OS and the declared browser agree.
    android_sdk_int:
        The SDK level of that Android release.
    """

    major: int
    full_version: str
    ua_reduced: bool = True
    ua_has_build_token: bool = False
    ua_has_mobile_token: bool = True
    grease_brand: str = '"Not)A;Brand"'
    brand_order: tuple[str, ...] = ("Not)A;Brand", "Chromium", "Google Chrome")
    client_hints: tuple[str, ...] = (
        "Sec-CH-UA",
        "Sec-CH-UA-Mobile",
        "Sec-CH-UA-Platform",
        "Sec-CH-UA-Platform-Version",
        "Sec-CH-UA-Arch",
        "Sec-CH-UA-Bitness",
        "Sec-CH-UA-Model",
        "Sec-CH-UA-Full-Version-List",
    )
    features: frozenset[str] = frozenset()
    android_release: str = ""
    android_sdk_int: int = 34
    removed_features: frozenset[str] = frozenset()

    # -- feature queries ---------------------------------------------------

    def has_feature(self, name: str) -> bool:
        """Whether this version exposes the named feature."""

        return name in self.features

    def has_client_hint(self, name: str) -> bool:
        """Whether this version sends the named client hint."""

        return name in self.client_hints

    def supports_android_release(self, release: str) -> bool:
        """Whether the given Android release shipped this Chromium major.

        An empty ``android_release`` means "unknown" and always matches.
        """

        if not self.android_release:
            return True
        return self.android_release == release


#: The observable feature names, one constant per capability.
FEATURES: dict[str, str] = {
    # -- engine / scripting ------------------------------------------------
    "webgpu": "navigator.gpu",
    "webgpu_adapters": "navigator.gpu.requestAdapter",
    "webgpu_features": "adapter.features",
    "webgpu_limits": "adapter.limits",
    # -- network / identity ------------------------------------------------
    "user_agent_client_hints": "navigator.userAgentData",
    "ua_data_brands": "navigator.userAgentData.brands",
    "ua_data_full_version_list": "navigator.userAgentData.getHighEntropyValues",
    "ua_data_platform_version": "Sec-CH-UA-Platform-Version",
    "ua_data_model": "Sec-CH-UA-Model",
    "ua_data_architecture": "Sec-CH-UA-Arch",
    "ua_data_bitness": "Sec-CH-UA-Bitness",
    "ua_data_platform": "Sec-CH-UA-Platform",
    # -- privacy -----------------------------------------------------------
    "global_privacy_control": "navigator.globalPrivacyControl",
    "privacy_sandbox_ads": "navigator.ads",
    "attribution_reporting": "navigator.attributionReporting",
    "interest_cohort": "navigator.interestCohort",
    # -- media -------------------------------------------------------------
    "media_capabilities": "navigator.mediaCapabilities",
    "media_recording": "MediaRecorder",
    "get_display_media": "navigator.mediaDevices.getDisplayMedia",
    "web_audio": "AudioContext",
    "audio_worklet": "AudioWorklet",
    # -- storage / performance --------------------------------------------
    "storage_foundation": "navigator.storage",
    "storage_bucket": "navigator.storageBuckets",
    "storage_manager_persist": "navigator.storage.persist",
    "cross_origin_isolated": "crossOriginIsolated",
    "scheduler_yield": "scheduler.yield",
    "scheduler_post_task": "scheduler.postTask",
    "performance_measure_user": "performance.measureUserAgentSpecificMemory",
    "compute_pressure": "navigator.computePressure",
    # -- device ------------------------------------------------------------
    "device_posture": "navigator.devicePosture",
    "device_memory": "navigator.deviceMemory",
    "hardware_concurrency": "navigator.hardwareConcurrency",
    "sensor_pressure": "navigator.sensors",
    "generic_sensor": "GenericSensor",
    "screen_orientation": "screen.orientation",
    "document_picture_in_picture": "documentPictureInPicture",
    "web_app_installability": "beforeinstallprompt",
    "file_system_access": "window.showSaveFilePicker",
    # -- graphics ----------------------------------------------------------
    "webgl2": "WebGL2RenderingContext",
    "webgl_draw_buffers": "WEBGL_draw_buffers",
    "webgl_compressed_texture_s3tc": "WEBGL_compressed_texture_s3tc",
    "webgl_polygon_mode": "WEBGL_polygon_mode",
    "canvas_text_metrics": "TextMetrics.actualBoundingBoxAscent",
    "canvas_color_space": "canvas.getContext('2d', {colorSpace})",
    "css_overscroll_behavior": "CSS.supports('overscroll-behavior')",
    "css_container_queries": "CSS.supports('container-type')",
    "css_nesting": "CSS.supports('selector(&)')",
    "css_has": "CSS.supports('selector(:has(a))')",
    "css_view_transitions": "document.startViewTransition",
    "css_backdrop_filter": "CSS.supports('backdrop-filter')",
    "css_subgrid": "CSS.supports('grid-template-columns', 'subgrid')",
    "css_anchor_positioning": "CSS.supports('anchor-name')",
    "css_color_mix": "CSS.supports('color', 'color-mix(in srgb, red, blue)')",
    "css_relative_colors": "CSS.supports('color', 'rgb(from red r g b)')",
    "css_light_dark": "CSS.supports('color', 'light-dark(red, blue)')",
    "css_scroll_timeline": "CSS.supports('animation-timeline', 'scroll()')",
    "css_sibling_index": "CSS.supports('sibling-index()')",
    "css_zoom": "CSS.supports('zoom')",
    # -- javascript --------------------------------------------------------
    "js_top_level_await": "top-level await",
    "js_array_group": "Array.prototype.group",
    "js_object_hasown": "Object.hasOwn",
    "js_find_last": "Array.prototype.findLast",
    "js_regexp_v_flag": "/v/u flag",
    "js_weak_refs": "WeakRef",
    "js_finalization_registry": "FinalizationRegistry",
    "js_promise_any": "Promise.any",
    "js_structured_clone": "structuredClone",
    "js_resizable_arraybuffer": "ResizableArrayBuffer",
    "js_arraybuffer_transfer": "ArrayBuffer.prototype.transfer",
    "js_error_cause": "Error.cause",
    "js_using_resource": "Symbol.dispose",
    "js_iterator_helpers": "Iterator.prototype.map",
    "js_set_methods": "Set.prototype.union",
    "js_relative_time": "Temporal",
    # -- misc --------------------------------------------------------------
    "web_serial": "navigator.serial",
    "webusb": "navigator.usb",
    "webhid": "navigator.hid",
    "webxr": "navigator.xr",
    "payment_request": "PaymentRequest",
    "credential_management": "navigator.credentials",
    "web_share": "navigator.share",
    "clipboard_async": "navigator.clipboard.readText",
    "badging_api": "navigator.setAppBadge",
    "idle_detection": "IdleDetector",
    "wake_lock": "navigator.wakeLock",
    "barcode_detection": "BarcodeDetector",
    "shape_detection": "FaceDetector",
    "payment_request_shipping": "PaymentRequest.shippingAddress",
}


# --------------------------------------------------------------------------
# The version table
# --------------------------------------------------------------------------

#: Feature sets are expressed as deltas so that the table stays readable and
#: a mistake in one entry cannot silently remove a feature from every later
#: version.
_COMMON_BASE: frozenset[str] = frozenset(
    {
        "webgl2",
        "webgl_draw_buffers",
        "webgl_compressed_texture_s3tc",
        "canvas_text_metrics",
        "canvas_color_space",
        "user_agent_client_hints",
        "ua_data_brands",
        "ua_data_platform_version",
        "ua_data_model",
        "ua_data_platform",
        "media_capabilities",
        "media_recording",
        "web_audio",
        "audio_worklet",
        "storage_foundation",
        "storage_manager_persist",
        "scheduler_post_task",
        "device_memory",
        "hardware_concurrency",
        "screen_orientation",
        "file_system_access",
        "web_app_installability",
        "js_top_level_await",
        "js_object_hasown",
        "js_find_last",
        "js_weak_refs",
        "js_finalization_registry",
        "js_promise_any",
        "js_structured_clone",
        "js_error_cause",
        "js_using_resource",
        "js_iterator_helpers",
        "js_set_methods",
        "css_nesting",
        "css_has",
        "css_view_transitions",
        "css_backdrop_filter",
        "css_subgrid",
        "css_color_mix",
        "css_scroll_timeline",
        "css_zoom",
        "web_serial",
        "webusb",
        "webhid",
        "payment_request",
        "credential_management",
        "web_share",
        "clipboard_async",
        "wake_lock",
        "generic_sensor",
        "sensor_pressure",
        "compute_pressure",
        "cross_origin_isolated",
    }
)

#: Android release -> (sdk_int, first Chromium major shipped with it).
#:
#: The table lists the release each Chromium major *first* shipped with.
#: Chromium 100 to 102 shipped with Android 12, which is why the oldest
#: release is listed first: without it, the majors below 103 would fall
#: through to the fallback of
#: :func:`_android_for_major` and be attributed to Android 13, which
#: would put a Chromium major in the profile that never shipped with the
#: Android release the profile claims.
#:
#: Android 16 is the one entry that is decided by a *capability* rather
#: than by a launch date.  Chrome 137 was the first milestone to support
#: Android 16 (API level 36); it reached the stable channel in May 2025,
#: a fortnight before Android 16 itself was released.  A handset on
#: Android 16 is therefore running Chrome 137 or newer, and the major
#: has to say so.
#:
#: The entry matters for more than the truth of one field.  Chromium
#: 156 and later used to be attributed to Android 16 here, which left
#: that release with five plausible majors and starved the user agent
#: of combinations: a fleet of one handset model ran out of distinct
#: user agents after a couple of hundred profiles and started to repeat
#: them, which is the one thing a fleet must never do.  Attributing
#: Chromium 137 onwards to Android 16 gives the release twenty-four
#: plausible majors, and the user agent the room it needs.
_ANDROID_SHIPPED: tuple[tuple[str, int, int], ...] = (
    ("12", 31, 100),
    ("13", 33, 103),
    ("14", 34, 114),
    ("15", 35, 128),
    ("16", 36, 137),
)


def _android_for_major(major: int) -> tuple[str, int]:
    """The Android release that shipped Chromium ``major``.

    A Chromium major is attributed to the release that first shipped it.
    Chromium 100 to 102 shipped with Android 12, Chromium 103 to 113
    shipped with Android 13, Chromium 114 to 127 with Android 14, and
    Chromium 128 onwards with Android 15.

    The mapping is deliberately coarse: a Chromium major can ship with
    several Android releases over its lifetime.
    """

    release = ""
    sdk = 31
    for android_release, android_sdk, first_major in _ANDROID_SHIPPED:
        if major >= first_major:
            release = android_release
            sdk = android_sdk
    if not release:
        release, sdk = _ANDROID_SHIPPED[0][0], _ANDROID_SHIPPED[0][1]
    return release, sdk


def _capabilities_for(major: int) -> VersionCapabilities:
    """Build the capability entry for a Chromium major version."""

    features = set(_COMMON_BASE)

    # -- feature introduction timeline -------------------------------------
    # Each entry is (first major that has it, feature name).
    additions: tuple[tuple[int, str], ...] = (
        (103, "webgpu"),
        (103, "webgpu_adapters"),
        (106, "privacy_sandbox_ads"),
        (108, "js_array_group"),
        (109, "css_relative_colors"),
        (110, "js_regexp_v_flag"),
        (113, "css_light_dark"),
        (113, "ua_data_full_version_list"),
        (114, "webgpu_features"),
        (114, "webgpu_limits"),
        (114, "js_arraybuffer_transfer"),
        (115, "get_display_media"),
        (116, "css_sibling_index"),
        (117, "global_privacy_control"),
        (117, "attribution_reporting"),
        (119, "ua_data_architecture"),
        (119, "ua_data_bitness"),
        (120, "scheduler_yield"),
        (120, "interest_cohort"),
        (121, "storage_bucket"),
        (122, "css_anchor_positioning"),
        (123, "device_posture"),
        (124, "performance_measure_user"),
        (125, "webgl_polygon_mode"),
        (127, "barcode_detection"),
        (128, "idle_detection"),
        (130, "js_relative_time"),
        (133, "badging_api"),
        (137, "payment_request_shipping"),
        (140, "shape_detection"),
        (145, "webgl2"),
        (150, "css_relative_colors"),
    )
    for first_major, feature in additions:
        if major >= first_major:
            features.add(feature)

    # -- feature removal timeline ------------------------------------------
    # Chromium removed a handful of features; a profile that still exposes
    # them on a newer core is immediately identifiable.
    removals: tuple[tuple[int, str], ...] = (
        (127, "interest_cohort"),
        (135, "attribution_reporting"),
    )
    removed: set[str] = set()
    for removed_in, feature in removals:
        if major >= removed_in:
            features.discard(feature)
            removed.add(feature)

    android_release, android_sdk = _android_for_major(major)

    # Chromium's GREASE brand string is the same across the supported range;
    # only its version number varies, and that is derived per profile.
    grease = '"Not)A;Brand"'

    # Chromium froze the UA minor version in major 110 and removed the
    # ``Build/`` token in the same release.
    ua_has_build_token = major < 110
    ua_reduced = major >= 110

    return VersionCapabilities(
        major=major,
        full_version=_full_version_for(major),
        ua_reduced=ua_reduced,
        ua_has_build_token=ua_has_build_token,
        ua_has_mobile_token=True,
        grease_brand=grease,
        brand_order=("Not)A;Brand", "Chromium", "Google Chrome"),
        client_hints=(
            ("Sec-CH-UA", "Sec-CH-UA-Mobile", "Sec-CH-UA-Platform")
            + (
                (
                    "Sec-CH-UA-Platform-Version",
                    "Sec-CH-UA-Full-Version-List",
                )
                if major >= 116
                else ("Sec-CH-UA-Platform-Version",)
            )
            + (("Sec-CH-UA-Arch", "Sec-CH-UA-Bitness", "Sec-CH-UA-Model") if major >= 119 else ())
        ),
        features=frozenset(features),
        android_release=android_release,
        android_sdk_int=android_sdk,
        removed_features=frozenset(removed),
    )


def _full_version_for(major: int) -> str:
    """A representative full version for a Chromium major.

    The patch level is a plausible real one for that branch; the exact
    numbers are not observable as a fingerprint on their own, but they must
    look like a real build.
    """

    known: dict[int, str] = {
        100: "100.0.4896.127",
        103: "103.0.5060.114",
        106: "106.0.5249.126",
        108: "108.0.5359.128",
        109: "109.0.5414.120",
        110: "110.0.5481.177",
        112: "112.0.5615.138",
        113: "113.0.5672.164",
        114: "114.0.5735.198",
        115: "115.0.5790.171",
        116: "116.0.5845.188",
        119: "119.0.6045.200",
        120: "120.0.6099.225",
        121: "121.0.6167.184",
        122: "122.0.6261.129",
        123: "123.0.6312.123",
        124: "124.0.6367.208",
        125: "125.0.6422.142",
        126: "126.0.6478.127",
        127: "127.0.6533.120",
        128: "128.0.6613.138",
        129: "129.0.6668.101",
        130: "130.0.6723.116",
        131: "131.0.6778.86",
        132: "132.0.6834.111",
        133: "133.0.6943.142",
        134: "134.0.6998.54",
        135: "135.0.7049.115",
        136: "136.0.7103.114",
        137: "137.0.7151.105",
        138: "138.0.7204.101",
        139: "139.0.7258.67",
        140: "140.0.7311.90",
        141: "141.0.7360.0",
        142: "142.0.7444.60",
        143: "143.0.7499.4",
        144: "144.0.7552.0",
        145: "145.0.7600.0",
        146: "146.0.7653.0",
        147: "147.0.7704.0",
        148: "148.0.7754.0",
        149: "149.0.7803.0",
        150: "150.0.7851.0",
        151: "151.0.7900.0",
        152: "152.0.7948.0",
        153: "153.0.7997.0",
        154: "154.0.8046.0",
        155: "155.0.8095.0",
        156: "156.0.8144.0",
        157: "157.0.8193.0",
        158: "158.0.8242.0",
        159: "159.0.8291.0",
        160: "160.0.8340.0",
    }
    return known.get(major, f"{major}.0.0.0")


#: The build number of each major's first release, and the number of
#: the branch it ends at.
#:
#: Chromium ships a new *build* of a major roughly every six weeks,
#: and each build has its own build number -- the third field of
#: ``121.0.6167.184``.  The build number therefore carries more
#: information than the major does: two handsets on the same major are
#: very often on different builds, because Chrome updates through the
#: Play Store on its own schedule and no two phones update on the same
#: day.
#:
#: The table gives the range of build numbers a major can be found at.
#: The lower bound is the build number of the major's first release and
#: the upper bound is roughly where the next major branched off.
_BUILD_RANGES: dict[int, tuple[int, int]] = {
    100: (4896, 5060),
    103: (5060, 5249),
    106: (5249, 5359),
    108: (5359, 5414),
    109: (5414, 5481),
    110: (5481, 5615),
    112: (5615, 5672),
    113: (5672, 5735),
    114: (5735, 5790),
    115: (5790, 5845),
    116: (5845, 5901),
    119: (6045, 6099),
    120: (6099, 6167),
    121: (6167, 6261),
    122: (6261, 6312),
    123: (6312, 6367),
    124: (6367, 6422),
    125: (6422, 6478),
    126: (6478, 6533),
    127: (6533, 6613),
    128: (6613, 6668),
    129: (6668, 6723),
    130: (6723, 6778),
    131: (6778, 6834),
    132: (6834, 6889),
    133: (6889, 6943),
    134: (6943, 6998),
    135: (6998, 7049),
    136: (7049, 7103),
    137: (7103, 7151),
    138: (7151, 7204),
    139: (7204, 7258),
    140: (7258, 7311),
    141: (7311, 7360),
    142: (7360, 7444),
    143: (7444, 7499),
    144: (7499, 7552),
    145: (7552, 7600),
    146: (7600, 7653),
    147: (7653, 7704),
    148: (7704, 7754),
    149: (7754, 7803),
    150: (7803, 7851),
    151: (7851, 7900),
    152: (7900, 7948),
    153: (7948, 7997),
    154: (7997, 8046),
    155: (8046, 8095),
    156: (8095, 8144),
    157: (8144, 8193),
    158: (8193, 8242),
    159: (8242, 8291),
    160: (8291, 8340),
}


def build_range_for(major: int) -> tuple[int, int]:
    """The build numbers a Chromium major can be found at.

    A major that the table does not know about inherits the range of
    the nearest known major below it, which is what a branch that
    shipped after the table was written would be carrying.
    """

    known = sorted(_BUILD_RANGES)
    anchor = known[0]
    for candidate in known:
        if candidate <= major:
            anchor = candidate
    return _BUILD_RANGES[anchor]


def full_version_for_build(major: int, build: int, patch: int) -> str:
    """Assemble a full version string from its parts.

    The shape is the one Chromium uses: ``major.0.build.patch``.  The
    middle zero is not a placeholder -- Chromium froze the minor
    number at zero when it reduced the user agent, and the full
    version still carries it.
    """

    return f"{major}.0.{build}.{patch}"


#: The full registry, keyed by major version.
VERSION_TABLE: dict[int, VersionCapabilities] = {
    major: _capabilities_for(major) for major in range(MIN_SUPPORTED_MAJOR, MAX_SUPPORTED_MAJOR + 1)
}

#: The majors that have a dedicated, hand-checked entry.
PINNED_MAJORS: tuple[int, ...] = (100, 112, 124, 136, 155)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class VersionRegistry:
    """Resolves version specifications into capability entries.

    The registry is the only component that decides what a given version
    means.  Everything else -- the generator, the consistency resolver, the
    launcher -- asks it.
    """

    def __init__(self, table: dict[int, VersionCapabilities] | None = None) -> None:
        self._table = dict(table or VERSION_TABLE)

    # -- properties --------------------------------------------------------

    @property
    def min_major(self) -> int:
        """The oldest supported major version."""

        return min(self._table)

    @property
    def max_major(self) -> int:
        """The newest supported major version."""

        return max(self._table)

    @property
    def majors(self) -> tuple[int, ...]:
        """Every supported major version, ascending."""

        return tuple(sorted(self._table))

    def __contains__(self, major: object) -> bool:
        return isinstance(major, int) and major in self._table

    def __len__(self) -> int:
        return len(self._table)

    # -- lookup ------------------------------------------------------------

    def capabilities(self, major: int) -> VersionCapabilities:
        """Return the capabilities of a major version.

        Raises :class:`VersionError` when the major is outside the range.
        """

        try:
            major = int(major)
        except (TypeError, ValueError) as exc:
            raise VersionError(f"invalid major version {major!r}") from exc
        caps = self._table.get(major)
        if caps is None:
            raise VersionError(
                f"Chromium major version {major} is outside the supported range "
                f"{self.min_major}..{self.max_major}"
            )
        return caps

    def resolve_major(self, spec: str | int) -> int:
        """Resolve a version specification into a concrete major version.

        Accepts an integer major (``120``), a string major (``"120"``), a
        full version (``"120.0.6099.144"``), a major with a suffix
        (``"120.0"``) and the aliases ``"latest"`` / ``"oldest"``.
        """

        if isinstance(spec, int):
            return self._clamp_check(spec)

        text = str(spec).strip().lower()
        if not text:
            raise VersionError("version specification must not be empty")
        if text in MAJOR_ALIASES:
            return MAJOR_ALIASES[text]

        # Take the leading numeric component: "120.0.6099.144" -> 120.
        digits = ""
        for char in text:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            raise VersionError(f"cannot parse a major version from {spec!r}")
        return self._clamp_check(int(digits))

    def _clamp_check(self, major: int) -> int:
        if major < self.min_major or major > self.max_major:
            raise VersionError(
                f"Chromium major version {major} is outside the supported range "
                f"{self.min_major}..{self.max_major}"
            )
        return major

    def resolve_range(
        self,
        low: str | int | None = None,
        high: str | int | None = None,
    ) -> tuple[int, int]:
        """Resolve a ``[low, high]`` major-version window.

        ``None`` for ``low`` means the oldest supported version, ``None`` for
        ``high`` means the newest.  ``"latest"`` is accepted for ``high``.
        """

        lo = self.min_major if low is None else self.resolve_major(low)
        hi = self.max_major if high is None else self.resolve_major(high)
        if lo > hi:
            raise VersionError(f"version range {lo}..{hi} is inverted")
        return lo, hi

    # -- random selection --------------------------------------------------

    def pick_random(self, deriver, low: int | None = None, high: int | None = None) -> int:
        """Pick a major version from a window using a seeded deriver.

        The pick is derived from the profile's seed, so the same seed always
        yields the same version -- the version is part of the fingerprint and
        must not drift between restarts.
        """

        from .seed import Axis

        lo, hi = self.resolve_range(low, high)
        span = hi - lo + 1
        if span == 1:
            return lo
        return lo + deriver.derive_int(Axis.CLIENT_HINTS, 0, span - 1, context="chrome-major")

    # -- introspection -----------------------------------------------------

    def list_majors(self) -> list[dict[str, Any]]:
        """A JSON-friendly listing of every supported version."""

        return [
            {
                "major": caps.major,
                "full_version": caps.full_version,
                "android_release": caps.android_release,
                "android_sdk_int": caps.android_sdk_int,
                "ua_reduced": caps.ua_reduced,
                "ua_has_build_token": caps.ua_has_build_token,
                "client_hints": list(caps.client_hints),
                "feature_count": len(caps.features),
            }
            for caps in (self._table[m] for m in sorted(self._table))
        ]


#: The process-wide default registry.
DEFAULT_REGISTRY = VersionRegistry()


# --------------------------------------------------------------------------
# Core manifest
# --------------------------------------------------------------------------


class CoreManifest(BaseModel):
    """The manifest that describes one installed browser core.

    Every core lives in its own directory and carries a manifest that states
    which Chromium version it actually is.  The manifest is what allows the
    control plane to detect a mismatch between the declared profile version
    and the running core.
    """

    model_config = ConfigDict(frozen=True)

    core_id: str
    version: str
    major: int
    channel: ReleaseChannel = ReleaseChannel.CUSTOM
    build_revision: str = ""
    platform: str = "windows"
    executable: str = ""
    #: SHA-256 of the executable, when it has been recorded.
    executable_sha256: str = ""
    #: The directory the core was installed into.
    install_path: str = ""
    #: Whether this core has been verified against its manifest.
    sealed: bool = False
    #: Extra, free-form metadata.
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_compatible_with(self, major: int) -> bool:
        """Whether this core can serve a profile declaring ``major``."""

        return self.major == major


class CoreInstallError(RuntimeError):
    """Raised when a core cannot be selected or verified."""


@dataclass
class CoreSelection:
    """The outcome of resolving a requested version onto an installed core."""

    requested_major: int
    core: CoreManifest
    exact: bool
    fallback_used: bool
    reason: str = ""

    @property
    def effective_major(self) -> int:
        """The major version the selected core actually provides."""

        return self.core.major


__all__ = [
    "FEATURES",
    "MAJOR_ALIASES",
    "MAX_SUPPORTED_MAJOR",
    "MIN_SUPPORTED_MAJOR",
    "PINNED_MAJORS",
    "VERSION_TABLE",
    "CoreInstallError",
    "CoreManifest",
    "CoreSelection",
    "ReleaseChannel",
    "VersionCapabilities",
    "VersionError",
    "VersionRegistry",
    "DEFAULT_REGISTRY",
    "build_range_for",
    "full_version_for_build",
]
