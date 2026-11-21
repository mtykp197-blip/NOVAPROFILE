"""Proxy endpoint handling.

The launcher turns a :class:`~novaprofile.schema.network.Proxy` into a
Chromium ``--proxy-server`` switch and, where required, a PAC-style rule
list.  This module owns that translation so that the launcher, the API and
the rotation manager all agree on the semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from ..schema.network import Proxy

#: Chromium accepts a comma-separated list of proxy schemes, for example
#: ``socks5://host:1080;http://host:8080``.  The order matters: the first
#: entry handles every scheme unless the entry itself restricts one.
_SCHEME_FOR_PROTOCOL = {
    "http": "http",
    "https": "https",
    "socks4": "socks4",
    "socks5": "socks5",
    "socks5h": "socks5h",
    "ssh": "socks5",
    "direct": "direct",
}


@dataclass(frozen=True)
class ProxyEndpoint:
    """A resolved proxy endpoint."""

    protocol: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def is_direct(self) -> bool:
        """Whether traffic should bypass any proxy entirely."""
        return self.protocol == "direct" or not self.host or not self.port

    @property
    def scheme(self) -> str:
        """The URL scheme Chromium expects for this protocol."""
        return _SCHEME_FOR_PROTOCOL.get(self.protocol, "http")

    def to_url(self, redact: bool = False) -> str:
        """The full proxy URL, optionally with the password redacted."""
        if self.is_direct:
            return "direct://"
        credentials = ""
        if self.username:
            if redact:
                credentials = f"{quote(self.username, safe='')}:***@"
            elif self.password:
                credentials = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
            else:
                credentials = f"{quote(self.username, safe='')}@"
        return f"{self.scheme}://{credentials}{self.host}:{self.port}"

    def chrome_switch(self) -> str:
        """The value for Chromium's ``--proxy-server`` switch."""
        if self.is_direct:
            return "direct://"
        return f"{self.scheme}://{self.host}:{self.port}"

    def pac_url(self) -> str | None:
        """A ``PROXY host:port`` rule for a PAC script, or ``None``."""
        if self.is_direct:
            return None
        return f"PROXY {self.host}:{self.port}"

    def __str__(self) -> str:
        return self.to_url(redact=True)


class ProxyResolver:
    """Turns a schema proxy into a :class:`ProxyEndpoint`."""

    def resolve(self, cfg: Proxy) -> ProxyEndpoint | None:
        """Resolve a proxy configuration.

        Returns ``None`` when the proxy is disabled or configured as
        ``direct``, which the launcher reads as "do not pass a proxy switch
        at all".
        """
        if not cfg.enabled or cfg.protocol == "direct":
            return None
        if not cfg.host or not cfg.port:
            return None
        return ProxyEndpoint(
            protocol=cfg.protocol,
            host=cfg.host,
            port=cfg.port,
            username=cfg.username or None,
            password=cfg.password or None,
        )


def parse_proxy_string(text: str) -> ProxyEndpoint | None:
    """Parse the common proxy string formats.

    Supported forms::

        ip:port
        ip:port:user:pass
        scheme://ip:port
        scheme://user:pass@ip:port

    Returns ``None`` when the string cannot be parsed.
    """
    cleaned = text.strip()
    if not cleaned:
        return None

    scheme = ""
    if "://" in cleaned:
        scheme, _, cleaned = cleaned.partition("://")

    credentials = ""
    user: str | None = None
    password: str | None = None
    if "@" in cleaned:
        credentials, _, cleaned = cleaned.rpartition("@")
        if ":" in credentials:
            user, _, password = credentials.partition(":")
        else:
            user, password = credentials, ""

    parts = cleaned.split(":")
    if len(parts) < 2:
        return None
    host = parts[0]
    try:
        port = int(parts[1])
    except ValueError:
        return None
    if not host or not 0 < port < 65536:
        return None

    # The ``host:port:user:pass`` form carries the credentials after the
    # port rather than before the host.
    if len(parts) >= 4 and user is None:
        user, password = parts[2], ":".join(parts[3:])

    protocol = scheme.lower() if scheme else "http"
    if protocol not in _SCHEME_FOR_PROTOCOL:
        protocol = "http"

    return ProxyEndpoint(
        protocol=protocol,
        host=host,
        port=port,
        username=user or None,
        password=password or None,
    )


__all__ = ["ProxyEndpoint", "ProxyResolver", "parse_proxy_string"]
