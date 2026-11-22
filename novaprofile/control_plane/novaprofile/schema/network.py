"""Network schema groups: proxy, proxy rotation, geo intelligence, geo sync,
WebRTC, DNS and HTTP headers.

Group 2 of the specification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    DnsMode,
    ProxyProtocol,
    ProxyRotationMode,
    WebRtcMode,
)

# --------------------------------------------------------------------------
# 2.1 Proxy
# --------------------------------------------------------------------------


class Proxy(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    protocol: ProxyProtocol = "direct"
    host: str = ""
    port: int = Field(default=0, ge=0, le=65535)
    username: str = ""
    password: str = ""  # stored encrypted by the control plane
    auth_method: Literal["none", "userpass", "token"] = "none"
    connect_timeout_ms: int = Field(default=30000, ge=1000)
    read_timeout_ms: int = Field(default=60000, ge=1000)

    @field_validator("host")
    @classmethod
    def _strip_host(cls, v: str) -> str:
        return v.strip()

    @field_validator("password")
    @classmethod
    def _strip_password(cls, v: str) -> str:
        # Passwords are stored encrypted; strip whitespace but keep the payload
        # intact (it is base64 and may contain '=' padding).
        return v.strip()

    @property
    def is_direct(self) -> bool:
        return not self.enabled or self.protocol == "direct" or not self.host


# --------------------------------------------------------------------------
# 2.2 Proxy rotation
# --------------------------------------------------------------------------


class ProxyRotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    mode: ProxyRotationMode = "manual"
    interval_sec: int = Field(default=600, ge=10)
    rotate_url: str = ""
    http_method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    cooldown_sec: int = Field(default=30, ge=0)


# --------------------------------------------------------------------------
# 2.3 Geo intelligence
# --------------------------------------------------------------------------


class GeoIntelligence(BaseModel):
    """What the proxy exit IP looks like from the outside."""

    model_config = ConfigDict(frozen=True)

    exit_ip_v4: str = ""
    exit_ip_v6: str = ""
    country_code: str = ""
    country_name: str = ""
    region_code: str = ""
    region_name: str = ""
    city: str = ""
    postal_code: str = ""
    asn: int = Field(default=0, ge=0)
    isp: str = ""
    org: str = ""
    is_mobile_carrier: bool = False
    is_datacenter: bool = False
    latency_ms: int = Field(default=0, ge=0)
    checked_at: datetime | None = None


# --------------------------------------------------------------------------
# 2.4 Geo sync
# --------------------------------------------------------------------------


class GeoSync(BaseModel):
    """Which axes follow the proxy's physical location."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    lock_manual_overrides: bool = True
    sync_timezone: bool = True
    sync_locale: bool = True
    sync_geolocation: bool = True
    sync_webrtc_ip: bool = True


# --------------------------------------------------------------------------
# 2.5 WebRTC
# --------------------------------------------------------------------------


class WebRtc(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: WebRtcMode = "proxy_only"
    spoofed_public_ip_v4: str = ""
    spoofed_public_ip_v6: str = ""
    spoofed_local_ip_v4: str = ""
    mdns_enabled: bool = True
    block_non_proxied_udp: bool = True
    ice_transport_policy: Literal["all", "relay"] = "relay"

    @field_validator("spoofed_public_ip_v4")
    @classmethod
    def _valid_v4(cls, v: str) -> str:
        if v:
            parts = v.split(".")
            if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                raise ValueError(f"spoofed_public_ip_v4 {v!r} is not a valid IPv4 address")
        return v

    @field_validator("spoofed_public_ip_v6")
    @classmethod
    def _valid_v6(cls, v: str) -> str:
        if v and ":" not in v:
            raise ValueError(f"spoofed_public_ip_v6 {v!r} is not a valid IPv6 address")
        return v


# --------------------------------------------------------------------------
# 2.6 DNS
# --------------------------------------------------------------------------


class Dns(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: DnsMode = "proxy_remote"
    servers: list[str] = Field(default_factory=list)
    doh_url: str = ""
    dot_host: str = ""
    prevent_dns_leak: bool = True


# --------------------------------------------------------------------------
# 2.7 TLS handshake
# --------------------------------------------------------------------------


class Tls(BaseModel):
    """The shape of the TLS ClientHello.

    JA3 is the MD5 of the ClientHello -- the cipher suites, the extension
    order, the elliptic curves and the point formats, in the order the
    client sent them.  JA4 is the readable form of the same handshake.

    The handshake happens in the network service, before any script runs,
    so no amount of JavaScript can change it.  The only way to change it
    is to change the bytes the client puts on the wire, which means
    changing the network stack.  The native core does that: it installs
    the profile's cipher list, curve list, signature algorithm list and
    ALPN list on the BoringSSL context before the handshake.

    The order of the cipher suites and of the extensions is what a
    fingerprint is made of, so both are lists here rather than sets.
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["native", "spoof"] = "native"
    # The cipher suites, in the order they are offered.  The names are
    # the IANA names, e.g. "TLS_AES_128_GCM_SHA256".
    cipher_suites: list[str] = Field(default_factory=list)
    # The supported groups, in the order they are offered.  The names
    # are the ones BoringSSL uses, e.g. "X25519" or "P-256".
    supported_groups: list[str] = Field(default_factory=list)
    # The signature algorithms, in the order they are offered.
    signature_algorithms: list[str] = Field(default_factory=list)
    # The ALPN protocols, in the order they are offered.
    alpn_protocols: list[str] = Field(default_factory=list)
    # The TLS versions, from the oldest to the newest.
    versions: list[str] = Field(default_factory=list)
    # The extension order, by the name that BoringSSL uses.  An
    # extension that is not listed keeps the position that BoringSSL
    # would have given it.
    extension_order: list[str] = Field(default_factory=list)
    # The per-profile salt.  The handshake is the same for every profile
    # that uses the same browser build unless something in the profile
    # varies it, so the salt is what makes the handshake of two profiles
    # differ.
    salt: str = Field(default="", min_length=8, max_length=128)
    # Whether the GREASE extension is sent.
    grease_enabled: bool = True

    @field_validator("cipher_suites", "supported_groups", "signature_algorithms")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("the TLS lists must not repeat an entry")
        return v


# --------------------------------------------------------------------------
# 2.8 HTTP headers
# --------------------------------------------------------------------------



class HttpHeaders(BaseModel):
    """``Accept-Language`` and ``Sec-CH-UA-*`` values.

    The client-hint values must agree with the UA and the device build; the
    consistency resolver enforces that.
    """

    model_config = ConfigDict(frozen=True)

    accept_language: str = "en-US,en;q=0.9"
    dnt: str | None = None
    sec_ch_ua: str = ""
    sec_ch_ua_mobile: str = "?1"
    sec_ch_ua_platform: str = '"Android"'
    sec_ch_ua_platform_version: str = ""
    sec_ch_ua_arch: str = ""
    sec_ch_ua_bitness: str = ""
    sec_ch_ua_model: str = ""
    sec_ch_ua_full_version_list: str = ""
    custom_headers: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "Proxy",
    "ProxyRotation",
    "GeoIntelligence",
    "GeoSync",
    "WebRtc",
    "Dns",
    "Tls",
    "HttpHeaders",
]
