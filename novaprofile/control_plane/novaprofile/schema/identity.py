"""Identity schema groups: navigator, client hints, permissions, timezone/locale.

Group 3 and group 5 of the specification.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    CalendarType,
    HourCycle,
    NumberingSystem,
    PermissionName,
    PermissionState,
)

# --------------------------------------------------------------------------
# 3.1 Navigator
# --------------------------------------------------------------------------


class Navigator(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_agent: str
    app_version: str = Field(default="5.0 (Linux; Android 14)")
    app_name: str = "Netscape"
    app_code_name: str = "Mozilla"
    vendor: str = "Google Inc."
    vendor_sub: str = ""
    product: str = "Gecko"
    product_sub: str = "20030107"
    platform: Literal["Linux armv8l", "Linux aarch64", "Linux armv7l"]
    # ``hardwareConcurrency`` is the number of logical cores the platform
    # reports.  Chromium reads ``base::SysInfo::NumberOfProcessors()``
    # and hands that to the page, so the count is neither rounded nor
    # clamped: a workstation with eighteen threads reports eighteen.
    #
    # The values here are the counts the handsets of this fleet ship
    # with.  A big.LITTLE SoC of the era reports four, six, eight or
    # twelve logical cores, because its little cores count twice.
    hardware_concurrency: Literal[4, 6, 8, 12]
    # ``deviceMemory`` is the physical memory in gigabytes, rounded to
    # the nearest power of two and clamped -- the algorithm Chromium
    # itself uses in ``ApproximatedDeviceMemory``.  A handset with 6 GB
    # reports 8, and a handset with 3 GB reports 4.
    #
    # The ceiling is eight, because Chromium clamps Android to
    # [1 GB, 8 GB]: the desktop clamp of thirty-two is what a desktop
    # Chrome reports, and the product of this project is an Android
    # browser.  A handset with twelve gigabytes therefore reports eight.
    device_memory: Literal[1, 2, 4, 8]
    max_touch_points: Literal[5, 10]
    language: str
    languages: list[str] = Field(min_length=1)
    cookie_enabled: bool = True
    do_not_track: str | None = None
    global_privacy_control: bool | None = None
    pdf_viewer_enabled: bool = True
    webdriver: bool = False
    on_line: bool = True
    java_enabled: bool = False

    @field_validator("languages")
    @classmethod
    def _primary_language_first(cls, v: list[str]) -> list[str]:
        return v


class BrandVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    brand: str
    version: str


class UserAgentData(BaseModel):
    """``navigator.userAgentData`` / ``Sec-CH-UA-*`` source of truth."""

    model_config = ConfigDict(frozen=True)

    brands: list[BrandVersion]
    full_version_list: list[BrandVersion]
    mobile: bool = True
    platform: str = "Android"
    platform_version: str
    architecture: Literal["", "arm"]
    bitness: Literal["", "64"]
    model: str
    wow64: bool = False


class PluginMimeType(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    suffixes: str = ""


class PluginEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    filename: str
    description: str
    mime_types: list[PluginMimeType] = Field(default_factory=list)


class PluginsMime(BaseModel):
    model_config = ConfigDict(frozen=True)

    plugins: list[PluginEntry] = Field(default_factory=list)
    mime_types: list[PluginMimeType] = Field(default_factory=list)


class Permissions(BaseModel):
    """Per-origin permission policies.

    On Android/Chromium the *default* for most of these is ``prompt``; a
    profile that answers ``prompt`` everywhere looks synthetic, so the
    generator assigns a realistic mix.
    """

    model_config = ConfigDict(frozen=True)

    policies: dict[PermissionName, PermissionState] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# 5. Timezone / locale / geolocation
# --------------------------------------------------------------------------


class Timezone(BaseModel):
    model_config = ConfigDict(frozen=True)

    iana_name: str
    offset_minutes: int = Field(ge=-840, le=840)
    dst_active: bool
    abbreviation: str


class Locale(BaseModel):
    model_config = ConfigDict(frozen=True)

    primary: str
    intl_locale: str
    numbering_system: NumberingSystem = "latn"
    calendar: CalendarType = "gregory"
    hour_cycle: HourCycle = "h23"
    currency: str


class Geolocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["allow_custom", "sync_proxy_ip", "prompt", "block"] = "prompt"
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    accuracy: float | None = Field(default=None, ge=0.0)
    altitude: float | None = Field(default=None)
    altitude_accuracy: float | None = Field(default=None, ge=0.0)
    heading: float | None = Field(default=None, ge=0.0, le=360.0)
    speed: float | None = Field(default=None, ge=0.0)
    timestamp_jitter_ms: int = Field(default=0, ge=0)


__all__ = [
    "Navigator",
    "BrandVersion",
    "UserAgentData",
    "PluginMimeType",
    "PluginEntry",
    "PluginsMime",
    "Permissions",
    "Timezone",
    "Locale",
    "Geolocation",
]
