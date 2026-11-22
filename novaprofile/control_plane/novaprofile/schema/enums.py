"""Shared enumerations and literal types for the NOVA profile schema.

Every enum here is deliberately closed: the consistency resolver validates that
emitted values belong to a physically-plausible Android/Chromium set.  Allowing
free-form strings here would defeat the whole point of the cross-layer resolver.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

# --------------------------------------------------------------------------
# Profile lifecycle
# --------------------------------------------------------------------------

ProfileStatus = Literal[
    "created",
    "ready",
    "starting",
    "running",
    "stopping",
    "crashed",
    "archived",
    "revoked",
]


class ProfileStatusEnum(str, Enum):
    CREATED = "created"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"
    ARCHIVED = "archived"
    REVOKED = "revoked"


# --------------------------------------------------------------------------
# 1. Identity / seed / android build
# --------------------------------------------------------------------------

#: The Android releases the catalogue covers.
#:
#: A handset keeps updating after it ships, so a fleet of one model is
#: spread across several releases.  The release is in the user agent,
#: which makes it one of the cheapest ways to tell two handsets of the
#: same model apart.
AndroidRelease = Literal["13", "14", "15", "16"]
AndroidSdkInt = Literal[33, 34, 35, 36]


class SeedConfigStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    REVOKED = "revoked"


# --------------------------------------------------------------------------
# 2. Proxy / network / geo
# --------------------------------------------------------------------------

ProxyProtocol = Literal[
    "http",
    "https",
    "socks4",
    "socks5",
    "socks5h",
    "ssh",
    "direct",
]

ProxyRotationMode = Literal[
    "manual",
    "on_launch",
    "on_interval",
    "on_api_call",
    "on_error",
]

WebRtcMode = Literal[
    "real",
    "altered",
    "disabled",
    "default_public_interface_only",
    "proxy_only",
    # A profile that does not use a proxy.  The candidates are reachable
    # exactly as they are on an ordinary handset, which is what a real
    # phone reports; the mDNS obfuscation still hides the local address.
    "direct",
]

DnsMode = Literal["system", "proxy_remote", "custom_udp", "doh", "dot"]

GeolocationMode = Literal["allow_custom", "sync_proxy_ip", "prompt", "block"]


# --------------------------------------------------------------------------
# 3. Navigator / client hints / permissions
# --------------------------------------------------------------------------

PermissionState = Literal["granted", "denied", "prompt"]

PermissionName = Literal[
    "geolocation",
    "notifications",
    "camera",
    "microphone",
    "clipboard-read",
    "clipboard-write",
    "midi",
    "persistent-storage",
    "accelerometer",
    "gyroscope",
    "magnetometer",
]


# --------------------------------------------------------------------------
# 4. Screen / viewport / media queries
# --------------------------------------------------------------------------

OrientationType = Literal[
    "portrait-primary",
    "portrait-secondary",
    "landscape-primary",
    "landscape-secondary",
]

OrientationAngle = Literal[0, 90, 180, 270]

ColorScheme = Literal["light", "dark"]
ReducedMotion = Literal["no-preference", "reduce"]
ContrastPref = Literal["no-preference", "more", "less", "custom"]
ForcedColors = Literal["none", "active"]
PointerCapability = Literal["none", "coarse", "fine"]
HoverCapability = Literal["none", "hover"]
ColorGamut = Literal["srgb", "p3", "rec2020"]
DynamicRange = Literal["standard", "high"]


# --------------------------------------------------------------------------
# 5. Timezone / locale / geolocation
# --------------------------------------------------------------------------

HourCycle = Literal["h23", "h24", "h11", "h12"]
NumberingSystem = Literal["latn", "arab", "deva", "thai"]
CalendarType = Literal["gregory", "buddhist", "islamic", "hebrew", "japanese", "persian"]


# --------------------------------------------------------------------------
# 6. Canvas / WebGL / WebGPU
# --------------------------------------------------------------------------

CanvasMode = Literal["noise", "native_passthrough", "block"]
CanvasTarget = Literal[
    "toDataURL",
    "toBlob",
    "getImageData",
    "isPointInPath",
    "isPointInStroke",
    "measureText",
]

WebGlMode = Literal["noise", "native_passthrough", "disabled"]

WebGlVendor = Literal[
    "ARM",
    "Qualcomm",
    "Google Inc. (ARM)",
    "Google Inc. (Qualcomm)",
    # The Exynos parts are Samsung's own implementation on an ARM
    # licence; they report themselves as ARM or as Samsung.
    "Samsung",
    # The Kirin parts are Huawei's; the Maleoon graphics core ships
    # inside them.
    "Huawei",
    # The PowerVR parts that ship in a handful of MediaTek devices.
    "Imagination Technologies",
    # The AMD parts that ship in the handful of Android tablets and
    # the Steam Deck's Android layer.
    "AMD",
    # The Intel parts that ship in the Android x86 tablets.
    "Intel",
]


# --------------------------------------------------------------------------
# 7. Audio / rects / fonts / media devices / sensors
# --------------------------------------------------------------------------

AudioMode = Literal["noise", "native_quantized", "disabled"]

RectsMode = Literal["noise", "off"]

RectsTarget = Literal[
    "getBoundingClientRect",
    "getClientRects",
    "Range.getBoundingClientRect",
    "Range.getClientRects",
    "measureText",
]

FontsMode = Literal["android_preset", "custom_list"]

MediaDevicesMode = Literal["deterministic_mock", "real", "none"]

MediaDeviceIdScope = Literal["per_profile", "per_profile_and_origin"]

EffectiveType = Literal["4g", "3g", "2g"]
ConnectionType = Literal["wifi", "cellular"]

MotionProfile = Literal["stationary_micro_jitter", "walking_subtle", "custom_static"]

GamepadDevice = Literal[
    "Xbox 360 Controller",
    "Xbox One Controller",
    "PlayStation 5 Controller",
    "PlayStation 4 Controller",
    "Generic Gamepad",
]


# --------------------------------------------------------------------------
# Signature registry
# --------------------------------------------------------------------------


class SignatureKind(str, Enum):
    """The axes whose signatures are tracked in the registry.

    These map 1:1 onto the collision-guard axes.  ``FULL`` is the composite
    signature over every other axis; it is the last line of defence.
    """

    CANVAS = "sig_canvas"
    WEBGL_DRAW = "sig_webgl_draw"
    WEBGL_PARAMS = "sig_webgl_params"
    RECTS = "sig_rects"
    DEVICES = "sig_devices"
    AUDIO = "sig_audio"
    #: The device identity: serial, build fingerprint, bootloader and panel.
    #:
    #: This axis exists because the others only cover the *rendering* and
    #: *hardware sampling* surfaces.  Two profiles could differ on every one
    #: of them and still report the same serial and the same build
    #: fingerprint -- which is exactly the pair a site uses to recognise a
    #: returning device.  The device axis is what makes "two profiles, two
    #: devices" an enforced property rather than a hope.
    DEVICE = "sig_device"
    #: The motion sensors.  ``DeviceMotionEvent`` exposes the raw
    #: accelerometer and gyroscope readings, so a site can cluster two
    #: profiles that report the same signal without ever touching canvas or
    #: WebGL.
    SENSORS = "sig_sensors"
    #: The storage quota and the network estimate.  ``navigator.storage``
    #: and ``navigator.connection`` are both readable on their own.
    STORAGE = "sig_storage"
    #: The WebGPU adapter description.  ``navigator.gpu.requestAdapter()``
    #: returns the adapter and its limits.
    WEBGPU = "sig_webgpu"
    #: The installed font list.  A site can recover the font configuration
    #: from the metrics of rendered text, so the font set identifies the
    #: unit just as the canvas noise does.
    FONTS = "sig_fonts"
    #: The navigator surface: platform, hardware concurrency, device memory
    #: and the language list.
    #:
    #: This axis is deliberately **not** guarded.  Those values are
    #: properties of the device *family* and of the browser *build*: two
    #: real phones of the same model running the same Chrome release
    #: report exactly the same navigator, and Chromium 110 and later carry
    #: no build token in the user agent at all.  Treating that surface as
    #: an identity would demand a difference no real device has.  It stays
    #: defined so that signature rows written by older versions still
    #: resolve.
    NAVIGATOR = "sig_navigator"
    FULL = "sig_full"


class SignatureState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    REVOKED = "revoked"


# --------------------------------------------------------------------------
# Batch / import-export formats
# --------------------------------------------------------------------------

CookieFormat = Literal["json", "netscape"]

__all__ = [name for name in dir() if not name.startswith("_")]
