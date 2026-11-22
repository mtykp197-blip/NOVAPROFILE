"""The surfaces a registration page can read from the browser.

A registration page that wants to know whether two visits come from
the same device reads a handful of things.  Some of them are typed
into a form; most of them are read silently from the browser by a
script, and the script sends them to the server along with the form.

This module is the list of those surfaces.  It is written so that the
list can be used two ways: to check a profile against it, and to write
a test that reads every surface from a real browser and reports what
it found.

The list is a description of what the page may do, not a claim about
what any particular page does.  The measurement decides.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Surface:
    """One thing a page can learn about the visitor."""

    #: The name of the surface.
    name: str
    #: The browser API the surface is read through.
    api: str
    #: The category the surface falls into.
    category: str
    #: Whether the surface is stable for a device.
    stable: bool
    #: How much the surface narrows the field down.
    #: "none", "weak", "medium" or "strong".
    power: str
    #: A note about what the surface actually reads.
    detail: str = ""


#: The categories a surface can fall into.
CATEGORIES = (
    "typed",
    "navigator",
    "screen",
    "graphics",
    "audio",
    "fonts",
    "locale",
    "hardware",
    "media",
    "storage",
    "network",
    "sensor",
)


#: Every surface a registration page may read.
SURFACES: tuple[Surface, ...] = (
    # -- what the visitor types ------------------------------------------
    Surface("email", "input[type=email]", "typed", True, "strong", "the address of the account"),
    Surface("password", "input[type=password]", "typed", False, "none", "not readable by a script"),
    Surface("phone", "input[type=tel]", "typed", True, "strong", "the number of the line"),
    Surface(
        "invite_code",
        "input[name*=invite]",
        "typed",
        True,
        "medium",
        "which campaign the visit came from",
    ),
    Surface(
        "full_name", "input[name*=name]", "typed", True, "medium", "the name of the account holder"
    ),
    # -- the navigator ----------------------------------------------------
    Surface(
        "user_agent",
        "navigator.userAgent",
        "navigator",
        True,
        "medium",
        "the browser and the device it claims",
    ),
    Surface(
        "platform",
        "navigator.platform",
        "navigator",
        True,
        "weak",
        "the operating system the browser claims",
    ),
    Surface(
        "language",
        "navigator.language",
        "navigator",
        True,
        "weak",
        "the first language of the browser",
    ),
    Surface(
        "languages",
        "navigator.languages",
        "navigator",
        True,
        "medium",
        "the whole list of the languages",
    ),
    Surface(
        "hardware_concurrency",
        "navigator.hardwareConcurrency",
        "navigator",
        True,
        "medium",
        "the number of the cores",
    ),
    Surface(
        "device_memory",
        "navigator.deviceMemory",
        "navigator",
        True,
        "weak",
        "the amount of the memory, rounded",
    ),
    Surface(
        "max_touch_points",
        "navigator.maxTouchPoints",
        "navigator",
        True,
        "weak",
        "the number of the touch points",
    ),
    Surface(
        "do_not_track",
        "navigator.doNotTrack",
        "navigator",
        True,
        "none",
        "a setting the visitor chose",
    ),
    Surface(
        "webdriver",
        "navigator.webdriver",
        "navigator",
        True,
        "none",
        "whether the browser is being driven",
    ),
    # -- the screen -------------------------------------------------------
    Surface(
        "screen_size",
        "screen.width, screen.height",
        "screen",
        True,
        "medium",
        "the size of the screen",
    ),
    Surface(
        "avail_size",
        "screen.availWidth, screen.availHeight",
        "screen",
        True,
        "weak",
        "the size without the task bar",
    ),
    Surface("color_depth", "screen.colorDepth", "screen", True, "weak", "the depth of the colour"),
    Surface(
        "pixel_ratio", "devicePixelRatio", "screen", True, "medium", "the density of the pixels"
    ),
    # -- the graphics -----------------------------------------------------
    Surface(
        "canvas_2d",
        "canvas.getContext('2d')",
        "graphics",
        True,
        "strong",
        "a picture drawn by the graphics stack",
    ),
    Surface(
        "webgl_renderer",
        "WebGL UNMASKED_RENDERER_WEBGL",
        "graphics",
        True,
        "strong",
        "the name of the graphics chip",
    ),
    Surface(
        "webgl_vendor",
        "WebGL UNMASKED_VENDOR_WEBGL",
        "graphics",
        True,
        "medium",
        "the maker of the graphics chip",
    ),
    Surface(
        "webgl_parameters",
        "WebGL getParameter",
        "graphics",
        True,
        "strong",
        "the limits of the graphics stack",
    ),
    Surface(
        "webgl_extension_list",
        "WebGL getSupportedExtensions",
        "graphics",
        True,
        "medium",
        "the extensions the stack has",
    ),
    Surface(
        "webgpu_adapter",
        "navigator.gpu.requestAdapter",
        "graphics",
        True,
        "strong",
        "the name of the graphics adapter",
    ),
    Surface(
        "webgpu_features",
        "adapter.features",
        "graphics",
        True,
        "medium",
        "the features the adapter has",
    ),
    # -- the audio --------------------------------------------------------
    Surface(
        "audio_context", "AudioContext", "audio", True, "strong", "the sound stack of the device"
    ),
    Surface(
        "offline_audio",
        "OfflineAudioContext",
        "audio",
        True,
        "strong",
        "the sound stack, computed offline",
    ),
    # -- the fonts --------------------------------------------------------
    Surface(
        "font_list",
        "measureText over a font list",
        "fonts",
        True,
        "strong",
        "the fonts the device has",
    ),
    # -- the locale -------------------------------------------------------
    Surface(
        "timezone",
        "Intl.DateTimeFormat().resolvedOptions().timeZone",
        "locale",
        True,
        "medium",
        "the time zone of the device",
    ),
    Surface(
        "timezone_offset",
        "new Date().getTimezoneOffset()",
        "locale",
        True,
        "medium",
        "the offset from the universal time",
    ),
    Surface(
        "locale_string",
        "navigator.language",
        "locale",
        True,
        "medium",
        "the language and the region",
    ),
    # -- the hardware -----------------------------------------------------
    Surface(
        "battery", "navigator.getBattery()", "hardware", True, "weak", "the state of the battery"
    ),
    Surface(
        "device_memory_detail",
        "performance.memory",
        "hardware",
        True,
        "weak",
        "the heap the browser is using",
    ),
    Surface(
        "connection", "navigator.connection", "hardware", True, "weak", "the kind of the network"
    ),
    # -- the media --------------------------------------------------------
    Surface(
        "media_devices",
        "navigator.mediaDevices.enumerateDevices()",
        "media",
        True,
        "strong",
        "the cameras and the microphones",
    ),
    Surface(
        "permissions",
        "navigator.permissions.query()",
        "media",
        True,
        "weak",
        "the state of a permission",
    ),
    Surface(
        "media_recorder_types",
        "MediaRecorder.isTypeSupported()",
        "media",
        True,
        "weak",
        "the formats the device records",
    ),
    # -- the storage ------------------------------------------------------
    Surface(
        "storage_estimate",
        "navigator.storage.estimate()",
        "storage",
        True,
        "weak",
        "how much is stored",
    ),
    Surface(
        "indexed_db",
        "indexedDB.databases()",
        "storage",
        True,
        "weak",
        "the databases the browser holds",
    ),
    # -- the network ------------------------------------------------------
    Surface(
        "public_ip",
        "the address of the connection",
        "network",
        True,
        "strong",
        "the address of the visitor",
    ),
    Surface(
        "cookie", "document.cookie", "network", True, "medium", "the identifier the server set"
    ),
    Surface(
        "local_storage",
        "localStorage",
        "network",
        True,
        "medium",
        "what the browser kept from a previous visit",
    ),
    Surface(
        "webrtc_addresses",
        "RTCPeerConnection",
        "network",
        True,
        "strong",
        "the addresses of the interfaces",
    ),
    # -- the sensors ------------------------------------------------------
    Surface(
        "device_motion", "DeviceMotionEvent", "sensor", True, "medium", "the movement of the device"
    ),
    Surface(
        "device_orientation",
        "DeviceOrientationEvent",
        "sensor",
        True,
        "medium",
        "the way the device is held",
    ),
    # -- what the server knows -------------------------------------------
    Surface(
        "address_history",
        "the server",
        "network",
        True,
        "strong",
        "the other accounts from the same place",
    ),
    Surface("timing", "the server", "network", True, "medium", "how fast the form was filled in"),
    Surface("behaviour", "the server", "network", True, "strong", "the way the visitor behaves"),
)


def by_category() -> dict[str, tuple[Surface, ...]]:
    """The surfaces grouped by their category."""

    grouped: dict[str, list[Surface]] = {}
    for surface in SURFACES:
        grouped.setdefault(surface.category, []).append(surface)
    return {name: tuple(items) for name, items in grouped.items()}


def strong_surfaces() -> tuple[Surface, ...]:
    """The surfaces that narrow the field down the most."""

    return tuple(surface for surface in SURFACES if surface.power == "strong")


def stable_surfaces() -> tuple[Surface, ...]:
    """The surfaces that do not change between two visits."""

    return tuple(surface for surface in SURFACES if surface.stable)


__all__ = [
    "CATEGORIES",
    "SURFACES",
    "Surface",
    "by_category",
    "strong_surfaces",
    "stable_surfaces",
]
