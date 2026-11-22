"""The TLS ClientHello of every Chrome release the fleet covers.

JA3 is the MD5 of the ClientHello.  It is built from the cipher suites,
the extension order, the elliptic curves and the point formats -- in the
order the client sent them.  JA4 is the readable form of the same
handshake.  Two profiles that offer the same suites in the same order
have the same JA3, and a site that collects JA3 hashes links them.

The handshake is produced by the network service, in C++, before any
script runs.  No JavaScript can change it.  The only way to change it is
to change the bytes the client puts on the wire, which is what the native
core does: it installs the profile's cipher list, curve list, signature
algorithm list and ALPN list on the BoringSSL context before the
handshake.

The tables below are the ClientHello that the *real* Chrome of that
release produces on Android.  A profile therefore reports the handshake of
the Chrome version it claims to be, which is what makes the TLS surface
agree with the user agent and with the client hints.

The names are the ones BoringSSL uses, because that is what the native
core hands to ``SSL_CTX_set_custom_cipher_list`` and to
``SSL_set1_group_ids``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "TlsProfile",
    "profile_for_major",
    "MAJOR_PROFILES",
]


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------

# The ciphers that Chrome 100 through 106 offered, in the order BoringSSL
# emits them.  Chrome 100 is the oldest release the fleet covers; before
# that the list still contained the CBC suites, and Chrome removed them
# in 107.
_CHROME_100_CIPHERS: tuple[str, ...] = (
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-RSA-AES128-SHA",
    "ECDHE-RSA-AES256-SHA",
    "AES128-GCM-SHA256",
    "AES256-GCM-SHA384",
    "AES128-SHA",
    "AES256-SHA",
)

# Chrome 107 removed the CBC suites.  The list is the same until Chrome
# 109, which added the ML-KEM key agreement.
_CHROME_107_CIPHERS: tuple[str, ...] = (
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
)

# Chrome 116 was the release that enabled TLS 1.3 by default on Android
# and that reordered the list.
_CHROME_116_CIPHERS: tuple[str, ...] = (
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
)

# Chrome 131 was the release that enabled the X25519Kyber768Draft00 key
# agreement, which is a hybrid of X25519 and the post-quantum Kyber.
_CHROME_131_CIPHERS: tuple[str, ...] = (
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
)

# The curves.  Chrome offered X25519 first from the start of the range;
# the hybrid curve was added in 131 and the secp256r1 was moved behind
# it.
_GROUPS_MODERN: tuple[str, ...] = ("X25519", "P-256", "P-384")
_GROUPS_P256_FIRST: tuple[str, ...] = ("P-256", "X25519", "P-384")
_GROUPS_HYBRID: tuple[str, ...] = ("X25519Kyber768Draft00", "X25519", "P-256", "P-384")

# The signature algorithms.  The list is what the site sees in the
# ClientHello; it is sent in the order it is written.
_SIG_ECDSA_FIRST: tuple[str, ...] = (
    "ECDSA_SECP256R1_SHA256",
    "RSA_PSS_RSAE_SHA256",
    "RSA_PKCS1_SHA256",
    "ECDSA_SECP384R1_SHA384",
    "RSA_PSS_RSAE_SHA384",
    "RSA_PKCS1_SHA384",
    "RSA_PSS_RSAE_SHA512",
    "RSA_PKCS1_SHA512",
)
_SIG_RSA_FIRST: tuple[str, ...] = (
    "RSA_PSS_RSAE_SHA256",
    "ECDSA_SECP256R1_SHA256",
    "RSA_PKCS1_SHA256",
    "RSA_PSS_RSAE_SHA384",
    "ECDSA_SECP384R1_SHA384",
    "RSA_PKCS1_SHA384",
    "RSA_PSS_RSAE_SHA512",
    "RSA_PKCS1_SHA512",
)

# The ALPN list.  Chrome always offers h2 and http/1.1, and the order
# tells the server what the client would rather speak.
_ALPN_HTTP2: tuple[str, ...] = ("h2", "http/1.1")
_ALPN_HTTP1: tuple[str, ...] = ("http/1.1",)

# The extension order.  The name is the one BoringSSL uses; an extension
# that is not listed keeps the position BoringSSL would have given it.
_EXT_MODERN: tuple[str, ...] = (
    "server_name",
    "renegotiation_info",
    "supported_groups",
    "ec_point_formats",
    "session_ticket",
    "application_layer_protocol_negotiation",
    "signed_certificate_timestamp",
    "key_share",
    "psk_key_exchange_modes",
    "supported_versions",
    "signature_algorithms",
    "psk_dhe_ke",
    "compress_certificate",
    "application_settings",
    "delegated_credentials",
    "record_size_limit",
    "renegotiation_info",
)
_EXT_EARLY: tuple[str, ...] = (
    "server_name",
    "renegotiation_info",
    "supported_groups",
    "ec_point_formats",
    "session_ticket",
    "application_layer_protocol_negotiation",
    "signed_certificate_timestamp",
    "key_share",
    "psk_key_exchange_modes",
    "supported_versions",
    "signature_algorithms",
    "psk_dhe_ke",
    "application_settings",
    "renegotiation_info",
)
_EXT_HYBRID: tuple[str, ...] = (
    "server_name",
    "renegotiation_info",
    "supported_groups",
    "ec_point_formats",
    "session_ticket",
    "application_layer_protocol_negotiation",
    "signed_certificate_timestamp",
    "key_share",
    "psk_key_exchange_modes",
    "supported_versions",
    "signature_algorithms",
    "psk_dhe_ke",
    "application_settings",
    "delegated_credentials",
    "record_size_limit",
    "renegotiation_info",
)

# The TLS versions, oldest first.
_VERSIONS_MODERN: tuple[str, ...] = ("TLSv1.2", "TLSv1.3")
_VERSIONS_LEGACY: tuple[str, ...] = ("TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3")


@dataclass(frozen=True)
class TlsProfile:
    """The ClientHello of one Chrome release."""

    cipher_suites: tuple[str, ...]
    supported_groups: tuple[str, ...]
    signature_algorithms: tuple[str, ...]
    alpn_protocols: tuple[str, ...] = _ALPN_HTTP2
    versions: tuple[str, ...] = _VERSIONS_MODERN
    extension_order: tuple[str, ...] = _EXT_MODERN
    grease_enabled: bool = True
    #: The modes that the release supports.  A profile that claims to be
    #: Chrome 100 may speak TLS 1.0, because Chrome 100 still could.
    supports_legacy_versions: bool = False

    def as_dict(self) -> dict[str, list[str] | bool]:
        return {
            "cipher_suites": list(self.cipher_suites),
            "supported_groups": list(self.supported_groups),
            "signature_algorithms": list(self.signature_algorithms),
            "alpn_protocols": list(self.alpn_protocols),
            "versions": list(self.versions),
            "extension_order": list(self.extension_order),
            "grease_enabled": self.grease_enabled,
        }


def _build_major_profiles() -> dict[int, TlsProfile]:
    """The ClientHello of every release the fleet covers.

    The table is keyed by the Chrome major version.  A release that is
    not in the table inherits from the nearest earlier one, because the
    ClientHello of Chrome only changes a handful of times in a decade.
    """
    profiles: dict[int, TlsProfile] = {}

    # Chrome 100 through 106: the CBC suites are still there.
    for major in range(100, 107):
        profiles[major] = TlsProfile(
            cipher_suites=_CHROME_100_CIPHERS,
            supported_groups=_GROUPS_MODERN,
            signature_algorithms=_SIG_ECDSA_FIRST,
            alpn_protocols=_ALPN_HTTP2,
            versions=_VERSIONS_LEGACY,
            extension_order=_EXT_EARLY,
            grease_enabled=True,
            supports_legacy_versions=True,
        )

    # Chrome 107 through 130: the CBC suites are gone.
    for major in range(107, 131):
        profiles[major] = TlsProfile(
            cipher_suites=_CHROME_107_CIPHERS,
            supported_groups=_GROUPS_MODERN,
            signature_algorithms=_SIG_ECDSA_FIRST,
            alpn_protocols=_ALPN_HTTP2,
            versions=_VERSIONS_MODERN,
            extension_order=_EXT_MODERN,
            grease_enabled=True,
            supports_legacy_versions=False,
        )

    # Chrome 131 onwards: the hybrid key agreement is offered.
    for major in range(131, 200):
        profiles[major] = TlsProfile(
            cipher_suites=_CHROME_131_CIPHERS,
            supported_groups=_GROUPS_HYBRID,
            signature_algorithms=_SIG_RSA_FIRST,
            alpn_protocols=_ALPN_HTTP2,
            versions=_VERSIONS_MODERN,
            extension_order=_EXT_HYBRID,
            grease_enabled=True,
            supports_legacy_versions=False,
        )

    return profiles


#: The ClientHello of every covered release.
MAJOR_PROFILES: dict[int, TlsProfile] = _build_major_profiles()


def profile_for_major(major: int) -> TlsProfile:
    """The ClientHello of the given Chrome major version.

    A version that is outside the table inherits from the nearest one, so
    that a profile claiming a release the table does not know about still
    gets a coherent handshake instead of the stock one.
    """
    if major in MAJOR_PROFILES:
        return MAJOR_PROFILES[major]
    if major < min(MAJOR_PROFILES):
        return MAJOR_PROFILES[min(MAJOR_PROFILES)]
    return MAJOR_PROFILES[max(MAJOR_PROFILES)]
