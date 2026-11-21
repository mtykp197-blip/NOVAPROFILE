"""Network intelligence and leak-prevention subsystem.

Modules
-------
:mod:`novaprofile.network.proxy`
    Proxy endpoint resolution (Lead-owned).
:mod:`novaprofile.network.geo`
    Exit-IP -> country/region/city/ASN/ISP intelligence, plus timezone and
    geolocation derivation that matches the exit node.
:mod:`novaprofile.network.rotation`
    Proxy rotation manager implementing the ``manual`` / ``on_launch`` /
    ``on_interval`` / ``on_api_call`` / ``on_error`` rotation modes.
:mod:`novaprofile.network.dns`
    Chromium DNS-leak-prevention flag builder.
:mod:`novaprofile.network.webrtc`
    Chromium WebRTC leak-prevention flags and deterministic local-IP spoofing.
:mod:`novaprofile.network.leakcheck`
    Post-launch verification that the running browser leaks neither its real
    public IP nor its real DNS resolver.

Every module in this package is importable standalone and performs no network
I/O at import time.
"""

from __future__ import annotations

__all__ = [
    "dns",
    "geo",
    "leakcheck",
    "rotation",
    "webrtc",
]
