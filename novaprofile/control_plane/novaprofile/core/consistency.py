"""Cross-layer consistency resolver.

An anti-bot vendor that samples the UA string, the client hints, the WebGL
renderer and the screen size in the same session can trivially detect a
profile whose parts disagree with each other.  This module is the gate that
prevents that: it verifies that every coupled field is physically plausible
and, where possible, repairs it deterministically.

Two rules drive the design:

* **Errors** describe combinations that are impossible or that leak the
  generator (for example a desktop GPU string on an Android UA).  They block
  a profile from being handed to the browser core.
* **Warnings** describe combinations that are merely implausible.  They are
  repaired by :meth:`ConsistencyResolver.resolve`.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schema.profile import ProfileConfig
from .devices import DEVICE_PRESETS
from .gpu_params import build_tier_table, tier_for
from .seed import SeedDeriver

# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


class ConsistencyIssue(BaseModel):
    severity: Literal["error", "warning"]
    axis: str
    message: str
    fields: list[str] = Field(default_factory=list)


class ConsistencyReport(BaseModel):
    ok: bool
    errors: list[ConsistencyIssue] = Field(default_factory=list)
    warnings: list[ConsistencyIssue] = Field(default_factory=list)

    @property
    def all_issues(self) -> list[ConsistencyIssue]:
        return [*self.errors, *self.warnings]


class ConsistencyError(ValueError):
    """Raised when a profile cannot be made consistent."""

    def __init__(self, report: ConsistencyReport) -> None:
        self.report = report
        detail = "; ".join(f"[{i.axis}] {i.message}" for i in report.errors)
        super().__init__(f"profile is inconsistent: {detail}")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

#: GPUs that only appear on desktop/PC.  Seeing one in an Android profile is
#: an instant anomaly signal.
#:
#: The list is deliberately narrow.  A marker that also occurs in the name
#: of a *mobile* part has to stay out of it, because the fleet holds real
#: handsets whose integrated GPU carries a desktop-family name:
#:
#: * the Tegra 2, the Tegra 3 and the Tegra K1 of the Android tablets of
#:   2011 to 2013 ship an integrated GPU that NVIDIA calls "ULP GeForce"
#:   -- ULP stands for Ultra Low Power, and it is a mobile part;
#: * the Intel Atom tablets of the same era carry the Intel integrated
#:   graphics of their x86 SoC, which Chromium reports as
#:   "Intel HD Graphics" or as the architecture name "Intel Gen 7".
#:
#: A renderer that is one of those is an Android GPU, and flagging it as a
#: desktop anomaly would reject thirty-eight real devices of the fleet.
#: The desktop markers below are therefore the ones that no Android
#: handset ever reports.
_DESKTOP_GPU_MARKERS = (
    # The ANGLE wrapper string of a desktop Chromium build.
    "ANGLE (",
    # A discrete NVIDIA part.  The Tegra parts are spelled "ULP GeForce"
    # and are matched by the mobile test above, so they never reach this
    # list; a bare "GeForce" or "NVIDIA" string is a desktop part.
    "NVIDIA",
    "GeForce",
    "Radeon",
    # The "Intel(R) HD Graphics" branding of a desktop driver.  The
    # mobile Intel parts are spelled "Intel HD Graphics" without the
    # trademark and are matched by the mobile test above.
    "Intel(R)",
    "Intel ",
    "SwiftShader",
    "llvmpipe",
    "Microsoft Basic Render",
)

#: Renderers of mobile parts that carry a desktop-family name.  A
#: renderer that matches one of them is an Android GPU, whatever family
#: its name mentions.
_MOBILE_GPU_RENDERERS = (
    # The integrated GPU of the Tegra 2, the Tegra 3 and the Tegra K1.
    "ULP GeForce",
    # The integrated graphics of the Intel Atom tablets.  Chromium
    # reports them either by the branding ("Intel HD Graphics") or by the
    # architecture of the core ("Intel Gen 7", "Intel Gen 9").
    "Intel HD Graphics",
    "Intel Gen",
    "Intel graphics media accelerator",
)

#: The GPU families an Android profile may report.  A renderer that
#: matches none of them is reported as an unknown Android GPU.
_ANDROID_GPU_MARKERS = (
    "Mali",
    "Adreno",
    "PowerVR",
    "Immortalis",
    # Samsung's own Xclipse cores, which replace the Mali parts in the
    # Exynos 2200 and the 2400.
    "Xclipse",
    # Huawei's Maleoon core, which replaces the Mali parts in the Kirin
    # 9000 series.
    "Maleoon",
    # Imagination's B-Series cores, which some Unisoc and some MediaTek
    # parts carry instead of a Mali.  The sheets spell them either as a
    # bare part number ("IMG8322") or with the family name ("IMG
    # BXM-8-256").
    "IMG8322",
    "IMG BXM",
    "IMG8",
    # Vivante's cores, which the Marvell and the older MediaTek tablets
    # of 2013 and 2014 carry.
    "Vivante",
    # Broadcom's VideoCore, which the Samsung and the Huawei handsets of
    # 2012 and 2013 carry.
    "VideoCore",
    # The Tegra X1 and the Tegra K1 of the Android tablets of 2015 and
    # 2016.  Their integrated GPU is a desktop architecture under a
    # mobile name.
    "Nvidia Maxwell",
    "Kepler",
    # The mobile parts that carry a desktop-family name.
    "ULP GeForce",
    "Intel HD Graphics",
    "Intel Gen",
    "Intel graphics media accelerator",
)


def _is_mobile_gpu_renderer(renderer: str) -> bool:
    """Whether the renderer names a GPU that shipped in an Android device.

    Some mobile parts carry a desktop-family name, and the renderer a
    real device reports is that name:

    * the Tegra 2, the Tegra 3 and the Tegra K1 of the Android tablets
      of 2011 to 2013 have an integrated GPU that NVIDIA calls "ULP
      GeForce" -- ULP is the Ultra Low Power variant of the Tegra
      graphics, and it is a mobile part;
    * the Intel Atom tablets of the same era carry the integrated
      graphics of their x86 SoC, which the driver reports as "Intel HD
      Graphics" or as the architecture name of the core.

    A renderer that matches one of them is an Android GPU.  A renderer
    that mentions a desktop family *without* being one of these -- a bare
    "GeForce" or a "NVIDIA" string -- is still a desktop anomaly.
    """

    lowered = (renderer or "").lower()
    return any(marker.lower() in lowered for marker in _MOBILE_GPU_RENDERERS)


def _chrome_major_window(version_release: str, sdk_int: int) -> tuple[int, int]:
    """The range of Chromium majors that shipped with an Android release.

    Android 13 shipped Chromium 103 through 116, Android 14 shipped 114
    through 127, and Android 15 shipped 128 and later.  Anti-bot vendors
    check that the UA's Chrome major is inside the plausible window for
    the claimed OS release.

    The window is a *band*, not a single value: a Chromium major can ship
    with several Android releases over its lifetime, so any major inside
    the band is plausible.  A UA that names a major outside the band is
    the profile that gets singled out.
    """
    try:
        number = int(version_release.split(".")[0])
    except (ValueError, AttributeError):
        number = 13

    if number >= 15:
        return (128, 199)
    if number == 14:
        return (114, 127)
    return (103, 116)


def _chrome_major(version_release: str, sdk_int: int) -> str:
    """The Chromium major version most likely to be seen on a release.

    This is the *centre* of the plausible band, used where a single
    representative version is needed rather than a range.
    """
    low, high = _chrome_major_window(version_release, sdk_int)
    return str((low + high) // 2)


#: The Android density buckets and the pixel ratio each of them reports.
#:
#: The buckets are the platform's own table, and they are not evenly
#: spaced: the 213 bucket sits between the 160 and the 240 one, the 411
#: and the 420 buckets sit between the 400 and the 440 one, and the
#: 520, 525 and 545 buckets sit between the 480 and the 560 one.  The
#: ratio is therefore *not* the quotient of the density by 160 -- the
#: 213 bucket reports 1.5 and not the 1.333 of its quotient.
#:
#: The table is duplicated from the device catalogue on purpose: the
#: consistency resolver has to keep working when the scraped dataset is
#: not on the machine, and the catalogue that builds it needs the same
#: table at import time.
_DENSITY_RATIO: dict[int, float] = {
    120: 1.0,
    160: 1.0,
    213: 1.5,
    240: 1.5,
    280: 1.75,
    320: 2.0,
    360: 2.25,
    400: 2.5,
    411: 2.625,
    420: 2.625,
    440: 2.75,
    480: 3.0,
    520: 3.25,
    525: 3.25,
    545: 3.5,
    560: 3.5,
    640: 4.0,
}


def _ratio_for_density(density_dpi: int) -> float | None:
    """The pixel ratio the platform reports for a panel of that density.

    A panel's true density is rounded to the nearest bucket the platform
    defines, and the browser then reports the ratio of that bucket.
    ``None`` is returned for a density that is out of the table's range,
    in which case the rule does not apply.
    """

    if density_dpi in _DENSITY_RATIO:
        return _DENSITY_RATIO[density_dpi]
    buckets = sorted(_DENSITY_RATIO)
    if density_dpi < buckets[0] or density_dpi > buckets[-1]:
        return None
    nearest = min(buckets, key=lambda bucket: abs(bucket - density_dpi))
    return _DENSITY_RATIO[nearest]


_UA_PATTERN = re.compile(
    r"^Mozilla/5\.0 \(Linux; Android (?P<rel>[0-9]+(?:\.[0-9]+)*); "
    r"(?P<model>[^;)]+?)(?: Build/[^;)]+)?\) AppleWebKit/537\.36 "
    r"\(KHTML, like Gecko\) Chrome/(?P<chrome>[0-9]+)\.0\.0\.0 "
    r"(?:Mobile )?Safari/537\.36$"
)


# --------------------------------------------------------------------------
# Resolver
# --------------------------------------------------------------------------


class ConsistencyResolver:
    """Validates and repairs a :class:`ProfileConfig`."""

    def __init__(self, *, strict: bool = False) -> None:
        self.strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, config: ProfileConfig) -> ConsistencyReport:
        """Run every consistency rule and collect the findings."""
        errors: list[ConsistencyIssue] = []
        warnings: list[ConsistencyIssue] = []

        self._check_android_build(config, errors, warnings)
        self._check_abi(config, errors, warnings)
        self._check_screen(config, errors, warnings)
        self._check_navigator(config, errors, warnings)
        self._check_client_hints(config, errors, warnings)
        self._check_webgl(config, errors, warnings)
        self._check_geo(config, errors, warnings)
        self._check_webrtc(config, errors, warnings)
        self._check_headers(config, errors, warnings)
        self._check_privacy(config, errors, warnings)
        self._check_hardware_surfaces(config, errors, warnings)

        return ConsistencyReport(
            ok=not errors,
            errors=errors,
            warnings=warnings,
        )

    def resolve(self, config: ProfileConfig) -> ProfileConfig:
        """Return a copy of ``config`` with every repairable warning fixed.

        Errors are never "resolved" silently -- if the profile has a hard
        inconsistency, :class:`ConsistencyError` is raised.

        The repairs run **before** the validation: a profile whose proxy was
        attached after the generation is inconsistent by construction (the
        WebRTC policy still follows the old, proxy-less state), and it is
        exactly that combination the repair is there to fix.  Validating
        first would refuse the very profile the operator asked for.
        """
        data = config.model_dump()
        changed = self._repair(data)

        repaired = ProfileConfig.model_validate(data) if changed else config
        report = self.check(repaired)
        if report.errors:
            raise ConsistencyError(report)

        if not changed:
            return config

        return repaired

    def _repair(self, data: dict[str, Any]) -> bool:
        """Apply every repair to ``data`` in place.

        Returns ``True`` when at least one field was changed.
        """
        changed = False

        # --- repair 1: UA Chrome major vs. the claimed OS release ---
        navigator = data["navigator"]
        build = data["android_build"]
        expected_major = _chrome_major(build["version_release"], build["sdk_int"])
        ua = navigator["user_agent"]
        match = _UA_PATTERN.match(ua)
        if match:
            actual_major = match.group("chrome")
            if actual_major != expected_major:
                navigator["user_agent"] = ua.replace(
                    f"Chrome/{actual_major}.0.0.0", f"Chrome/{expected_major}.0.0.0"
                )
                changed = True

        # --- repair 2: navigator.app_version must mention the OS release ---
        app_version = navigator.get("app_version", "")
        if app_version and build["version_release"] not in app_version:
            navigator["app_version"] = f"5.0 (Linux; Android {build['version_release']})"
            changed = True

        # --- repair 3: languages list must start with the primary language ---
        locale = data["locale"]
        languages = navigator.get("languages") or []
        primary = locale.get("primary", "")
        if primary and (not languages or languages[0] != primary):
            navigator["languages"] = [primary, *[x for x in languages if x != primary]]
            changed = True

        # --- repair 4: client hints platform_version vs. build ---
        uad = data["user_agent_data"]
        platform_version = uad.get("platform_version", "")
        expected_pv = build["version_release"]
        if platform_version and platform_version != expected_pv:
            uad["platform_version"] = expected_pv
            changed = True

        # --- repair 5: Sec-CH-UA headers vs. the user agent data ---
        headers = data["http_headers"]
        brands = uad.get("brands") or []
        if brands:
            rendered = ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in brands)
            if headers.get("sec_ch_ua") != rendered:
                headers["sec_ch_ua"] = rendered
                changed = True
        full_list = uad.get("full_version_list") or []
        if full_list:
            rendered_full = ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in full_list)
            if headers.get("sec_ch_ua_full_version_list") != rendered_full:
                headers["sec_ch_ua_full_version_list"] = rendered_full
                changed = True
        if headers.get("sec_ch_ua_platform") != '"Android"':
            headers["sec_ch_ua_platform"] = '"Android"'
            changed = True
        if headers.get("sec_ch_ua_mobile") != "?1":
            headers["sec_ch_ua_mobile"] = "?1"
            changed = True
        if headers.get("sec_ch_ua_platform_version") != uad.get("platform_version", ""):
            headers["sec_ch_ua_platform_version"] = uad.get("platform_version", "")
            changed = True
        if headers.get("sec_ch_ua_arch") != uad.get("architecture", ""):
            headers["sec_ch_ua_arch"] = uad.get("architecture", "")
            changed = True
        if headers.get("sec_ch_ua_bitness") != uad.get("bitness", ""):
            headers["sec_ch_ua_bitness"] = uad.get("bitness", "")
            changed = True
        if headers.get("sec_ch_ua_model") != uad.get("model", ""):
            headers["sec_ch_ua_model"] = uad.get("model", "")
            changed = True

        # --- repair 6: Accept-Language vs. the primary locale ---
        primary = locale.get("primary", "")
        if primary:
            base = primary.split("-")[0]
            expected = f"{primary},{base};q=0.9" if base != primary else primary
            if headers.get("accept_language") != expected:
                headers["accept_language"] = expected
                changed = True

        # --- repair 7: timezone offset vs. the IANA name ---
        timezone = data["timezone"]
        expected_offset = _iana_offset(
            timezone.get("iana_name", ""), timezone.get("dst_active", False)
        )
        if expected_offset is not None and timezone.get("offset_minutes") != expected_offset:
            timezone["offset_minutes"] = expected_offset
            changed = True

        # --- repair 8: viewport must fit inside the screen ---
        screen = data["screen"]
        viewport = data["viewport"]
        if viewport.get("outer_width", 0) > screen["width"]:
            viewport["outer_width"] = screen["width"]
            viewport["inner_width"] = min(
                viewport.get("inner_width", screen["width"]), screen["width"]
            )
            changed = True
        if viewport.get("outer_height", 0) > screen["height"]:
            viewport["outer_height"] = screen["height"]
            viewport["inner_height"] = min(
                viewport.get("inner_height", screen["height"]), screen["height"]
            )
            changed = True

        # The viewport also has to fit inside the *available* area, which
        # is the screen minus the system bars.  A viewport that reaches
        # past the available height is a contradiction a page sees by
        # comparing the two numbers.
        avail_height = screen.get("avail_height", screen["height"])
        avail_width = screen.get("avail_width", screen["width"])
        if viewport.get("inner_height", 0) > avail_height:
            viewport["inner_height"] = avail_height
            viewport["outer_height"] = min(
                viewport.get("outer_height", avail_height), avail_height
            )
            changed = True
        if viewport.get("inner_width", 0) > avail_width:
            viewport["inner_width"] = avail_width
            viewport["outer_width"] = min(
                viewport.get("outer_width", avail_width), avail_width
            )
            changed = True

        # --- repair 9: storage usage must not exceed the quota ---
        storage = data["storage_network"]
        if storage.get("usage_bytes", 0) > storage.get("quota_bytes", 0):
            storage["usage_bytes"] = storage.get("quota_bytes", 0)
            changed = True

        # --- repair 10: the WebRTC policy has to follow the proxy state ---
        # The proxy is usually attached to the profile *after* it has been
        # generated, so the WebRTC configuration has to be re-derived here.
        # Without this the profile would claim a proxy and still expose the
        # host's address through the ICE candidates.
        webrtc = data["webrtc"]
        proxy = data["proxy"]
        proxied = (
            bool(proxy.get("enabled"))
            and proxy.get("protocol") != "direct"
            and bool(proxy.get("host"))
        )
        expected_mode = "proxy_only" if proxied else "direct"
        if webrtc.get("mode") != expected_mode:
            webrtc["mode"] = expected_mode
            changed = True
        if proxied:
            if webrtc.get("ice_transport_policy") != "relay":
                webrtc["ice_transport_policy"] = "relay"
                changed = True
            if not webrtc.get("block_non_proxied_udp"):
                webrtc["block_non_proxied_udp"] = True
                changed = True
            if not webrtc.get("mdns_enabled"):
                webrtc["mdns_enabled"] = True
                changed = True
        else:
            # An unproxied profile must behave like an ordinary handset: the
            # candidates have to be reachable, otherwise the missing
            # RTCPeerConnection is a louder bot signal than any leak.
            if webrtc.get("ice_transport_policy") != "all":
                webrtc["ice_transport_policy"] = "all"
                changed = True
            if webrtc.get("block_non_proxied_udp"):
                webrtc["block_non_proxied_udp"] = False
                changed = True
        if not webrtc.get("mdns_enabled"):
            # The mDNS obfuscation is what keeps the local interface
            # addresses out of the candidates in the first place.
            webrtc["mdns_enabled"] = True
            changed = True

        return changed

    # ------------------------------------------------------------------
    # Individual rule groups
    # ------------------------------------------------------------------

    def _check_android_build(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        build = config.android_build
        fingerprint = build.fingerprint

        # The fingerprint embeds the brand/product/device and the release.
        #   brand/product/device : release/build_id.incremental : id : user/tags
        parts = fingerprint.split(":")
        if len(parts) >= 2:
            brand_device = parts[0]
            # The second segment holds "release/build_id.incremental".
            release, _, build_segment = parts[1].partition("/")
            if "/" in brand_device:
                brand, _, rest = brand_device.partition("/")
                if brand and brand.lower() != build.brand.lower():
                    errors.append(
                        ConsistencyIssue(
                            severity="error",
                            axis="android_build",
                            message=(
                                f"fingerprint brand {brand!r} does not match brand {build.brand!r}"
                            ),
                            fields=["android_build.fingerprint", "android_build.brand"],
                        )
                    )
                segments = rest.split("/")
                model_name = segments[0] if segments else ""
                if model_name and build.product and model_name != build.product:
                    warnings.append(
                        ConsistencyIssue(
                            severity="warning",
                            axis="android_build",
                            message=(
                                f"fingerprint product segment {model_name!r} does not "
                                f"match product {build.product!r}"
                            ),
                            fields=["android_build.fingerprint", "android_build.product"],
                        )
                    )
            if release and release != build.version_release:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="android_build",
                        message=(
                            f"fingerprint release {release!r} does not match "
                            f"version_release {build.version_release!r}"
                        ),
                        fields=["android_build.fingerprint"],
                    )
                )
            # The build id segment must match build_id.incremental.
            expected_build = f"{build.build_id}.{build.version_incremental}"
            if build_segment != expected_build:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="android_build",
                        message=(
                            f"fingerprint build segment {build_segment!r} does "
                            f"not match {expected_build!r}"
                        ),
                        fields=["android_build.fingerprint"],
                    )
                )

        # The SDK level must match the OS release.
        expected_sdk = {"13": 33, "14": 34, "15": 35}.get(build.version_release)
        if expected_sdk is not None and build.sdk_int != expected_sdk:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="android_build",
                    message=(
                        f"sdk_int {build.sdk_int} does not correspond to Android "
                        f"{build.version_release} (expected {expected_sdk})"
                    ),
                    fields=["android_build.sdk_int"],
                )
            )

        # The security patch must not be in the future.
        if build.security_patch > "2026-12-31":
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="android_build",
                    message=f"security patch {build.security_patch} looks implausible",
                    fields=["android_build.security_patch"],
                )
            )

    def _check_abi(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        abi = config.android_abi_soc
        if abi.primary_abi not in abi.supported_abis:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="android_abi_soc",
                    message=(f"primary_abi {abi.primary_abi!r} is not in supported_abis"),
                    fields=["android_abi_soc.primary_abi"],
                )
            )
        partition = set(abi.supported_64_bit_abis) | set(abi.supported_32_bit_abis)
        if partition != set(abi.supported_abis):
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="android_abi_soc",
                    message=("the 64-bit and 32-bit ABI lists must partition supported_abis"),
                    fields=["android_abi_soc.supported_abis"],
                )
            )
        if abi.is_64_bit != (abi.primary_abi in abi.supported_64_bit_abis):
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="android_abi_soc",
                    message="is_64_bit disagrees with primary_abi",
                    fields=["android_abi_soc.is_64_bit"],
                )
            )

    def _check_screen(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        screen = config.screen
        viewport = config.viewport
        if viewport.outer_width > screen.width:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="screen_viewport",
                    message=(
                        f"outer_width {viewport.outer_width} exceeds screen width {screen.width}"
                    ),
                    fields=["viewport.outer_width", "screen.width"],
                )
            )
        if viewport.inner_width > viewport.outer_width:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="screen_viewport",
                    message="inner_width must not exceed outer_width",
                    fields=["viewport.inner_width"],
                )
            )
        if viewport.inner_height > viewport.outer_height:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="screen_viewport",
                    message="inner_height must not exceed outer_height",
                    fields=["viewport.inner_height"],
                )
            )

        # The viewport has to fit inside the area the browser says it
        # has.  A page that measures the viewport against
        # ``screen.avavailHeight`` finds the contradiction immediately.
        if viewport.inner_width > screen.avail_width:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="screen_viewport",
                    message=(
                        f"inner_width {viewport.inner_width} exceeds the available "
                        f"width {screen.avail_width}"
                    ),
                    fields=["viewport.inner_width", "screen.avail_width"],
                )
            )
        if viewport.inner_height > screen.avail_height:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="screen_viewport",
                    message=(
                        f"inner_height {viewport.inner_height} exceeds the available "
                        f"height {screen.avail_height}"
                    ),
                    fields=["viewport.inner_height", "screen.avail_height"],
                )
            )
        # The pixel ratio has to be one the platform's own density
        # buckets produce.  A panel of 160 dpi reports 1.0, a panel of
        # 240 dpi reports 1.5, and so on -- the ratio is a property of
        # the panel, not a preference.
        #
        # The check used to warn about any ratio below 1.75, on the
        # ground that a ratio that low is unusual for a *modern* phone.
        # The fleet contradicts that: it holds real budget handsets and
        # real tablets whose panels are 120, 160, 213 and 240 dpi, and
        # the platform reports those as 1.0 and 1.5.  The Tecno Pop 2, the
        # Oppo A31, the ZTE Blade A3 and the Samsung Galaxy J2 Core all
        # ship an HD or an FWVGA panel, and a site that reads
        # ``devicePixelRatio`` sees exactly those ratios on them.
        #
        # The rule that survives that is the one the platform itself
        # defines: the ratio has to be the ratio of the density bucket
        # the panel falls into.
        nominal = _ratio_for_density(screen.density_dpi)
        if nominal is not None and abs(screen.device_pixel_ratio - nominal) > 1e-9:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="screen_viewport",
                    message=(
                        f"device_pixel_ratio {screen.device_pixel_ratio} does not match "
                        f"the ratio {nominal} of the {screen.density_dpi} dpi density "
                        "bucket the panel falls into"
                    ),
                    fields=["screen.device_pixel_ratio", "screen.density_dpi"],
                )
            )

    def _check_navigator(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        nav = config.navigator
        ua = nav.user_agent

        if "Android" not in ua:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="navigator",
                    message="the user agent does not mention Android",
                    fields=["navigator.user_agent"],
                )
            )
        if "Mobile" not in ua:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="navigator",
                    message="an Android phone user agent must contain 'Mobile'",
                    fields=["navigator.user_agent"],
                )
            )
        if nav.platform.startswith("Linux") is False:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="navigator",
                    message="navigator.platform must start with 'Linux' on Android",
                    fields=["navigator.platform"],
                )
            )
        if nav.webdriver:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="navigator",
                    message="navigator.webdriver must be false",
                    fields=["navigator.webdriver"],
                )
            )
        if nav.java_enabled:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="navigator",
                    message="navigator.javaEnabled is false on every Android browser",
                    fields=["navigator.java_enabled"],
                )
            )

        # The model in the UA must match the device build.
        build = config.android_build
        match = _UA_PATTERN.match(ua)
        if match:
            model = match.group("model")
            if build.model not in ua and model.replace("_", " ") not in ua:
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="navigator",
                        message=(
                            f"the user agent model {model!r} does not mention the "
                            f"device model {build.model!r}"
                        ),
                        fields=["navigator.user_agent", "android_build.model"],
                    )
                )
            chrome_major = match.group("chrome")
            low, high = _chrome_major_window(build.version_release, build.sdk_int)
            if not (low <= int(chrome_major) <= high):
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="navigator",
                        message=(
                            f"Chrome/{chrome_major} is outside the plausible window for "
                            f"Android {build.version_release} "
                            f"(expected {low} to {high})"
                        ),
                        fields=["navigator.user_agent"],
                    )
                )

        if nav.languages and nav.language != nav.languages[0]:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="navigator",
                    message="navigator.language must equal navigator.languages[0]",
                    fields=["navigator.language", "navigator.languages"],
                )
            )

        if nav.max_touch_points < 5:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="navigator",
                    message="an Android phone reports at least 5 touch points",
                    fields=["navigator.max_touch_points"],
                )
            )

    def _check_client_hints(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        uad = config.user_agent_data
        build = config.android_build

        if uad.platform != "Android":
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message="navigator.userAgentData.platform must be 'Android'",
                    fields=["user_agent_data.platform"],
                )
            )
        if not uad.mobile:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message="navigator.userAgentData.mobile must be true on Android",
                    fields=["user_agent_data.mobile"],
                )
            )
        if uad.platform_version and uad.platform_version != build.version_release:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message=(
                        f"client hint platform_version {uad.platform_version!r} does not "
                        f"match version_release {build.version_release!r}"
                    ),
                    fields=["user_agent_data.platform_version"],
                )
            )
        if uad.model and uad.model != build.model:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message=(
                        f"client hint model {uad.model!r} does not match the device "
                        f"model {build.model!r}"
                    ),
                    fields=["user_agent_data.model", "android_build.model"],
                )
            )
        if uad.wow64:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message="wow64 must be false on Android",
                    fields=["user_agent_data.wow64"],
                )
            )
        # The brand list must contain the Chromium brand and the "Not?A_Brand"
        # brand that Chromium sends to avoid fingerprinting.
        brand_names = {b.brand for b in uad.brands}
        if brand_names and not any("Chromium" in b or "Google Chrome" in b for b in brand_names):
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message="the brand list must contain a Chromium/Chrome brand",
                    fields=["user_agent_data.brands"],
                )
            )

        self._check_client_hints_against_version(config, errors, warnings)
        self._check_full_version_agreement(config, errors, warnings)

    def _check_full_version_agreement(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        """Check that every statement of the browser version agrees.

        The version is stated in four places: the reduced user agent,
        the brand list, the full version list, and the full version
        itself.  Chromium freezes the user agent to ``<major>.0.0.0``
        but keeps the real build in the high-entropy hints, so the two
        are *meant* to disagree on the last three fields.

        What must agree is the major.  A profile whose brand list
        announces Chrome 121 while its full version list announces
        120 is telling a site two different stories, and a site that
        reads both learns that the profile is assembled rather than
        observed.  The build number is checked against the range the
        major actually spans, because a build number from another
        branch is just as impossible.
        """

        version = getattr(config, "browser_version", None)
        if version is None:
            return

        uad = config.user_agent_data
        major = version.major

        # The brands and the full version list both carry a version per
        # brand.  Every one of them has to belong to the declared
        # major.
        for brand in uad.brands:
            if brand.brand == "Not)A;Brand":
                # The GREASE brand is a number that means nothing; it is
                # there precisely so that it cannot be checked.
                continue
            if not brand.version.isdigit():
                continue
            if int(brand.version) != major:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="client_hints",
                        message=(
                            f"the {brand.brand} brand reports version "
                            f"{brand.version!r} but the profile declares "
                            f"Chromium {major}"
                        ),
                        fields=["user_agent_data.brands"],
                    )
                )

        for brand in uad.full_version_list:
            if brand.brand == "Not)A;Brand":
                continue
            parts = brand.version.split(".")
            if not parts or not parts[0].isdigit():
                continue
            if int(parts[0]) != major:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="client_hints",
                        message=(
                            f"the full version list reports {brand.version!r} "
                            f"for {brand.brand} but the profile declares "
                            f"Chromium {major}"
                        ),
                        fields=["user_agent_data.full_version_list"],
                    )
                )
            elif len(parts) >= 3 and parts[2].isdigit():
                # The build number has to be one the major can be
                # found at.  A build from another branch is impossible.
                try:
                    from .versions import build_range_for

                    low, high = build_range_for(major)
                except Exception:  # noqa: BLE001 - an unknown major is not fatal
                    return
                build = int(parts[2])
                if not low <= build <= high:
                    errors.append(
                        ConsistencyIssue(
                            severity="error",
                            axis="client_hints",
                            message=(
                                f"the build number {build} is outside the range "
                                f"{low}-{high} that Chromium {major} spans"
                            ),
                            fields=["user_agent_data.full_version_list"],
                        )
                    )

        # The full version itself.
        full = version.full_version
        if full:
            parts = full.split(".")
            if parts and parts[0].isdigit() and int(parts[0]) != major:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="client_hints",
                        message=(
                            f"the full version {full!r} does not belong to the "
                            f"declared major {major}"
                        ),
                        fields=["browser_version.full_version"],
                    )
                )

    def _check_client_hints_against_version(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        """Check the hints against the capabilities of the declared core.

        A hint that the declared core cannot produce is an inconsistency:
        the profile claims a capability the running browser does not have.
        The reverse -- a hint the core *would* send but the profile omits --
        is a warning, because it is a weaker signal.
        """

        version = getattr(config, "browser_version", None)
        if version is None:
            return

        try:
            from .versions import DEFAULT_REGISTRY

            caps = DEFAULT_REGISTRY.capabilities(version.major)
        except Exception:  # noqa: BLE001 - an unknown major is not fatal here
            return

        headers = config.http_headers
        uad = config.user_agent_data

        # (the header field, the capability entry, the value that is sent)
        hinted = (
            (
                "sec_ch_ua_full_version_list",
                "Sec-CH-UA-Full-Version-List",
                headers.sec_ch_ua_full_version_list,
            ),
            ("sec_ch_ua_arch", "Sec-CH-UA-Arch", headers.sec_ch_ua_arch),
            ("sec_ch_ua_bitness", "Sec-CH-UA-Bitness", headers.sec_ch_ua_bitness),
            ("sec_ch_ua_model", "Sec-CH-UA-Model", headers.sec_ch_ua_model),
        )

        for field_name, hint, value in hinted:
            supported = caps.has_client_hint(hint)
            if supported and not value:
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="client_hints",
                        message=(
                            f"Chromium {version.major} sends {hint}, but the profile "
                            "does not declare it"
                        ),
                        fields=[f"http_headers.{field_name}"],
                    )
                )
            elif not supported and value:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="client_hints",
                        message=(
                            f"{hint} was introduced in Chromium 119 and later; "
                            f"Chromium {version.major} does not send it, but the "
                            "profile declares it"
                        ),
                        fields=[f"http_headers.{field_name}"],
                    )
                )

        # The declared brands must match the declared major exactly.
        brands = {b.brand: b.version for b in uad.brands}
        for brand in ("Chromium", "Google Chrome"):
            if brand in brands and brands[brand] != str(version.major):
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="client_hints",
                        message=(
                            f"the {brand} brand reports {brands[brand]!r} but the "
                            f"profile declares Chromium {version.major}"
                        ),
                        fields=["user_agent_data.brands"],
                    )
                )

    def _check_webgl(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        webgl = config.webgl
        renderer = webgl.unmasked_renderer

        # A renderer of a mobile part is an Android GPU, whatever family
        # its name mentions: the Tegra parts are called "ULP GeForce" and
        # the Intel Atom tablets carry "Intel HD Graphics".  The mobile
        # test therefore comes first, and only a renderer that is not a
        # known mobile part can be a desktop anomaly.
        if _is_mobile_gpu_renderer(renderer):
            pass
        elif any(marker in renderer for marker in _DESKTOP_GPU_MARKERS):
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webgl_params",
                    message=(
                        f"unmasked_renderer {renderer!r} is a desktop GPU string and "
                        "would immediately identify the profile as non-Android"
                    ),
                    fields=["webgl.unmasked_renderer"],
                )
            )
        elif not any(marker in renderer for marker in _ANDROID_GPU_MARKERS):
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="webgl_params",
                    message=f"unmasked_renderer {renderer!r} is not a known Android GPU",
                    fields=["webgl.unmasked_renderer"],
                )
            )

        if webgl.mode == "noise" and not webgl.image_noise.enabled:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="webgl_pixel",
                    message="the WebGL mode is 'noise' but the image noise is disabled",
                    fields=["webgl.image_noise.enabled"],
                )
            )

        # The image-noise value must have at least 17 significant digits and
        # must be reproducible from the seed.
        if webgl.image_noise.enabled:
            value = webgl.image_noise.value
            digits = len(value.replace("-", "").replace(".", "").lstrip("0"))
            if digits < 17:
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="webgl_pixel",
                        message=(
                            f"the image noise value {value!r} carries only {digits} "
                            "significant digits; 17 are required"
                        ),
                        fields=["webgl.image_noise.value"],
                    )
                )
            deriver = SeedDeriver(
                config.seed_config.master_seed,
                config.seed_config.seed_epoch,
                config.seed_config.nudge_step,
            )
            expected = deriver.derive_webgl_image_value(renderer)
            if expected != value:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="webgl_pixel",
                        message=(
                            "the WebGL image noise value does not match the value derived "
                            "from the profile seed; it would change between restarts"
                        ),
                        fields=["webgl.image_noise.value", "seed_config.master_seed"],
                    )
                )

        # The GPU parameters must match the catalog for known renderers.
        preset = _preset_for_renderer(renderer)
        if preset is not None:
            gpu = preset.gpu
            params = webgl.parameters
            # A unit may ship with a memory configuration that halves the
            # addressable texture size.  That is a property of the unit
            # rather than of the GPU model, and it is applied as a
            # consistent set, so the halved value is just as legitimate
            # as the catalogue one.
            #
            # The tier table is the authority: it holds every limit set the
            # model is known to produce, and each of them moves the whole
            # addressable-memory group together.
            allowed = {tier.max_texture_size for tier in build_tier_table(gpu)}
            if params.max_texture_size not in allowed:
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="webgl_params",
                        message=(
                            f"max_texture_size {params.max_texture_size} does not match the "
                            f"catalog value {gpu.max_texture_size} for {renderer}"
                        ),
                        fields=["webgl.parameters.max_texture_size"],
                    )
                )

            # The whole set has to move together.  A card that samples a
            # 16384 texture but cannot render into a 16384 renderbuffer is
            # not a configuration any driver produces, and a site that
            # compares the two numbers finds it in one read.
            tier = tier_for(gpu, webgl.parameter_set.tier)
            expected = tier.limits
            group = (
                ("max_texture_size", params.max_texture_size, expected.max_texture_size),
                (
                    "max_cube_map_texture_size",
                    params.max_cube_map_texture_size,
                    expected.max_cube_map_texture_size,
                ),
                (
                    "max_renderbuffer_size",
                    params.max_renderbuffer_size,
                    expected.max_renderbuffer_size,
                ),
            )
            for name, actual, wanted in group:
                if actual != wanted:
                    errors.append(
                        ConsistencyIssue(
                            severity="error",
                            axis="webgl_params",
                            message=(
                                f"{name} is {actual} but the tier of {renderer} this unit "
                                f"reports has {wanted}; the addressable memory limits of "
                                "a card always move together"
                            ),
                            fields=[f"webgl.parameters.{name}"],
                        )
                    )
            if list(params.max_viewport_dims) != list(expected.max_viewport_dims):
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="webgl_params",
                        message=(
                            f"max_viewport_dims is {list(params.max_viewport_dims)} but the "
                            f"tier of {renderer} this unit reports has "
                            f"{list(expected.max_viewport_dims)}"
                        ),
                        fields=["webgl.parameters.max_viewport_dims"],
                    )
                )

        # The parameter surface needs a per-unit salt.  The limit table
        # itself carries only a handful of bits -- the tier index is
        # worth about two -- and the entropy budget demands 26.6 bits of
        # every identifying field.
        if not webgl.parameter_set.salt:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webgl_params",
                    message=(
                        "the WebGL parameter surface needs a per-device salt; without it "
                        "every unit of a GPU model reports the same limit table"
                    ),
                    fields=["webgl.parameter_set.salt"],
                )
            )
        if webgl.parameter_set.source_renderer and (
            webgl.parameter_set.source_renderer != webgl.unmasked_renderer
        ):
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webgl_params",
                    message=(
                        f"the parameter table was derived for "
                        f"{webgl.parameter_set.source_renderer!r} but the profile reports "
                        f"{webgl.unmasked_renderer!r}"
                    ),
                    fields=["webgl.parameter_set.source_renderer"],
                )
            )

    def _check_geo(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        geo = config.geo_intelligence
        tz = config.timezone
        locale = config.locale
        geo_sync = config.geo_sync
        headers = config.http_headers

        if geo_sync.enabled and geo.country_code:
            # The country in the exit IP and the country in the locale must agree.
            locale_country = locale.primary.split("-")[-1].upper() if "-" in locale.primary else ""
            if locale_country and locale_country != geo.country_code.upper():
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="geo_timezone",
                        message=(
                            f"the locale {locale.primary!r} does not match the proxy exit "
                            f"country {geo.country_code!r}"
                        ),
                        fields=["locale.primary", "geo_intelligence.country_code"],
                    )
                )

            # The timezone must be plausible for the country.
            if (
                tz.iana_name
                and geo.country_code
                and not _timezone_plausible_for_country(tz.iana_name, geo.country_code)
            ):
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="geo_timezone",
                        message=(
                            f"timezone {tz.iana_name!r} looks unusual for country "
                            f"{geo.country_code!r}"
                        ),
                        fields=["timezone.iana_name"],
                    )
                )

            # The Accept-Language must reflect the exit country.
            accept = headers.accept_language
            if accept and locale.primary and not accept.startswith(locale.primary):
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="geo_timezone",
                        message=(
                            f"Accept-Language {accept!r} does not start with the primary "
                            f"locale {locale.primary!r}"
                        ),
                        fields=["http_headers.accept_language"],
                    )
                )

        # The timezone offset must match the IANA zone.
        expected_offset = _iana_offset(tz.iana_name, tz.dst_active)
        if expected_offset is not None and tz.offset_minutes != expected_offset:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="geo_timezone",
                    message=(
                        f"timezone offset {tz.offset_minutes} does not match the IANA "
                        f"offset {expected_offset} for {tz.iana_name}"
                    ),
                    fields=["timezone.offset_minutes"],
                )
            )

    def _check_headers(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        headers = config.http_headers
        if headers.sec_ch_ua_mobile != "?1":
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message="Sec-CH-UA-Mobile must be ?1 on Android",
                    fields=["http_headers.sec_ch_ua_mobile"],
                )
            )
        if headers.sec_ch_ua_platform != '"Android"':
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="client_hints",
                    message='Sec-CH-UA-Platform must be "Android"',
                    fields=["http_headers.sec_ch_ua_platform"],
                )
            )

    def _check_webrtc(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        """Check that WebRTC cannot expose the host's real address.

        WebRTC is the most reliable way for a page to learn the address of
        the machine it is running on: the ICE candidates carry the local
        interfaces and, when the policy is permissive, the public address
        as well.  A profile that uses a proxy but leaves WebRTC open hands
        the site's server the very address the operator paid to hide.
        """

        webrtc = config.webrtc
        proxy = config.proxy
        proxied = bool(proxy.enabled) and not proxy.is_direct

        # A profile that claims a proxied address must not let WebRTC talk
        # to the network directly.
        if proxied and webrtc.ice_transport_policy == "all":
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webrtc",
                    message=(
                        "the ICE transport policy is 'all' while the profile uses a "
                        "proxy: the page can learn the host's real address from the "
                        "WebRTC candidates"
                    ),
                    fields=["webrtc.ice_transport_policy"],
                )
            )

        if proxied and not webrtc.block_non_proxied_udp:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webrtc",
                    message=(
                        "the non-proxied UDP is not blocked while the profile uses a "
                        "proxy: a WebRTC handshake can leave the proxy and expose the "
                        "host's address"
                    ),
                    fields=["webrtc.block_non_proxied_udp"],
                )
            )

        # The local candidates have to be hidden as well.  The mDNS
        # obfuscation replaces them with the ".local" names that every
        # modern browser sends; without it the real interface names and
        # addresses are disclosed.
        if proxied and not webrtc.mdns_enabled:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webrtc",
                    message=(
                        "the mDNS obfuscation is disabled while the profile uses a "
                        "proxy: the local network interfaces are disclosed to the page"
                    ),
                    fields=["webrtc.mdns_enabled"],
                )
            )

        # A spoofed address must be a plausible one.  A private address
        # can never be the public address of a proxy exit, so claiming one
        # is a contradiction rather than a weak signal.
        if webrtc.spoofed_public_ip_v4 and webrtc.spoofed_public_ip_v4.startswith(
            (
                "10.",
                "192.168.",
                "127.",
                "169.254.",
                "172.16.",
                "172.17.",
                "172.18.",
                "172.19.",
                "172.2",
                "172.30.",
                "172.31.",
                "0.",
            )
        ):
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webrtc",
                    message=(
                        f"the spoofed public address {webrtc.spoofed_public_ip_v4!r} "
                        "is a private address; no internet-facing site can see it"
                    ),
                    fields=["webrtc.spoofed_public_ip_v4"],
                )
            )

        # The spoofed address has to agree with the proxy's exit address.
        geo = config.geo_intelligence
        if (
            proxied
            and webrtc.spoofed_public_ip_v4
            and geo.exit_ip_v4
            and webrtc.spoofed_public_ip_v4 != geo.exit_ip_v4
        ):
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="webrtc",
                    message=(
                        f"the spoofed WebRTC address {webrtc.spoofed_public_ip_v4!r} "
                        f"differs from the proxy exit address {geo.exit_ip_v4!r}: a "
                        "page that compares the two sees the mismatch"
                    ),
                    fields=["webrtc.spoofed_public_ip_v4", "geo_intelligence.exit_ip_v4"],
                )
            )

    def _check_privacy(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        # The permission mix must look like a real phone: geolocation and
        # notifications are usually granted on a personal device, while
        # clipboard-read and midi are almost always denied.
        policies = config.permissions.policies
        if policies.get("midi") == "granted":
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="permissions",
                    message="the MIDI permission is granted on virtually no Android device",
                    fields=["permissions.policies.midi"],
                )
            )
        if policies.get("geolocation") == "denied" and policies.get("notifications") == "denied":
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="permissions",
                    message=(
                        "denying both geolocation and notifications is atypical for a "
                        "long-lived profile"
                    ),
                    fields=["permissions.policies"],
                )
            )

    def _check_hardware_surfaces(
        self,
        config: ProfileConfig,
        errors: list[ConsistencyIssue],
        warnings: list[ConsistencyIssue],
    ) -> None:
        """Validate the surfaces that have no rule group of their own.

        The canvas, the audio context, the geometry perturbation, the
        media devices, the motion sensors, the storage estimate, the
        WebGPU adapter and the font list are all guarded axes.  A profile
        that is broken on any of them is a profile a site can single out,
        so they are checked here rather than being left to the schema
        alone.
        """

        # --- canvas ---
        canvas = config.canvas_2d
        if canvas.mode == "noise" and canvas.noise_strength <= 0.0:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="canvas_2d",
                    message=(
                        "a canvas in noise mode must perturb the rendering; "
                        "a zero strength leaves the device identical to every "
                        "other device that did not perturb it either"
                    ),
                    fields=["canvas_2d.noise_strength"],
                )
            )

        # The readback timing.  A profile that leaves the canvas readback
        # on the host's clock reports the timing of the *build machine*,
        # and a site that measures the readback groups the whole fleet
        # into one bucket.
        timing = canvas.timing
        if not timing.enabled:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="canvas_2d",
                    message=(
                        "the canvas readback timing shield is disabled; the profile "
                        "reports the timing of the host, which groups every profile "
                        "built on that host"
                    ),
                    fields=["canvas_2d.timing.enabled"],
                )
            )
        else:
            if not 0.0 < timing.delay_ms <= 3.0:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="canvas_2d",
                        message=(
                            f"the canvas readback delay is {timing.delay_ms} ms; a delay "
                            "of zero is no delay and a delay above three milliseconds is "
                            "a signal of its own"
                        ),
                        fields=["canvas_2d.timing.delay_ms"],
                    )
                )
            if not 0.0 < timing.read_pixels_delay_ms <= 3.0:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="canvas_2d",
                        message=(
                            f"the WebGL readback delay is {timing.read_pixels_delay_ms} ms; "
                            "a delay of zero is no delay and a delay above three "
                            "milliseconds is a signal of its own"
                        ),
                        fields=["canvas_2d.timing.read_pixels_delay_ms"],
                    )
                )

        # --- audio ---
        audio = config.audio
        if audio.channel_count > audio.max_channel_count:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="audio",
                    message=(
                        f"the audio context reports {audio.channel_count} channels "
                        f"but supports at most {audio.max_channel_count}"
                    ),
                    fields=["audio.channel_count", "audio.max_channel_count"],
                )
            )
        if audio.mode == "native_quantized" and not audio.respect_core_quantization:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="audio",
                    message=(
                        "a native quantized audio context must respect the core "
                        "quantization, otherwise the latency falls outside the "
                        "buckets a real Android device produces"
                    ),
                    fields=["audio.respect_core_quantization"],
                )
            )

        # --- geometry perturbation ---
        rects = config.rects
        if (
            rects.mode == "noise"
            and rects.delta_x == rects.delta_y == 0
            and rects.delta_width == rects.delta_height == 0
        ):
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="rects",
                    message=(
                        "the geometry perturbation is enabled but every delta is "
                        "zero, so the rectangles are not perturbed at all"
                    ),
                    fields=["rects.delta_x", "rects.delta_y"],
                )
            )

        # --- media devices ---
        media = config.media_devices
        if media.mode == "deterministic_mock" and not media.audio_inputs and not media.video_inputs:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="media_devices",
                    message=(
                        "the media devices are mocked but the device reports no "
                        "camera and no microphone, which no Android handset does"
                    ),
                    fields=["media_devices.audio_inputs", "media_devices.video_inputs"],
                )
            )

        # --- motion sensors ---
        sensors = config.sensors
        if sensors.enabled:
            gravity = sensors.gravity.z
            if not (8.0 <= gravity <= 11.0):
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="sensors",
                        message=(
                            f"the gravity reading is {gravity}, which is outside the "
                            "range Earth's gravity can produce"
                        ),
                        fields=["sensors.gravity.z"],
                    )
                )
            # A phone lying on a desk still shows a little movement.
            if (
                sensors.motion_profile == "stationary_micro_jitter"
                and abs(sensors.accelerometer.z - gravity) > 0.5
            ):
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="sensors",
                        message=(
                            "a stationary phone's accelerometer must stay close to "
                            "the gravity reading"
                        ),
                        fields=["sensors.accelerometer.z"],
                    )
                )

        # --- storage estimate ---
        storage = config.storage_network
        if storage.usage_bytes > storage.quota_bytes:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="storage_network",
                    message=(
                        f"the reported usage ({storage.usage_bytes}) exceeds the "
                        f"reported quota ({storage.quota_bytes})"
                    ),
                    fields=["storage_network.usage_bytes", "storage_network.quota_bytes"],
                )
            )
        # The downlink ceiling depends on the *link*, not on the effective
        # type alone.  A wifi link reports the effective type "4g" -- the
        # Network Information API only has four buckets -- but it can
        # carry far more than 10 Mbit/s.  A cellular 4g link cannot.
        if (
            storage.effective_type == "4g"
            and storage.connection_type == "cellular"
            and storage.downlink > 10.0
        ):
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="storage_network",
                    message=(
                        "a 4g cellular connection never reports a downlink "
                        "above 10 Mbit/s"
                    ),
                    fields=["storage_network.downlink"],
                )
            )
        if storage.effective_type == "3g" and storage.downlink > 3.0:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="storage_network",
                    message="a 3g connection never reports a downlink above 3 Mbit/s",
                    fields=["storage_network.downlink"],
                )
            )
        if storage.effective_type == "2g" and storage.downlink > 0.7:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="storage_network",
                    message="a 2g connection never reports a downlink above 0.7 Mbit/s",
                    fields=["storage_network.downlink"],
                )
            )

        # --- WebGPU ---
        webgpu = config.webgpu
        if webgpu.enabled and not webgpu.limits:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webgpu",
                    message=(
                        "an enabled WebGPU adapter must expose a limit table; "
                        "an empty one is not something a real GPU produces"
                    ),
                    fields=["webgpu.limits"],
                )
            )
        if not webgpu.salt:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="webgpu",
                    message=(
                        "the WebGPU surface needs a per-device salt; without it "
                        "every unit of a GPU model reports the same adapter"
                    ),
                    fields=["webgpu.salt"],
                )
            )
        if webgpu.enabled:
            # The WebGPU adapter and the WebGL context describe the same
            # physical GPU.  A site compares ``navigator.gpu``'s limits
            # with ``WebGLRenderingContext.getParameter`` and a
            # disagreement there is a hard inconsistency.
            expected_texture = float(config.webgl.parameters.max_texture_size)
            reported = webgpu.limits.get("maxTextureDimension2D")
            if reported is not None and reported != expected_texture:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="webgpu",
                        message=(
                            f"the WebGPU texture limit is {reported} but the WebGL "
                            f"texture limit is {expected_texture}; both describe the "
                            "same GPU"
                        ),
                        fields=["webgpu.limits.maxTextureDimension2D"],
                    )
                )
            if webgpu.adapter.description != config.webgl.unmasked_renderer:
                warnings.append(
                    ConsistencyIssue(
                        severity="warning",
                        axis="webgpu",
                        message=(
                            "the WebGPU adapter description does not match the WebGL "
                            "renderer string of the same device"
                        ),
                        fields=["webgpu.adapter.description"],
                    )
                )

        # --- fonts ---
        fonts = config.fonts
        if not fonts.installed:
            warnings.append(
                ConsistencyIssue(
                    severity="warning",
                    axis="fonts",
                    message="no fonts are installed, which no Android device reports",
                    fields=["fonts.installed"],
                )
            )
        elif not fonts.salt:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="fonts",
                    message=(
                        "the font list needs a per-device salt; without it every "
                        "unit of a locale shares the same font configuration"
                    ),
                    fields=["fonts.salt"],
                )
            )

        # The font timing shield.  Without it the timing of a font probe
        # depends on whether the family is really on the device, and a
        # site that times the probe recovers the font configuration
        # without ever reading the installed list.
        font_timing = fonts.timing
        if not font_timing.enabled:
            errors.append(
                ConsistencyIssue(
                    severity="error",
                    axis="fonts",
                    message=(
                        "the font timing shield is disabled; the timing of a font "
                        "probe then depends on whether the family is installed, "
                        "which leaks the font configuration"
                    ),
                    fields=["fonts.timing.enabled"],
                )
            )
        else:
            if not 0.0 < font_timing.delay_ms <= 2.0:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="fonts",
                        message=(
                            f"the font probe delay is {font_timing.delay_ms} ms; a delay "
                            "of zero is no delay, and a delay below the resolution of "
                            "the browser's timer is invisible"
                        ),
                        fields=["fonts.timing.delay_ms"],
                    )
                )
            if not 0.0 < font_timing.measure_delay_ms <= 2.0:
                errors.append(
                    ConsistencyIssue(
                        severity="error",
                        axis="fonts",
                        message=(
                            f"the text measurement delay is {font_timing.measure_delay_ms} "
                            "ms; a delay of zero is no delay, and a delay below the "
                            "resolution of the browser's timer is invisible"
                        ),
                        fields=["fonts.timing.measure_delay_ms"],
                    )
                )


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------


def _preset_for_renderer(renderer: str):
    """Find the catalog preset whose GPU matches ``renderer``."""
    for preset in DEVICE_PRESETS:
        if preset.gpu.unmasked_renderer == renderer:
            return preset
    return None


def _tzdata_available() -> bool:
    """Whether the IANA time zone database is available on this machine.

    Windows has no system copy of it.  Without the ``tzdata`` package
    the whole database is missing, and then *every* zone fails to
    resolve -- which would make every profile claim UTC with a zero
    offset.  The checks below have to know the difference between
    "this zone does not exist" and "the database is missing",
    otherwise they would pass silently on a machine that cannot
    possibly produce a correct profile.
    """

    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Europe/Vienna")
    except Exception:
        return False
    return True


def tzdata_status() -> tuple[bool, str]:
    """Whether the time zone database is usable, and why not if it is not.

    The control plane reports this in ``nova doctor``.  A machine
    without the database produces profiles that claim UTC, which is one
    of the loudest signals a site can detect, so it has to be visible
    before the fleet is created rather than after.
    """

    if _tzdata_available():
        return True, "the IANA time zone database is available"
    return False, (
        "the IANA time zone database is missing -- install the 'tzdata' package. "
        "Without it every profile reports UTC with a zero offset."
    )


def _iana_offset(iana_name: str, dst_active: bool) -> int | None:
    """The UTC offset in minutes for an IANA zone, if the zone is known.

    The offset is read at a *fixed* reference instant, not at the wall
    clock.  The generator uses the same instant, and a check that read
    the current time would report a contradiction for every zone that
    observes daylight saving: the profile of a Winnipeg handset says
    -360 because the profile was built at the January reference instant,
    and a checker that read the current time in November would expect
    -300 and call the profile wrong.

    The instant is fixed for a second reason as well: a profile has to
    be reproducible, so the offset it carries may not depend on the day
    the profile is generated.

    ``dst_active`` selects which of the two offsets of the zone is
    wanted: the standard offset when it is false and the daylight
    saving offset when it is true.  The two are found by *searching* the
    reference year for an instant on which the zone really is in that
    state, and not by picking a month: the southern hemisphere runs its
    daylight saving the other way round, so a fixed month would give
    Santiago the standard offset when it is in fact in the summer one.

    Returns ``None`` only when the zone name is empty or the database is
    genuinely missing.  A missing database is a *deployment* failure, not
    a profile failure, so it is reported separately -- see
    :func:`tzdata_status`.
    """

    if not iana_name:
        return None
    if not _tzdata_available():
        return None
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(iana_name)
    except Exception:
        return None

    # The reference year of the generator.  The offsets are read from it
    # rather than from the current year, so that a profile stays
    # reproducible from one year to the next.
    year = 2026
    offset = _offset_for_dst_state(zone, year, dst_active)
    if offset is None:
        return None
    return int(offset.total_seconds() // 60)


def _offset_for_dst_state(
    zone: object,
    year: int,
    dst_active: bool,
) -> object | None:
    """The UTC offset of a zone at an instant when it is in a given DST state.

    The reference year is scanned fortnight by fortnight for an instant
    on which the zone really is in the wanted state, and the offset of
    that instant is returned.  A zone that never enters daylight
    saving has one offset all year, so the first instant of the year
    answers for both states.
    """

    import datetime as _dt

    start = _dt.datetime(year, 1, 1, 12, 0, tzinfo=_dt.UTC)
    for step in range(0, 366, 14):
        instant = start + _dt.timedelta(days=step)
        local = instant.astimezone(zone)  # type: ignore[attr-defined]
        delta = local.dst()
        in_dst = bool(delta and delta.total_seconds() != 0)
        if in_dst == dst_active:
            offset = local.utcoffset()
            if offset is not None:
                return offset
    return None


#: A coarse country -> timezone-region map.  Used to catch obviously wrong
#: pairings (for example a Vietnamese locale with a German exit IP).
_COUNTRY_TIMEZONE_HINTS: dict[str, tuple[str, ...]] = {
    "VN": ("Asia/Ho_Chi_Minh",),
    "JP": ("Asia/Tokyo",),
    "KR": ("Asia/Seoul",),
    "CN": ("Asia/Shanghai", "Asia/Urumqi"),
    "SG": ("Asia/Singapore",),
    "TH": ("Asia/Bangkok",),
    "ID": ("Asia/Jakarta",),
    "MY": ("Asia/Kuala_Lumpur",),
    "PH": ("Asia/Manila",),
    "IN": ("Asia/Kolkata",),
    "PK": ("Asia/Karachi",),
    "AE": ("Asia/Dubai",),
    "SA": ("Asia/Riyadh",),
    "TR": ("Europe/Istanbul",),
    "RU": ("Europe/Moscow", "Asia/Yekaterinburg"),
    "DE": ("Europe/Berlin", "Europe/Busingen"),
    "FR": ("Europe/Paris",),
    "GB": ("Europe/London",),
    "NL": ("Europe/Amsterdam",),
    "ES": ("Europe/Madrid",),
    "IT": ("Europe/Rome",),
    "PL": ("Europe/Warsaw",),
    "UA": ("Europe/Kyiv", "Europe/Kiev"),
    "US": (
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "America/Phoenix",
        # Hawaii and Alaska are part of the United States; the profile
        # claims the country it really is in.
        "Pacific/Honolulu",
        "America/Anchorage",
    ),
    "CA": ("America/Toronto", "America/Vancouver", "America/Montreal"),
    "BR": ("America/Sao_Paulo",),
    "MX": ("America/Mexico_City",),
    "AU": ("Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane"),
    "NZ": ("Pacific/Auckland",),
    "ZA": ("Africa/Johannesburg",),
    "EG": ("Africa/Cairo",),
    "NG": ("Africa/Lagos",),
    "KE": ("Africa/Nairobi",),
}


def _timezone_plausible_for_country(iana_name: str, country_code: str) -> bool:
    """Whether an IANA zone is plausible for a country.

    The check is deliberately coarse -- it only rejects a zone that is
    clearly on another continent, because a country may span several zones.
    """
    hints = _COUNTRY_TIMEZONE_HINTS.get(country_code.upper())
    if not hints:
        return True
    if iana_name in hints:
        return True
    # Accept a zone that shares the region's area prefix.
    for hint in hints:
        prefix = hint.rsplit("/", 1)[0]
        if iana_name.startswith(prefix + "/"):
            return True
    return False


__all__ = [
    "ConsistencyIssue",
    "ConsistencyReport",
    "ConsistencyError",
    "ConsistencyResolver",
    "tzdata_status",
]
