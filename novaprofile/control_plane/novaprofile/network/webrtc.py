"""WebRTC leak prevention.

Chromium's WebRTC stack gathers ICE candidates from every local interface.
On a real Android device that means the Wi-Fi / cellular interface address
leaks through ``RTCPeerConnection`` even when the page traffic itself is
proxied.  This module builds the flags that suppress those candidates and
offers a deterministic local-IP spoofing helper.

Flags per ``webrtc.mode``
-------------------------

``real``
    No override at all -- Chromium keeps its default behaviour.
``altered``
    ``--force-webrtc-ip-handling-policy=default_public_interface_only``
    plus ``--webrtc-ip-handling-policy=default_public_interface_only``.
``disabled``
    ``--force-webrtc-ip-handling-policy=disable_non_proxied_udp``
    plus ``--webrtc-ip-handling-policy=disable_non_proxied_udp``.
``default_public_interface_only``
    ``--force-webrtc-ip-handling-policy=default_public_interface_only``
    plus ``--webrtc-ip-handling-policy=default_public_interface_only``.
``proxy_only``
    ``--force-webrtc-ip-handling-policy=proxy_only``
    plus ``--webrtc-ip-handling-policy=proxy_only``.
``direct``
    ``--force-webrtc-ip-handling-policy=default``
    plus ``--webrtc-ip-handling-policy=default``.  A profile that does not
    use a proxy behaves like an ordinary handset: the candidates are
    reachable, and only the mDNS obfuscation hides the local address.

Additionally the guard hides the local interface addresses behind mDNS
candidates when ``mdns_enabled`` is set (Chromium's ``WebRtcHideLocalIpsWithMdns``
feature stays enabled, so ``.local`` host candidates replace the raw
addresses), and, when UDP has to be blocked, adds
``--force-webrtc-ip-handling-policy=disable_non_proxied_udp``.

A ``relay`` ICE transport policy is expressed with
``--force-webrtc-ip-handling-policy=relay`` so that only TURN relays (and
therefore only proxied traffic) are used.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from typing import Any, Final

__all__ = [
    "WEBRTC_MODES",
    "WebRtcGuard",
    "build_webrtc_args",
    "has_mdns_hostname",
    "is_local_address",
    "public_ip_mismatch",
    "should_block_udp",
    "spoofed_local_ip",
]


#: Every WebRTC mode supported by the profile schema.
WEBRTC_MODES: Final[tuple[str, ...]] = (
    "real",
    "altered",
    "disabled",
    "default_public_interface_only",
    "proxy_only",
    "direct",
)

#: Chromium's IP handling policies, keyed by profile mode.
_IP_HANDLING_POLICY: Final[dict[str, str]] = {
    "altered": "default_public_interface_only",
    "disabled": "disable_non_proxied_udp",
    "default_public_interface_only": "default_public_interface_only",
    "proxy_only": "proxy_only",
    # A profile without a proxy keeps Chromium's own behaviour: the
    # candidates have to be reachable, exactly as they are on a real
    # handset.  The mDNS obfuscation still hides the local address.
    "direct": "default",
}


def spoofed_local_ip(master_seed: str) -> str:
    """Derive a deterministic RFC1918 address from a 256-bit hex seed.

    The seed is hashed with SHA-256; the first two bytes select the
    ``10.0.0.0/8`` range and the following two bytes the host part.  The same
    seed always produces the same address and two different seeds practically
    never collide.
    """
    digest = hashlib.sha256(master_seed.encode("utf-8")).digest()
    # First byte picks the private range: 0 -> 10.0.0.0/8, 1 -> 192.168.0.0/16.
    if digest[0] % 2 == 0:
        return f"10.{digest[1]}.{digest[2]}.{digest[3]}"
    return f"192.168.{digest[1]}.{digest[2]}"


def public_ip_mismatch(local_public_ip: str | None, spoofed_ip: str | None) -> bool:
    """Return ``True`` when the exposed public IP differs from the spoofed one.

    A mismatch means the browser is still exposing an address that does not
    belong to the profile's spoofed identity -- i.e. a leak.
    """
    if not local_public_ip or not spoofed_ip:
        return False
    try:
        exposed = ipaddress.ip_address(local_public_ip.strip())
        expected = ipaddress.ip_address(spoofed_ip.strip())
    except ValueError:
        return local_public_ip.strip() != spoofed_ip.strip()
    return exposed != expected


def should_block_udp(cfg: Any) -> bool:
    """Return ``True`` when non-proxied UDP must be blocked.

    The decision is driven by ``block_non_proxied_udp`` on the WebRTC
    configuration.  The ``disabled`` WebRTC mode implies blocking as well,
    because the mode exists precisely to stop UDP from leaving the machine.
    """
    if bool(getattr(cfg, "block_non_proxied_udp", False)):
        return True
    mode = str(getattr(cfg, "mode", "real") or "real")
    return mode == "disabled"


def build_webrtc_args(cfg: Any) -> list[str]:
    """Build the ordered Chromium flags implementing the configured WebRTC mode.

    The output is deterministic: the same configuration always yields the
    exact same list, in the same order.
    """
    mode = str(getattr(cfg, "mode", "real") or "real")
    block_udp = should_block_udp(cfg)
    mdns_enabled = bool(getattr(cfg, "mdns_enabled", False))
    transport_policy = str(getattr(cfg, "ice_transport_policy", "all") or "all")

    args: list[str] = []

    if mode == "real":
        if block_udp:
            args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    else:
        policy = _IP_HANDLING_POLICY.get(mode)
        if policy is None:
            raise ValueError(f"unsupported webrtc mode: {mode!r}")
        args.append(f"--force-webrtc-ip-handling-policy={policy}")
        args.append(f"--webrtc-ip-handling-policy={policy}")

    if block_udp and mode not in ("disabled", "real"):
        # The profile asked for UDP blocking on top of the mode policy.
        args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")

    if transport_policy == "relay":
        # Only TURN relays are used, so ICE never touches a local interface.
        args.append("--force-webrtc-ip-handling-policy=relay")

    if mdns_enabled:
        # Keep Chromium's mDNS hiding active so that the real interface
        # addresses are replaced by opaque ``.local`` host candidates.
        args.append("--enable-features=WebRtcHideLocalIpsWithMdns")
    else:
        # Without mDNS hiding Chromium would publish the raw addresses.
        args.append("--disable-features=WebRtcHideLocalIpsWithMdns")

    field_trials = _first_attr(cfg, "field_trials", "force_field_trials")
    if field_trials:
        args.append(f"--force-fieldtrials={field_trials}")

    enabled_features = _first_attr(cfg, "enable_features")
    if enabled_features:
        args.append(f"--enable-features={enabled_features}")

    return args


class WebRtcGuard:
    """Build WebRTC flags and evaluate WebRTC leak conditions."""

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg

    @property
    def mode(self) -> str:
        return str(getattr(self._cfg, "mode", "real") or "real")

    def build(self) -> list[str]:
        """Build the Chromium flags for the configured WebRTC mode."""
        return build_webrtc_args(self._cfg)

    def block_udp(self) -> bool:
        """Return ``True`` when non-proxied UDP must be blocked."""
        return should_block_udp(self._cfg)

    def spoofed_local_ip(self, master_seed: str) -> str:
        """Derive the deterministic spoofed local address for a profile."""
        return spoofed_local_ip(master_seed)

    def check_candidates(
        self,
        candidates: list[str],
        *,
        master_seed: str | None = None,
        expected_public_ip: str | None = None,
    ) -> list[str]:
        """Return the list of leaking ICE candidates.

        A candidate leaks when it exposes a public address that differs from
        the profile's spoofed address, or when it exposes a host name / mDNS
        candidate at all.
        """
        spoofed = spoofed_local_ip(master_seed) if master_seed else expected_public_ip
        leaks: list[str] = []
        for candidate in candidates:
            text = candidate.strip()
            if not text:
                continue
            if has_mdns_hostname(text):
                leaks.append(text)
                continue
            address = _extract_ip(text)
            if address is None:
                continue
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if is_local_address(parsed):
                continue
            if expected_public_ip and address == expected_public_ip.strip():
                continue
            if spoofed and public_ip_mismatch(address, spoofed):
                leaks.append(text)
        return leaks


def has_mdns_hostname(candidate: str) -> bool:
    """Return ``True`` when the candidate carries an mDNS ``.local`` host name."""
    return bool(re.search(r"\b[a-z0-9-]+\.local\b", candidate, re.IGNORECASE))


def is_local_address(parsed: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return ``True`` for addresses that never reveal the public IP.

    Only the genuinely local ranges are excluded: RFC1918 private space,
    loopback, link-local and the IPv6 unique-local / link-local ranges.
    Documentation ranges such as ``203.0.113.0/24`` are *public* addresses
    as far as leak detection is concerned, so they are not filtered out.
    """
    if parsed.is_loopback or parsed.is_link_local:
        return True
    if parsed.version == 4:
        return (
            parsed in ipaddress.ip_network("10.0.0.0/8")
            or parsed in ipaddress.ip_network("172.16.0.0/12")
            or parsed in ipaddress.ip_network("192.168.0.0/16")
        )
    return parsed in ipaddress.ip_network("fc00::/7")


def _extract_ip(candidate: str) -> str | None:
    """Pull the IPv4/IPv6 address out of an ICE candidate string.

    ICE candidates look like
    ``candidate:842163049 1 udp 1677729535 192.0.2.1 54321 typ srflx ...``
    where the address follows the priority/foundation/protocol/priority
    fields.  IPv6 candidates use the ``[2001:db8::1]`` bracket form.
    """
    bracket = re.search(r"\[([0-9A-Fa-f:.]+)\]", candidate)
    if bracket:
        return bracket.group(1)
    dotted = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", candidate)
    if dotted:
        return dotted.group(1)
    return None


def _first_attr(cfg: Any, *names: str) -> str | None:
    """Return the first non-empty string attribute among ``names``."""
    for name in names:
        value = getattr(cfg, name, None)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        if isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return ",".join(items)
            continue
        text = str(value)
        if text.strip():
            return text.strip()
    return None
