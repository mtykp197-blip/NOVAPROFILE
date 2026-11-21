"""Tests for the network policy that follows a profile's proxy.

The proxy is attached to a profile *after* its device identity has been
generated -- that is how the API works.  The WebRTC configuration has to
follow it, in both directions:

* a profile with a proxy must not expose the host's address through the
  ICE candidates;
* a profile without a proxy must look like an ordinary handset, whose
  candidates are reachable -- a missing ``RTCPeerConnection`` is a far
  louder signal than any address leak.
"""

from __future__ import annotations

import pytest

from novaprofile.api import routes_profiles
from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.generator import ProfileGenerator


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def test_a_proxy_override_is_parsed_into_the_profile(gen):
    """A proxy URL becomes a structured proxy on the profile."""

    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    updated = routes_profiles._apply_overrides(
        config, {"proxy": {"url": "http://user:pass@198.51.100.7:8080"}}
    )

    assert updated.proxy.enabled is True
    assert updated.proxy.host == "198.51.100.7"
    assert updated.proxy.port == 8080
    assert updated.proxy.username == "user"
    assert updated.proxy.auth_method == "userpass"


def test_a_proxy_override_locks_the_webrtc_policy(gen):
    """The ICE policy follows the proxy that was attached afterwards."""

    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    updated = routes_profiles._apply_overrides(
        config, {"proxy": {"url": "socks5://198.51.100.7:1080"}}
    )

    assert updated.webrtc.mode == "proxy_only"
    assert updated.webrtc.ice_transport_policy == "relay"
    assert updated.webrtc.block_non_proxied_udp is True
    assert updated.webrtc.mdns_enabled is True
    assert ConsistencyResolver().check(updated).errors == []


def test_a_profile_without_a_proxy_keeps_its_candidates(gen):
    """An unproxied profile must behave like an ordinary handset."""

    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    updated = routes_profiles._apply_overrides(config, {})

    assert updated.webrtc.mode == "direct"
    assert updated.webrtc.ice_transport_policy == "all"
    assert updated.webrtc.block_non_proxied_udp is False
    assert updated.webrtc.mdns_enabled is True
    assert ConsistencyResolver().check(updated).errors == []


def test_the_proxy_string_is_accepted_as_well(gen):
    """The override may also be a bare URL."""

    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    updated = routes_profiles._apply_overrides(config, {"proxy": "http://198.51.100.7:8080"})

    assert updated.proxy.enabled is True
    assert updated.proxy.host == "198.51.100.7"


def test_an_unparseable_proxy_leaves_the_profile_alone(gen):
    """A broken proxy string must not corrupt the profile."""

    config = gen.generate("pixel-8-pro-android14", master_seed="a" * 64)
    updated = routes_profiles._apply_overrides(config, {"proxy": {"url": "not a proxy"}})

    assert updated.proxy.enabled is False
