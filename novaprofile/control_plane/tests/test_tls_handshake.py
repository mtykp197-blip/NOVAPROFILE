"""Tests for the TLS handshake surface.

The ClientHello is the one fingerprint that is produced before any
script runs, in the network service, in C++.  A JavaScript-level
defence cannot reach it at all, so the profile has to carry the shape
of the handshake and the native core has to install it on the
BoringSSL context.

Two things have to hold.

*Determinism* -- the handshake of one profile is the same on every
run, and the handshake of two profiles is never the same.  The salt is
what carries the second half of that: the cipher list is a property of
the Chrome release, not of the handset, so without a per-unit salt every
profile of a release would send an identical ClientHello and a site that
collects JA3 hashes would link the whole fleet at once.

*Coherence* -- the handshake has to belong to the Chrome release the
profile claims to be.  The hybrid key agreement is the cheapest check
there is: it appears in the ClientHello of Chrome 131 and later and in
no earlier release, so a profile that claims 131 in its user agent and
sends the handshake of 120 disagrees with itself in the two places
that are cheapest to cross-check.
"""

from __future__ import annotations

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.seed import Axis, SeedDeriver, new_master_seed
from novaprofile.core.tls_profiles import MAJOR_PROFILES, profile_for_major
from novaprofile.schema.network import Tls
from novaprofile.schema.version import BrowserVersion


# ----------------------------------------------------------------------
# The table of the releases
# ----------------------------------------------------------------------


def test_the_table_covers_every_release_from_100():
    """Every Chrome release the fleet claims to be has to be in the table."""

    assert min(MAJOR_PROFILES) == 100
    assert max(MAJOR_PROFILES) >= 180
    for major in range(100, max(MAJOR_PROFILES) + 1):
        assert major in MAJOR_PROFILES, f"Chrome {major} has no ClientHello"


def test_a_version_outside_the_table_inherits_from_the_nearest_one():
    """An unknown release still gets a coherent handshake.

    A profile that claims a release the table does not know about must
    not fall back to the stock Chromium handshake: that handshake is the
    one of the build, not of the release the profile claims, and the two
    are what a site compares.
    """

    old = profile_for_major(50)
    new = profile_for_major(999)
    assert old.cipher_suites
    assert new.cipher_suites
    assert old.cipher_suites != new.cipher_suites


def test_the_release_before_131_has_no_hybrid_key_agreement():
    """The hybrid curve is the cross-check, so it has to be exact."""

    for major in range(100, 131):
        groups = profile_for_major(major).supported_groups
        assert not any("Kyber" in group for group in groups), (
            f"Chrome {major} did not offer the hybrid key agreement"
        )


def test_the_release_from_131_has_the_hybrid_key_agreement():
    """Chrome 131 turned it on; every later release keeps it."""

    for major in range(131, max(MAJOR_PROFILES) + 1):
        groups = profile_for_major(major).supported_groups
        assert any("Kyber" in group for group in groups), (
            f"Chrome {major} did offer the hybrid key agreement"
        )


def test_the_release_before_107_keeps_the_cbc_suites():
    """Chrome 107 removed them; a profile that claims 100 has to keep them."""

    for major in range(100, 107):
        suites = profile_for_major(major).cipher_suites
        assert any("AES128-SHA" in suite for suite in suites), (
            f"Chrome {major} still offered the CBC suites"
        )


def test_the_release_from_107_has_no_cbc_suites():
    """The CBC suites are gone from 107 on."""

    for major in range(107, max(MAJOR_PROFILES) + 1):
        suites = profile_for_major(major).cipher_suites
        assert not any("AES128-SHA" in suite for suite in suites), (
            f"Chrome {major} did not offer the CBC suites"
        )


def test_every_release_offers_the_tls_13_suites():
    """Chrome has offered TLS 1.3 since 81."""

    for major, profile in MAJOR_PROFILES.items():
        assert any(
            suite.startswith("TLS_AES_") or suite.startswith("TLS_CHACHA20")
            for suite in profile.cipher_suites
        ), f"Chrome {major} offers no TLS 1.3 suite"


def test_no_release_repeats_a_cipher_suite():
    """A repeated entry would make the list ambiguous to BoringSSL."""

    for major, profile in MAJOR_PROFILES.items():
        assert len(profile.cipher_suites) == len(set(profile.cipher_suites)), (
            f"Chrome {major} repeats a cipher suite"
        )
        assert len(profile.supported_groups) == len(set(profile.supported_groups)), (
            f"Chrome {major} repeats a curve"
        )


# ----------------------------------------------------------------------
# The schema
# ----------------------------------------------------------------------


def test_the_schema_rejects_a_repeated_entry():
    """The list is handed to BoringSSL as it is."""

    with pytest.raises(ValueError):
        Tls(
            salt="0123456789abcdef",
            cipher_suites=["TLS_AES_128_GCM_SHA256", "TLS_AES_128_GCM_SHA256"],
        )


def test_the_schema_rejects_a_salt_that_is_too_short():
    """The salt is the key of the noise; a short one is a weak one."""

    with pytest.raises(ValueError):
        Tls(salt="short")


# ----------------------------------------------------------------------
# The generator
# ----------------------------------------------------------------------


#: A preset of the Android 14 band, which is the one the tests use.
PRESET = "pixel-8-pro-android14"


def _generate(major: int, seed: str = "1" * 64):
    generator = ProfileGenerator()
    return generator.generate(
        PRESET,
        master_seed=seed,
        browser_version=BrowserVersion(major=major),
    )


def test_the_profile_carries_a_spoofed_handshake():
    """A generated profile must not fall back to the stock handshake."""

    config = _generate(120)
    assert config.tls.mode == "spoof"
    assert config.tls.cipher_suites
    assert config.tls.supported_groups
    assert config.tls.signature_algorithms
    assert config.tls.alpn_protocols
    assert config.tls.versions
    assert config.tls.extension_order
    assert config.tls.salt


def test_the_handshake_matches_the_claimed_release():
    """The handshake has to belong to the release the profile claims."""

    for major in (100, 110, 120, 131, 140):
        config = _generate(major)
        expected = profile_for_major(major)
        assert list(config.tls.cipher_suites) == list(expected.cipher_suites)
        assert list(config.tls.supported_groups) == list(expected.supported_groups)


def test_two_profiles_of_one_release_do_not_share_the_handshake():
    """The salt is what separates two units of the same release."""

    generator = ProfileGenerator()
    first = generator.generate(
        PRESET, master_seed="1" * 64, browser_version=BrowserVersion(major=125)
    )
    second = generator.generate(
        PRESET, master_seed="2" * 64, browser_version=BrowserVersion(major=125)
    )

    assert first.tls.salt != second.tls.salt
    # The cipher list is the same -- it belongs to the release -- so the
    # salt is the only thing that differs, and it has to differ.
    assert first.tls.cipher_suites == second.tls.cipher_suites


def test_the_same_profile_produces_the_same_handshake():
    """The handshake of one profile is stable across the runs."""

    seed = new_master_seed()
    deriver_a = SeedDeriver(seed)
    deriver_b = SeedDeriver(seed)
    assert (
        deriver_a.derive_bytes(Axis.TLS_HANDSHAKE, 16, context="salt").hex()
        == deriver_b.derive_bytes(Axis.TLS_HANDSHAKE, 16, context="salt").hex()
    )


def test_the_tls_axis_has_its_own_domain_salt():
    """Reusing another axis's salt would couple the two surfaces.

    The TLS handshake is read by a site that never touches the canvas,
    so the two must not be derivable from one another.
    """

    seed = new_master_seed()
    tls = SeedDeriver(seed).derive_bytes(Axis.TLS_HANDSHAKE, 16, context="salt")
    canvas = SeedDeriver(seed).derive_bytes(Axis.CANVAS_2D, 16, context="salt")
    assert tls != canvas


def test_the_handshake_is_part_of_the_collision_signature():
    """The collision guard has to watch the TLS axis.

    Without it, a fleet could hold two profiles whose handshake is
    identical -- which is exactly the pair a JA3 collector would link.
    """

    config = _generate(120)
    parts = config.signature_parts()
    assert "sig_tls" in parts
    assert config.tls.salt in parts["sig_tls"]
    assert ",".join(config.tls.cipher_suites) in parts["sig_tls"]
