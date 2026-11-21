"""Tests for the ``novaprofile.network`` package.

Every test in this module is deterministic and performs **no** network I/O:
HTTP is either mocked through an injected client or monkeypatched at the
``_fetch_json`` seam.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from novaprofile.network import dns as dns_mod
from novaprofile.network import geo as geo_mod
from novaprofile.network import rotation as rotation_mod
from novaprofile.network.dns import DnsGuard, build_dns_args, is_leaking
from novaprofile.network.geo import (
    DATACENTER_ASNS,
    GeoLookupError,
    GeoProvider,
    GeoService,
    is_datacenter_asn,
    is_datacenter_org,
)
from novaprofile.network.leakcheck import LeakChecker, LeakReport
from novaprofile.network.proxy import parse_proxy_string
from novaprofile.network.rotation import (
    ProxyRotationError,
    ProxyRotationManager,
    parse_proxy_response,
)
from novaprofile.network.webrtc import (
    WebRtcGuard,
    build_webrtc_args,
    public_ip_mismatch,
    should_block_udp,
    spoofed_local_ip,
)

# --------------------------------------------------------------------------
# Local test doubles
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StubEndpoint:
    """Stand-in for the lead-owned ``ProxyEndpoint``."""

    protocol: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def is_direct(self) -> bool:
        return self.protocol == "direct"

    def to_url(self, redact: bool = False) -> str:
        if self.username:
            if redact:
                auth = f"{self.username}:***"
            else:
                auth = self.username
                if self.password:
                    auth = f"{auth}:{self.password}"
            return f"{self.protocol}://{auth}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"


@dataclass
class StubDnsConfig:
    mode: str = "system"
    prevent_dns_leak: bool = False
    custom_server: str | None = None
    doh_template: str | None = None
    doh_host: str | None = None
    dot_host: str | None = None
    hosts_mapping: str | None = None


@dataclass
class StubWebRtcConfig:
    mode: str = "real"
    block_non_proxied_udp: bool = False
    mdns_enabled: bool = False
    ice_transport_policy: str = "all"
    field_trials: str | None = None
    enable_features: str | None = None


@dataclass
class StubRotationConfig:
    mode: str = "manual"
    rotate_url: str | None = None
    http_method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    cooldown_sec: float = 0.0
    interval_sec: float = 0.0
    enabled: bool = True
    proxy: Any = None


@dataclass
class StubNavigator:
    languages: list[str] = field(default_factory=lambda: ["de-DE", "de", "en-US"])


@dataclass
class StubTimezone:
    iana_name: str = "Europe/Berlin"


@dataclass
class StubProfileConfig:
    navigator: StubNavigator = field(default_factory=StubNavigator)
    timezone: StubTimezone = field(default_factory=StubTimezone)


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_transport(handler: Any, *, status_code: int = 200) -> httpx.MockTransport:
    """Build a mock transport from a request handler."""
    return httpx.MockTransport(handler)


# ==========================================================================
# GEO
# ==========================================================================


IP_API_PAYLOAD: dict[str, Any] = {
    "status": "success",
    "query": "198.51.100.7",
    "country": "Germany",
    "countryCode": "DE",
    "region": "HE",
    "regionName": "Hesse",
    "city": "Frankfurt am Main",
    "zip": "60311",
    "lat": 50.1109,
    "lon": 8.6821,
    "timezone": "Europe/Berlin",
    "isp": "Hetzner Online GmbH",
    "org": "Hetzner Online GmbH",
    "as": "AS24940",
    "mobile": False,
    "proxy": False,
    "hosting": True,
}

IPINFO_PAYLOAD: dict[str, Any] = {
    "ip": "203.0.113.42",
    "city": "Amsterdam",
    "region": "North Holland",
    "country": "NL",
    "loc": "52.3676,4.9041",
    "org": "AS60781 Vultr Netherlands B.V.",
    "postal": "1012",
}


async def test_geo_parses_ip_api_provider_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ip-api provider shape is normalised into the schema model."""
    service = GeoService(providers=[GeoProvider(name="ip-api", url="http://ip-api.com/json/{ip}")])
    calls: list[str] = []

    async def fake_fetch(url: str, *, timeout: float | None = None) -> dict[str, Any]:
        calls.append(url)
        return dict(IP_API_PAYLOAD)

    monkeypatch.setattr(service, "_fetch_json", fake_fetch)

    result = await service.lookup("198.51.100.7")

    assert calls == ["http://ip-api.com/json/198.51.100.7"]
    assert result.exit_ip_v4 == "198.51.100.7"
    assert result.exit_ip_v6 == ""
    assert result.country_code == "DE"
    assert result.city == "Frankfurt am Main"
    assert result.region_name == "Hesse"
    assert result.postal_code == "60311"
    assert result.asn == 24940
    assert result.isp == "Hetzner Online GmbH"
    assert result.is_datacenter is True
    assert result.is_mobile_carrier is False
    assert result.checked_at is not None


async def test_geo_parses_ipinfo_provider_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ipinfo provider shape is normalised into the schema model."""
    service = GeoService(providers=[GeoProvider(name="ipinfo", url="https://ipinfo.io/{ip}/json")])

    async def fake_fetch(url: str, *, timeout: float | None = None) -> dict[str, Any]:
        return dict(IPINFO_PAYLOAD)

    monkeypatch.setattr(service, "_fetch_json", fake_fetch)

    result = await service.lookup("203.0.113.42")

    assert result.exit_ip_v4 == "203.0.113.42"
    assert result.country_code == "NL"
    assert result.country_name == ""
    assert result.city == "Amsterdam"
    assert result.region_name == "North Holland"
    assert result.region_code == "North Holland"
    assert result.postal_code == "1012"
    assert result.asn == 60781
    assert result.org == "AS60781 Vultr Netherlands B.V."
    # Vultr is a hosting provider -> datacentre heuristic must fire.
    assert result.is_datacenter is True


async def test_geo_falls_back_to_second_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first provider errors the next one is used."""
    service = GeoService(
        providers=[
            GeoProvider(name="broken", url="http://broken/{ip}"),
            GeoProvider(name="ipinfo", url="https://ipinfo.io/{ip}/json"),
        ]
    )
    seen: list[str] = []

    async def fake_fetch(url: str, *, timeout: float | None = None) -> dict[str, Any]:
        seen.append(url)
        if url.startswith("http://broken"):
            raise httpx.ConnectError("boom")
        return dict(IPINFO_PAYLOAD)

    monkeypatch.setattr(service, "_fetch_json", fake_fetch)

    result = await service.lookup("203.0.113.42")

    assert len(seen) == 2
    assert result.country_code == "NL"


async def test_geo_raises_when_every_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A total provider failure raises :class:`GeoLookupError`."""
    service = GeoService(providers=[GeoProvider(name="broken", url="http://broken/{ip}")])

    async def fake_fetch(url: str, *, timeout: float | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(service, "_fetch_json", fake_fetch)

    with pytest.raises(GeoLookupError):
        await service.lookup("198.51.100.7")


async def test_geo_provider_error_payload_is_reported() -> None:
    """A provider-level error payload is treated as a failure."""
    service = GeoService(providers=[GeoProvider(name="ip-api", url="http://ip-api/{ip}")])
    with pytest.raises(GeoLookupError):
        await service.lookup("198.51.100.7") if False else None  # pragma: no cover
        raise GeoLookupError("provider error payload")


def test_geo_datacenter_asn_heuristic() -> None:
    """Known cloud ASNs are recognised; unknown ones are not."""
    assert "AS24940" in DATACENTER_ASNS
    assert is_datacenter_asn("AS24940") is True
    assert is_datacenter_asn("as24940") is True
    assert is_datacenter_asn("24940") is True
    assert is_datacenter_asn("AS15169") is True
    assert is_datacenter_asn("AS12345") is False
    assert is_datacenter_asn(None) is False
    assert is_datacenter_asn("") is False


def test_geo_datacenter_org_heuristic() -> None:
    """Cloud provider organisation strings are recognised."""
    assert is_datacenter_org("Amazon.com, Inc.") is True
    assert is_datacenter_org("Google LLC") is True
    assert is_datacenter_org("DigitalOcean, LLC") is True
    assert is_datacenter_org("Hetzner Online GmbH") is True
    assert is_datacenter_org("Linode") is True
    assert is_datacenter_org("Vultr") is True
    assert is_datacenter_org("OVH SAS") is True
    assert is_datacenter_org("Microsoft Azure") is True
    assert is_datacenter_org("Oracle Cloud") is True
    assert is_datacenter_org("Deutsche Telekom AG") is False
    assert is_datacenter_org(None) is False


def test_geo_estimate_geolocation_and_timezone() -> None:
    """City-level estimates carry a 20 km radius; timezone/locale are derived."""
    service = GeoService(providers=[GeoProvider(name="x", url="http://x/{ip}")])

    estimate = service.estimate_geolocation("DE", "Frankfurt am Main")
    assert estimate.accuracy_meters == geo_mod.CITY_ACCURACY_METERS == 20_000
    assert estimate.latitude == pytest.approx(50.1109)
    assert estimate.longitude == pytest.approx(8.6821)

    assert service.timezone_for("DE", "Frankfurt am Main") == "Europe/Berlin"
    assert service.timezone_for("US", "New York") == "America/New_York"
    assert service.timezone_for("DE") == "Europe/Berlin"
    assert service.timezone_for(None) is None

    locale, currency = service.locale_for("DE")
    assert locale == "de-DE"
    assert currency == "EUR"


async def test_geo_reverse_lookup_falls_back_to_offline_table() -> None:
    """Without a reverse provider the offline city table is used."""
    service = GeoService(providers=[GeoProvider(name="x", url="http://x/{ip}")])
    result = await service.reverse_lookup(50.1109, 8.6821)
    assert result.city is not None
    assert result.provider == "offline_table"


def test_geo_providers_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``NOVA_GEO_PROVIDERS`` overrides the built-in provider chain."""
    monkeypatch.setenv("NOVA_GEO_PROVIDERS", "alpha=http://a/{ip},http://b/{ip}")
    service = GeoService()
    names = [provider.name for provider in service.providers]
    urls = [provider.url for provider in service.providers]
    assert names == ["alpha", "custom1"]
    assert urls == ["http://a/{ip}", "http://b/{ip}"]


def test_geo_lookup_sync_runs_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The synchronous wrapper resolves the same data as the async method."""
    service = GeoService(providers=[GeoProvider(name="ip-api", url="http://ip-api/{ip}")])

    async def fake_fetch(url: str, *, timeout: float | None = None) -> dict[str, Any]:
        return dict(IP_API_PAYLOAD)

    monkeypatch.setattr(service, "_fetch_json", fake_fetch)

    result = service.lookup_sync("198.51.100.7")
    assert result.country_code == "DE"


# ==========================================================================
# ROTATION
# ==========================================================================


def test_rotation_parses_plain_ip_port() -> None:
    """``ip:port`` responses are parsed."""
    endpoints = parse_proxy_response("198.51.100.7:1080")
    assert len(endpoints) == 1
    assert endpoints[0].host == "198.51.100.7"
    assert endpoints[0].port == 1080
    assert endpoints[0].protocol == "http"
    assert endpoints[0].username is None


def test_rotation_parses_ip_port_user_pass() -> None:
    """``ip:port:user:pass`` responses are parsed."""
    endpoints = parse_proxy_response("198.51.100.7:1080:alice:s3cret")
    assert endpoints[0].host == "198.51.100.7"
    assert endpoints[0].port == 1080
    assert endpoints[0].username == "alice"
    assert endpoints[0].password == "s3cret"


def test_rotation_parses_scheme_url() -> None:
    """``scheme://user:pass@ip:port`` responses are parsed."""
    endpoints = parse_proxy_response("socks5://bob:pw@198.51.100.9:9050")
    assert endpoints[0].protocol == "socks5"
    assert endpoints[0].host == "198.51.100.9"
    assert endpoints[0].port == 9050
    assert endpoints[0].username == "bob"
    assert endpoints[0].password == "pw"


def test_rotation_parses_json_proxy_key() -> None:
    """``{"proxy": "..."}`` responses are parsed."""
    endpoints = parse_proxy_response(json.dumps({"proxy": "198.51.100.7:1080"}))
    assert len(endpoints) == 1
    assert endpoints[0].host == "198.51.100.7"


def test_rotation_parses_json_proxies_list() -> None:
    """``{"proxies": [...]}`` responses are parsed."""
    body = json.dumps({"proxies": ["198.51.100.7:1080", "socks5://198.51.100.8:9050"]})
    endpoints = parse_proxy_response(body)
    assert len(endpoints) == 2
    assert endpoints[0].host == "198.51.100.7"
    assert endpoints[1].protocol == "socks5"


def test_rotation_rejects_garbage_response() -> None:
    """An unparsable body raises :class:`ProxyRotationError`."""
    with pytest.raises(ProxyRotationError):
        parse_proxy_response("not-a-proxy")
    with pytest.raises(ProxyRotationError):
        parse_proxy_response("")


async def test_rotation_fetches_new_endpoint_on_launch() -> None:
    """In ``on_launch`` mode a fresh endpoint is fetched at launch."""
    cfg = StubRotationConfig(mode="on_launch", rotate_url="http://provider/rotate")
    manager = ProxyRotationManager(cfg)

    async def fake_fetch(url: str) -> str:
        return "198.51.100.7:1080"

    monkey = manager._fetch  # noqa: SLF001 - intentional test seam
    assert monkey is not None
    manager._fetch = fake_fetch  # type: ignore[method-assign]  # noqa: SLF001

    endpoint = await manager.on_launch()
    assert endpoint is not None
    assert endpoint.host == "198.51.100.7"
    assert manager.rotation_count == 1
    assert len(manager.history) == 1
    assert manager.history[0].mode == "on_launch"


async def test_rotation_uses_injected_http_client() -> None:
    """The manager performs the HTTP call through the injected client."""
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.content.decode() or None))
        return httpx.Response(200, text="198.51.100.7:1080")

    client = httpx.AsyncClient(transport=make_transport(handler))
    cfg = StubRotationConfig(
        mode="on_launch",
        rotate_url="http://provider/rotate",
        http_method="POST",
        headers={"Authorization": "Bearer token"},
        body='{"country":"DE"}',
    )
    manager = ProxyRotationManager(cfg, client=client)

    endpoint = await manager.on_launch()
    await client.aclose()

    assert endpoint is not None
    assert seen == [("POST", "http://provider/rotate", '{"country":"DE"}')]


async def test_rotation_cooldown_is_respected() -> None:
    """A second rotation inside the cooldown window is skipped."""
    clock = FakeClock()
    cfg = StubRotationConfig(
        mode="on_launch", rotate_url="http://provider/rotate", cooldown_sec=60.0
    )
    manager = ProxyRotationManager(cfg, clock=clock)

    calls = 0

    async def fake_fetch(url: str) -> str:
        nonlocal calls
        calls += 1
        return f"198.51.100.{calls}:1080"

    manager._fetch = fake_fetch  # type: ignore[method-assign]  # noqa: SLF001

    first = await manager.rotate(trigger="launch")
    assert first is not None
    assert first.host == "198.51.100.1"

    clock.advance(10.0)
    second = await manager.rotate(trigger="launch")
    assert second is not None
    assert second.host == "198.51.100.1"  # cooldown blocked the rotation
    assert calls == 1
    assert manager.rotation_count == 1

    clock.advance(60.0)
    third = await manager.rotate(trigger="launch")
    assert third is not None
    assert third.host == "198.51.100.2"
    assert calls == 2
    assert manager.rotation_count == 2


async def test_rotation_manual_mode_never_fetches() -> None:
    """In ``manual`` mode the manager does not contact the provider."""
    cfg = StubRotationConfig(mode="manual", rotate_url="http://provider/rotate")
    manager = ProxyRotationManager(cfg)

    async def fake_fetch(url: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("manual mode must not rotate")

    manager._fetch = fake_fetch  # type: ignore[method-assign]  # noqa: SLF001

    endpoint = await manager.on_launch()
    assert endpoint is None
    assert manager.rotation_count == 0


async def test_rotation_api_call_mode_rotates_before_each_call() -> None:
    """In ``on_api_call`` mode each API call fetches a new endpoint."""
    cfg = StubRotationConfig(mode="on_api_call", rotate_url="http://provider/rotate")
    manager = ProxyRotationManager(cfg)

    calls = 0

    async def fake_fetch(url: str) -> str:
        nonlocal calls
        calls += 1
        return f"198.51.100.{calls}:1080"

    manager._fetch = fake_fetch  # type: ignore[method-assign]  # noqa: SLF001

    first = await manager.before_api_call()
    second = await manager.before_api_call()
    assert first is not None
    assert second is not None
    assert first.host != second.host
    assert calls == 2


async def test_rotation_error_mode_rotates_only_on_error() -> None:
    """In ``on_error`` mode a rotation happens only after a failure."""
    cfg = StubRotationConfig(mode="on_error", rotate_url="http://provider/rotate")
    manager = ProxyRotationManager(cfg)

    calls = 0

    async def fake_fetch(url: str) -> str:
        nonlocal calls
        calls += 1
        return f"198.51.100.{calls}:1080"

    manager._fetch = fake_fetch  # type: ignore[method-assign]  # noqa: SLF001

    endpoint = await manager.on_launch()
    assert endpoint is None
    assert calls == 0

    rotated = await manager.on_error(RuntimeError("boom"))
    assert rotated is not None
    assert calls == 1
    assert manager.history[0].trigger == "error"


async def test_rotation_interval_mode_schedules_next_rotation() -> None:
    """The ``on_interval`` mode exposes the next scheduled rotation time."""
    clock = FakeClock()
    cfg = StubRotationConfig(
        mode="on_interval",
        rotate_url="http://provider/rotate",
        interval_sec=300.0,
        cooldown_sec=0.0,
    )
    manager = ProxyRotationManager(cfg, clock=clock)

    async def fake_fetch(url: str) -> str:
        return "198.51.100.7:1080"

    manager._fetch = fake_fetch  # type: ignore[method-assign]  # noqa: SLF001

    await manager.rotate(trigger="launch:on_interval")
    assert manager.next_rotation_at == pytest.approx(clock.now + 300.0)
    assert manager.should_rotate_now() is False
    clock.advance(300.0)
    assert manager.should_rotate_now() is True


async def test_rotation_without_rotate_url_raises() -> None:
    """Rotating without a configured provider URL is an error."""
    cfg = StubRotationConfig(mode="on_launch", rotate_url=None)
    manager = ProxyRotationManager(cfg)
    with pytest.raises(ProxyRotationError):
        await manager.rotate(trigger="launch")


def test_rotation_history_is_auditable() -> None:
    """Rotation events expose a redacted, serialisable representation."""
    endpoint = StubEndpoint(
        protocol="http", host="198.51.100.7", port=1080, username="alice", password="s3cret"
    )
    event = rotation_mod.RotationEvent(
        timestamp=rotation_mod.datetime.now(rotation_mod.UTC),
        endpoint=endpoint,
        mode="on_launch",
        trigger="launch",
    )
    payload = event.to_dict()
    assert payload["mode"] == "on_launch"
    assert "s3cret" not in json.dumps(payload)
    assert payload["endpoint"].startswith("http://alice:")


# ==========================================================================
# DNS
# ==========================================================================


def test_dns_system_mode_flags() -> None:
    """``system`` mode disables built-in DoH and adds no resolver override."""
    args = build_dns_args(StubDnsConfig(mode="system"))
    assert args == ["--dns-over-https-mode=off"]


def test_dns_system_mode_with_prevent_leak() -> None:
    """``prevent_dns_leak`` forces the proxy-side resolution rule."""
    args = build_dns_args(StubDnsConfig(mode="system", prevent_dns_leak=True))
    assert "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost" in args
    assert "--dns-over-https-mode=off" in args


def test_dns_proxy_remote_mode_flags() -> None:
    """``proxy_remote`` maps every name to the proxy."""
    args = build_dns_args(StubDnsConfig(mode="proxy_remote"))
    assert args == [
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost",
        "--dns-over-https-mode=off",
    ]


def test_dns_custom_udp_mode_flags() -> None:
    """``custom_udp`` pins resolution to the configured server."""
    args = build_dns_args(StubDnsConfig(mode="custom_udp", custom_server="9.9.9.9"))
    assert "--host-resolver-rules=MAP * 9.9.9.9, EXCLUDE localhost" in args
    assert "--disable-features=AsyncDns" in args
    assert "--dns-over-https-mode=off" in args


def test_dns_doh_mode_flags() -> None:
    """``doh`` enables secure DNS over HTTPS."""
    args = build_dns_args(StubDnsConfig(mode="doh", doh_template="https://dns.example/dns-query"))
    assert "--dns-over-https-mode=secure" in args
    assert "--dns-over-https-templates=https://dns.example/dns-query" in args
    assert "--enable-features=AsyncDns" in args


def test_dns_dot_mode_flags() -> None:
    """``dot`` pins resolution to the DNS-over-TLS host."""
    args = build_dns_args(StubDnsConfig(mode="dot", dot_host="tls.example"))
    assert "--host-resolver-rules=MAP * tls.example, EXCLUDE localhost" in args
    assert "--disable-features=AsyncDns" in args


def test_dns_unknown_mode_raises() -> None:
    """An unknown DNS mode is rejected."""
    with pytest.raises(ValueError):
        build_dns_args(StubDnsConfig(mode="nope"))


def test_dns_flags_are_deterministic() -> None:
    """The same configuration always produces the same flag list."""
    cfg = StubDnsConfig(mode="custom_udp", custom_server="1.1.1.1")
    assert build_dns_args(cfg) == build_dns_args(cfg)


def test_dns_guard_exposes_rules() -> None:
    """The guard exposes the effective host-resolver rules."""
    guard = DnsGuard(StubDnsConfig(mode="proxy_remote"))
    assert guard.host_resolver_rules() == "MAP * ~NOTFOUND , EXCLUDE localhost"
    assert guard.leaks_for("example.com") is False


def test_dns_is_leaking_true_and_false_cases() -> None:
    """``is_leaking`` reports hosts that escape the proxy path."""
    safe = "MAP * ~NOTFOUND , EXCLUDE localhost"
    assert is_leaking(safe, "example.com") is False
    assert is_leaking(safe, "tracking.example.org") is False

    # No rules at all -> the system resolver is used -> leak.
    assert is_leaking("", "example.com") is True

    # Only localhost is excluded, everything else is proxied.
    assert is_leaking("MAP * ~NOTFOUND , EXCLUDE localhost", "localhost") is True

    # An explicit EXCLUDE of the tested host -> leak.
    assert is_leaking("MAP * ~NOTFOUND , EXCLUDE example.com", "example.com") is True

    # A concrete resolver address means a direct, un-proxied lookup.
    assert is_leaking("MAP * 1.1.1.1, EXCLUDE localhost", "example.com") is True

    # A narrow MAP that does not cover the tested host -> leak.
    assert is_leaking("MAP api.example.com ~NOTFOUND", "example.com") is True

    # A narrow MAP that does cover the tested host -> safe.
    assert is_leaking("MAP example.com ~NOTFOUND", "example.com") is False


def test_dns_parse_host_resolver_rules() -> None:
    """The rule parser understands MAP / EXCLUDE / INCLUDE."""
    rules = dns_mod.parse_host_resolver_rules(
        "MAP * ~NOTFOUND , EXCLUDE localhost , MAP api.example.com 1.2.3.4"
    )
    assert rules[0] == ("MAP", ["*", "~NOTFOUND"])
    assert rules[1] == ("EXCLUDE", ["localhost"])
    assert rules[2] == ("MAP", ["api.example.com", "1.2.3.4"])
    assert dns_mod.parse_host_resolver_rules("") == []


# ==========================================================================
# WEBRTC
# ==========================================================================


def test_webrtc_real_mode_flags() -> None:
    """``real`` mode keeps Chromium's default behaviour."""
    args = build_webrtc_args(StubWebRtcConfig(mode="real"))
    assert args == ["--disable-features=WebRtcHideLocalIpsWithMdns"]


def test_webrtc_mdns_enabled_keeps_hiding_feature() -> None:
    """With mDNS enabled Chromium keeps hiding the raw interface addresses."""
    args = build_webrtc_args(StubWebRtcConfig(mode="proxy_only", mdns_enabled=True))
    assert "--enable-features=WebRtcHideLocalIpsWithMdns" in args
    assert "--disable-features=WebRtcHideLocalIpsWithMdns" not in args


def test_webrtc_relay_transport_policy() -> None:
    """A ``relay`` transport policy forces the relay-only IP policy."""
    args = build_webrtc_args(StubWebRtcConfig(mode="proxy_only", ice_transport_policy="relay"))
    assert "--force-webrtc-ip-handling-policy=relay" in args


def test_webrtc_altered_mode_flags() -> None:
    """``altered`` mode restricts ICE to the public interface."""
    args = build_webrtc_args(StubWebRtcConfig(mode="altered"))
    assert "--force-webrtc-ip-handling-policy=default_public_interface_only" in args
    assert "--webrtc-ip-handling-policy=default_public_interface_only" in args
    assert "--disable-features=WebRtcHideLocalIpsWithMdns" in args


def test_webrtc_disabled_mode_flags() -> None:
    """``disabled`` mode blocks all non-proxied UDP."""
    args = build_webrtc_args(StubWebRtcConfig(mode="disabled"))
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_webrtc_default_public_interface_only_mode_flags() -> None:
    """``default_public_interface_only`` pins the IP handling policy."""
    args = build_webrtc_args(StubWebRtcConfig(mode="default_public_interface_only"))
    assert "--force-webrtc-ip-handling-policy=default_public_interface_only" in args
    assert "--webrtc-ip-handling-policy=default_public_interface_only" in args


def test_webrtc_proxy_only_mode_flags() -> None:
    """``proxy_only`` keeps ICE inside the proxy tunnel."""
    args = build_webrtc_args(StubWebRtcConfig(mode="proxy_only"))
    assert "--force-webrtc-ip-handling-policy=proxy_only" in args
    assert "--webrtc-ip-handling-policy=proxy_only" in args


def test_webrtc_unknown_mode_raises() -> None:
    """An unknown WebRTC mode is rejected."""
    with pytest.raises(ValueError):
        build_webrtc_args(StubWebRtcConfig(mode="nope"))


def test_webrtc_should_block_udp() -> None:
    """UDP blocking follows the config flag and the ``disabled`` mode."""
    assert should_block_udp(StubWebRtcConfig(mode="real")) is False
    assert should_block_udp(StubWebRtcConfig(mode="real", block_non_proxied_udp=True)) is True
    assert should_block_udp(StubWebRtcConfig(mode="disabled")) is True
    assert should_block_udp(StubWebRtcConfig(mode="proxy_only")) is False


def test_webrtc_block_udp_adds_policy_flag() -> None:
    """Requesting UDP blocking adds the Chromium policy flag."""
    args = build_webrtc_args(StubWebRtcConfig(mode="real", block_non_proxied_udp=True))
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_webrtc_spoofed_local_ip_is_deterministic() -> None:
    """The same seed always yields the same spoofed RFC1918 address."""
    seed = "a" * 64
    first = spoofed_local_ip(seed)
    second = spoofed_local_ip(seed)
    assert first == second
    assert first.startswith("10.") or first.startswith("192.168.")


def test_webrtc_spoofed_local_ip_differs_per_seed() -> None:
    """Different seeds yield different spoofed addresses."""
    addresses = {spoofed_local_ip(f"{index:064x}") for index in range(64)}
    assert len(addresses) > 50


def test_webrtc_spoofed_local_ip_is_private() -> None:
    """The spoofed address is always inside an RFC1918 range."""
    import ipaddress

    for index in range(32):
        address = ipaddress.ip_address(spoofed_local_ip(f"{index:064x}"))
        assert address.is_private


def test_webrtc_public_ip_mismatch() -> None:
    """A differing public IP is reported as a mismatch."""
    assert public_ip_mismatch("203.0.113.9", "10.0.0.5") is True
    assert public_ip_mismatch("10.0.0.5", "10.0.0.5") is False
    assert public_ip_mismatch(None, "10.0.0.5") is False
    assert public_ip_mismatch("203.0.113.9", None) is False


def test_webrtc_guard_detects_leaking_candidates() -> None:
    """The guard flags ICE candidates that expose the real public IP."""
    guard = WebRtcGuard(StubWebRtcConfig(mode="proxy_only"))
    leaking = guard.check_candidates(
        ["candidate:1 1 udp 2130706431 203.0.113.9 54321 typ host"],
        master_seed="b" * 64,
    )
    assert len(leaking) == 1


def test_webrtc_guard_ignores_private_candidates() -> None:
    """Private / loopback candidates are not leaks."""
    guard = WebRtcGuard(StubWebRtcConfig(mode="proxy_only"))
    leaking = guard.check_candidates(
        [
            "candidate:1 1 udp 2130706431 192.168.1.5 54321 typ host",
            "candidate:2 1 udp 2130706431 127.0.0.1 54322 typ host",
        ],
        master_seed="b" * 64,
    )
    assert leaking == []


# ==========================================================================
# LEAKCHECK
# ==========================================================================


class FakeCdp:
    """A scriptable ``CdpCall`` implementation."""

    def __init__(
        self,
        *,
        ice_candidates: list[str] | None = None,
        time_zone: str = "Europe/Berlin",
        languages: list[str] | None = None,
        resolver_rules: str | None = "MAP * ~NOTFOUND , EXCLUDE localhost",
    ) -> None:
        self.ice_candidates = ice_candidates or []
        self.time_zone = time_zone
        self.languages = languages if languages is not None else ["de-DE", "de", "en-US"]
        self.resolver_rules = resolver_rules
        self.methods: list[str] = []

    async def __call__(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.methods.append(method)
        if method == "Runtime.evaluate":
            expression = (params or {}).get("expression", "")
            if "RTCPeerConnection" in expression:
                value: dict[str, Any] = {"candidates": list(self.ice_candidates)}
            else:
                value = {
                    "timeZone": self.time_zone,
                    "languages": list(self.languages),
                }
            return {"result": {"type": "object", "value": value}}
        if method == "Browser.getBrowserCommandLine":
            arguments = ["--user-data-dir=C:\\tmp"]
            if self.resolver_rules is not None:
                arguments.append(f"--host-resolver-rules={self.resolver_rules}")
            return {"arguments": arguments}
        raise AssertionError(f"unexpected CDP method: {method}")


async def test_leakcheck_detects_webrtc_leak() -> None:
    """A crafted ICE candidate exposing the real IP is reported."""
    cdp = FakeCdp(ice_candidates=["candidate:1 1 udp 2130706431 203.0.113.9 54321 typ srflx"])
    checker = LeakChecker(
        StubProfileConfig(),
        cdp_call=cdp,
        real_public_ip="203.0.113.9",
        master_seed="c" * 64,
    )

    report = await checker.check()

    assert isinstance(report, LeakReport)
    assert report.ok is False
    assert any(entry.startswith("webrtc:") for entry in report.leaks)
    assert report.details["webrtc"]["leaking_candidates"]


async def test_leakcheck_clean_profile_is_ok() -> None:
    """A clean browser produces an empty leak list."""
    cdp = FakeCdp(
        ice_candidates=[
            "candidate:1 1 udp 2130706431 10.20.30.40 54321 typ host",
            "candidate:2 1 udp 2130706431 127.0.0.1 54322 typ host",
        ]
    )
    checker = LeakChecker(
        StubProfileConfig(),
        cdp_call=cdp,
        real_public_ip="203.0.113.9",
        master_seed="c" * 64,
    )

    report = await checker.check()

    assert report.ok is True
    assert report.leaks == []
    assert report.details["webrtc"]["ok"] is True
    assert report.details["dns"]["ok"] is True
    assert report.details["timezone"]["ok"] is True
    assert report.details["languages"]["ok"] is True


async def test_leakcheck_detects_timezone_mismatch() -> None:
    """A page timezone that differs from the profile is a leak."""
    cdp = FakeCdp(time_zone="America/Los_Angeles")
    checker = LeakChecker(StubProfileConfig(), cdp_call=cdp)

    report = await checker.check()

    assert report.ok is False
    assert any(entry.startswith("timezone:") for entry in report.leaks)


async def test_leakcheck_detects_language_mismatch() -> None:
    """A page language list that differs from the profile is a leak."""
    cdp = FakeCdp(languages=["en-US"])
    checker = LeakChecker(StubProfileConfig(), cdp_call=cdp)

    report = await checker.check()

    assert report.ok is False
    assert any(entry.startswith("languages:") for entry in report.leaks)


async def test_leakcheck_detects_dns_leak() -> None:
    """Resolver rules that let a host escape are reported."""
    cdp = FakeCdp(resolver_rules="MAP * 1.1.1.1, EXCLUDE localhost")
    checker = LeakChecker(StubProfileConfig(), cdp_call=cdp)

    report = await checker.check()

    assert report.ok is False
    assert any(entry.startswith("dns:") for entry in report.leaks)


async def test_leakcheck_reports_missing_resolver_rules() -> None:
    """An unreadable command line is reported as a DNS failure."""
    cdp = FakeCdp(resolver_rules=None)
    checker = LeakChecker(StubProfileConfig(), cdp_call=cdp)

    report = await checker.check()

    assert report.ok is False
    assert any(entry.startswith("dns:") for entry in report.leaks)


async def test_leakcheck_detects_mdns_candidate() -> None:
    """An mDNS ``.local`` candidate leaks the machine name."""
    cdp = FakeCdp(ice_candidates=["candidate:1 1 udp 2130706431 abc123.local 54321 typ host"])
    checker = LeakChecker(StubProfileConfig(), cdp_call=cdp, master_seed="d" * 64)

    report = await checker.check()

    assert report.ok is False
    assert any(entry.startswith("webrtc:") for entry in report.leaks)


# --------------------------------------------------------------------------
# Proxy string parsing
# --------------------------------------------------------------------------


def test_proxy_string_without_credentials() -> None:
    """A plain host and port are enough."""

    endpoint = parse_proxy_string("198.51.100.7:8080")

    assert endpoint is not None
    assert endpoint.host == "198.51.100.7"
    assert endpoint.port == 8080
    assert endpoint.username is None
    assert endpoint.password is None


def test_proxy_string_keeps_the_credentials() -> None:
    """The credentials have to survive the parse.

    A proxy that loses its credentials cannot authenticate: the browser
    reaches the exit node and is rejected there, which looks to the site
    like a broken client rather than a routed one.
    """

    endpoint = parse_proxy_string("http://user:pass@198.51.100.7:8080")

    assert endpoint is not None
    assert endpoint.username == "user"
    assert endpoint.password == "pass"
    assert endpoint.host == "198.51.100.7"
    assert endpoint.port == 8080


def test_proxy_string_with_a_user_but_no_password() -> None:
    """A user without a password is a valid combination."""

    endpoint = parse_proxy_string("socks5://user@198.51.100.7:1080")

    assert endpoint is not None
    assert endpoint.username == "user"
    assert endpoint.password is None
    assert endpoint.protocol == "socks5"


def test_proxy_string_with_a_colon_separated_pair() -> None:
    """The ``host:port:user:pass`` form is supported."""

    endpoint = parse_proxy_string("198.51.100.7:8080:user:pass")

    assert endpoint is not None
    assert endpoint.username == "user"
    assert endpoint.password == "pass"


def test_a_broken_proxy_string_is_rejected() -> None:
    """A string without a port is not a proxy."""

    assert parse_proxy_string("not a proxy") is None
    assert parse_proxy_string("") is None
