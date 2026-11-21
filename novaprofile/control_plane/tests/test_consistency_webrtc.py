"""Tests for the WebRTC leak checks.

WebRTC is the most reliable way for a page to learn the address of the
machine it runs on.  A profile that uses a proxy but leaves WebRTC open
hands the site's server the very address the operator paid to hide, so the
combination has to be refused.
"""

from __future__ import annotations

import pytest

from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.generator import ProfileGenerator
from novaprofile.schema.network import Proxy


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _proxied(gen: ProfileGenerator, **webrtc_overrides):
    """A profile with a proxy attached *after* it was generated.

    This is the shape the API produces: the device identity is generated
    first and the proxy is attached afterwards, so the WebRTC policy has
    to follow the proxy rather than the other way round.
    """
    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    config = config.model_copy(
        update={
            "proxy": Proxy(
                enabled=True,
                protocol="http",
                host="1.2.3.4",
                port=8080,
            )
        }
    )
    # The proxy is attached after the fact, so the WebRTC policy has to be
    # re-derived from it -- exactly what the resolver does.
    config = ConsistencyResolver().resolve(config)
    if webrtc_overrides:
        config = config.model_copy(
            update={"webrtc": config.webrtc.model_copy(update=webrtc_overrides)}
        )
    return config


def test_a_default_proxied_profile_passes(gen):
    """The generated defaults are safe."""

    report = ConsistencyResolver().check(_proxied(gen))
    assert report.errors == []


def test_the_resolver_locks_webrtc_down_when_a_proxy_is_attached(gen):
    """Attaching a proxy after the fact must not leave WebRTC open."""

    resolver = ConsistencyResolver()
    raw = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    raw = raw.model_copy(
        update={
            "proxy": Proxy(
                enabled=True,
                protocol="http",
                host="1.2.3.4",
                port=8080,
            )
        }
    )

    config = resolver.resolve(raw)

    assert config.webrtc.mode == "proxy_only"
    assert config.webrtc.ice_transport_policy == "relay"
    assert config.webrtc.block_non_proxied_udp is True
    assert config.webrtc.mdns_enabled is True
    assert resolver.check(config).errors == []


def test_the_resolver_opens_webrtc_up_when_the_proxy_is_removed(gen):
    """A profile without a proxy must look like an ordinary handset."""

    resolver = ConsistencyResolver()
    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    config = config.model_copy(
        update={
            "webrtc": config.webrtc.model_copy(
                update={
                    "mode": "proxy_only",
                    "ice_transport_policy": "relay",
                    "block_non_proxied_udp": True,
                }
            )
        }
    )

    resolved = resolver.resolve(config)
    assert resolved.webrtc.mode == "direct"
    assert resolved.webrtc.ice_transport_policy == "all"
    assert resolved.webrtc.block_non_proxied_udp is False
    # The mDNS obfuscation stays on either way: it is what hides the
    # local interface address from the candidates.
    assert resolved.webrtc.mdns_enabled is True
    assert resolver.check(resolved).errors == []


def test_an_open_ice_policy_is_an_error(gen):
    """The 'all' policy lets the page see the host's address."""

    report = ConsistencyResolver().check(_proxied(gen, ice_transport_policy="all"))
    assert not report.ok
    assert any(issue.axis == "webrtc" for issue in report.errors)


def test_unblocked_udp_is_an_error(gen):
    """A WebRTC handshake can leave the proxy."""

    report = ConsistencyResolver().check(_proxied(gen, block_non_proxied_udp=False))
    assert not report.ok
    assert any(issue.axis == "webrtc" for issue in report.errors)


def test_disabled_mdns_is_an_error(gen):
    """Without the mDNS obfuscation the local interfaces are disclosed."""

    report = ConsistencyResolver().check(_proxied(gen, mdns_enabled=False))
    assert not report.ok
    assert any(issue.axis == "webrtc" for issue in report.errors)


def test_a_direct_profile_is_not_penalised(gen):
    """Without a proxy there is nothing to hide from."""

    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    config = config.model_copy(
        update={"webrtc": config.webrtc.model_copy(update={"ice_transport_policy": "all"})}
    )

    report = ConsistencyResolver().check(config)
    assert report.errors == []


def test_a_spoofed_address_that_contradicts_the_exit_is_a_warning(gen):
    """A page that compares the two addresses sees the mismatch."""

    config = _proxied(gen, spoofed_public_ip_v4="9.9.9.9")
    config = config.model_copy(
        update={
            "geo_intelligence": config.geo_intelligence.model_copy(update={"exit_ip_v4": "1.2.3.4"})
        }
    )

    report = ConsistencyResolver().check(config)
    assert report.ok
    assert any(issue.axis == "webrtc" for issue in report.warnings)


def test_a_private_spoofed_address_is_an_error(gen):
    """No internet-facing site can see a private address."""

    config = _proxied(gen, spoofed_public_ip_v4="192.168.1.5")

    report = ConsistencyResolver().check(config)
    assert not report.ok
    assert any(issue.axis == "webrtc" for issue in report.errors)
