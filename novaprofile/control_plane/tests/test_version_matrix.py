"""Milestone tests for the browser-version matrix (100 -> latest).

Verifies, for the milestone majors, that generated profiles are internally
consistent (UA / brands / full-version-list / client hints), that the
``Build/`` token follows the UA freeze rule, and that generation is
deterministic (persistence) and collision-free across a sample.
"""

from __future__ import annotations

import json

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.versions import DEFAULT_REGISTRY
from novaprofile.schema.version import BrowserVersion

MILESTONES = (100, 112, 124, 136, 155)
LATEST = DEFAULT_REGISTRY.max_major
PRESET = "pixel-8-pro-android14"
SEED = "e" * 64


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _gen(gen: ProfileGenerator, major: int, seed: str = SEED):
    return gen.generate(PRESET, master_seed=seed, browser_version=BrowserVersion(major=major))


@pytest.mark.parametrize("major", MILESTONES + (LATEST,))
def test_ua_matches_declared_major(gen, major):
    p = _gen(gen, major)
    assert f"Chrome/{major}.0.0.0" in p.navigator.user_agent
    assert p.browser_version.major == major


@pytest.mark.parametrize("major", MILESTONES + (LATEST,))
def test_brands_and_full_list_consistent(gen, major):
    p = _gen(gen, major)
    brands = {b.brand: b.version for b in p.user_agent_data.brands}
    full = {b.brand: b.version for b in p.user_agent_data.full_version_list}
    assert brands["Chromium"] == str(major)
    assert brands["Google Chrome"] == str(major)

    # The full version list is a high-entropy hint: Chromium only sends it
    # from major 116 on.  A core that cannot produce it must not carry
    # one, and the data has to be empty as well as the header -- the data
    # is what the JS runtime hands to ``getHighEntropyValues`` and what
    # the consistency resolver renders the header from.
    caps = DEFAULT_REGISTRY.capabilities(major)
    if caps.has_client_hint("Sec-CH-UA-Full-Version-List"):
        assert full["Chromium"] == p.browser_version.full_version
        assert full["Chromium"].split(".")[0] == str(major)
        assert set(brands) == set(full)
    else:
        assert full == {}


@pytest.mark.parametrize("major", MILESTONES + (LATEST,))
def test_sec_ch_ua_header_matches_uad(gen, major):
    p = _gen(gen, major)
    assert f'"Chromium";v="{major}"' in p.http_headers.sec_ch_ua


@pytest.mark.parametrize("major", MILESTONES + (LATEST,))
def test_the_full_version_list_appears_only_when_the_core_sends_it(gen, major):
    """Chromium only started sending ``Sec-CH-UA-Full-Version-List`` in 116.

    A profile that sends it on an older core claims a capability the
    running browser does not have, which is a detectable inconsistency.
    """

    p = _gen(gen, major)
    caps = DEFAULT_REGISTRY.capabilities(major)

    if caps.has_client_hint("Sec-CH-UA-Full-Version-List"):
        assert p.browser_version.full_version in p.http_headers.sec_ch_ua_full_version_list
    else:
        assert p.http_headers.sec_ch_ua_full_version_list == ""


@pytest.mark.parametrize("major", MILESTONES + (LATEST,))
def test_the_high_entropy_hints_follow_their_introduction(gen, major):
    """The architecture, bitness and model arrived in Chromium 119.

    Before that they are absent from the wire entirely -- not present but
    empty -- and a profile that carries them early is inconsistent with its
    declared core.
    """

    p = _gen(gen, major)
    caps = DEFAULT_REGISTRY.capabilities(major)
    headers = p.http_headers

    for hint, value in (
        ("Sec-CH-UA-Arch", headers.sec_ch_ua_arch),
        ("Sec-CH-UA-Bitness", headers.sec_ch_ua_bitness),
        ("Sec-CH-UA-Model", headers.sec_ch_ua_model),
    ):
        if caps.has_client_hint(hint):
            assert value, f"{hint} must carry a value on Chromium {major}"
        else:
            assert value == "", f"{hint} must be absent on Chromium {major}"


def test_the_declared_architecture_matches_the_device_abi(gen):
    """An ARM device reports ``arm``; the bitness follows the ABI."""

    p = _gen(gen, 125)
    assert p.http_headers.sec_ch_ua_arch == "arm"
    assert p.http_headers.sec_ch_ua_bitness == ("64" if p.android_abi_soc.is_64_bit else "32")
    assert p.user_agent_data.architecture == p.http_headers.sec_ch_ua_arch
    assert p.user_agent_data.bitness == p.http_headers.sec_ch_ua_bitness


def test_build_token_freeze_rule(gen):
    assert "Build/" in _gen(gen, 100).navigator.user_agent
    assert "Build/" not in _gen(gen, 112).navigator.user_agent


@pytest.mark.parametrize("major", MILESTONES)
def test_generation_is_deterministic(gen, major):
    """Same seed -> same fingerprint.

    Excluded as bookkeeping, not fingerprint: ``identity`` (uuid and creation
    timestamps) and ``geo_intelligence.checked_at``, which records when the
    geo lookup ran.  Everything else must be byte-stable.
    """
    excl = {"identity": True, "geo_intelligence": {"checked_at": True}}
    a = _gen(gen, major).model_dump(mode="json", exclude=excl)
    b = _gen(gen, major).model_dump(mode="json", exclude=excl)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_zero_collisions_across_seeds(gen):
    """Distinct seeds must yield distinct profile signatures.

    UA and client hints are fixed per major by design (they are the
    browser's own build identity), so uniqueness is measured on the full
    seed-derived signature, not on UA alone.
    """
    total = 60
    keys = {
        json.dumps(_gen(gen, 124, seed=f"{i:064x}").signature_parts(), sort_keys=True)
        for i in range(total)
    }
    assert len(keys) == total
