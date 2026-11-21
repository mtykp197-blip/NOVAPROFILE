"""DNS leak prevention.

Builds the Chromium command-line flags that force every DNS query through
the profile's proxy path, and provides a pure-string analysis helper that
decides whether a host would escape the proxy.

Chromium background
-------------------
Chromium resolves host names through the *host resolver* before it opens a
socket.  When a proxy is configured, Chromium can be told to stop resolving
locally at all via ``--host-resolver-rules``:

``MAP * ~NOTFOUND``
    Never resolve locally -- the name is handed to the proxy, which performs
    the lookup.  This is the canonical "proxy-side DNS" configuration.
``MAP * <address>``
    Resolve locally, but only against the given DNS server address.  Useful
    for a custom UDP resolver or a DNS-over-TLS host reached through the
    proxy tunnel.
``EXCLUDE <pattern>``
    Exempt a host from the mapping (a leak: the host uses the system
    resolver).  ``EXCLUDE localhost`` is the standard exemption for the
    loopback resolver and is *not* considered a leak.

Flags per mode
--------------

``system``
    No resolver override; the platform resolver is used.  When
    ``prevent_dns_leak`` is set the flags still map everything through the
    proxy so that the *system* resolver is never consulted.
``proxy_remote``
    ``--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost`` -- the
    proxy performs the lookup.
``custom_udp``
    ``--host-resolver-rules=MAP * <server>, EXCLUDE localhost`` combined with
    ``--disable-features=AsyncDns`` so the built-in asynchronous resolver
    cannot bypass the mapping.
``doh``
    ``--dns-over-https-mode=secure`` plus
    ``--dns-over-https-templates=https://<host>/dns-query`` and
    ``--enable-features=AsyncDns``.
``dot``
    ``--host-resolver-rules=MAP * <dot-host>, EXCLUDE localhost`` combined
    with ``--disable-features=AsyncDns``; the DoT host itself is reached
    through the proxy tunnel.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = [
    "DNS_MODES",
    "DnsGuard",
    "HOST_RESOLVER_RULES_EXCLUDE_LOCALHOST",
    "build_dns_args",
    "is_leaking",
    "parse_host_resolver_rules",
]


#: Every DNS mode supported by the profile schema.
DNS_MODES: Final[tuple[str, ...]] = ("system", "proxy_remote", "custom_udp", "doh", "dot")

#: The canonical "resolve everything at the proxy" host-resolver rule.
HOST_RESOLVER_RULES_EXCLUDE_LOCALHOST: Final[str] = "MAP * ~NOTFOUND , EXCLUDE localhost"


# --------------------------------------------------------------------------
# Rule parsing
# --------------------------------------------------------------------------


def parse_host_resolver_rules(rules: str) -> list[tuple[str, list[str]]]:
    """Parse a ``--host-resolver-rules`` value into ``(kind, arguments)`` pairs.

    Chromium's grammar is a comma separated list of rules where each rule is
    a keyword (``MAP`` / ``EXCLUDE`` / ``INCLUDE``) followed by arguments.
    ``MAP`` takes a host pattern and a replacement; ``EXCLUDE`` / ``INCLUDE``
    take a single host pattern.

    The returned list preserves the original order because rule precedence
    in Chromium depends on it.
    """
    parsed: list[tuple[str, list[str]]] = []
    if not rules:
        return parsed
    for chunk in _split_rules(rules):
        tokens = chunk.split()
        if not tokens:
            continue
        kind = tokens[0].upper()
        args = [token for token in tokens[1:] if token]
        if kind == "MAP" and len(args) >= 2 or kind in ("EXCLUDE", "INCLUDE") and len(args) >= 1:
            parsed.append((kind, args))
    return parsed


def _split_rules(rules: str) -> list[str]:
    """Split a rule string on top-level commas."""
    chunks: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    for char in rules:
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        if char == "," and bracket_depth == 0:
            chunks.append("".join(current))
            current = []
            continue
        current.append(char)
    chunks.append("".join(current))
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _host_matches(pattern: str, host: str) -> bool:
    """Match a Chromium host pattern against a concrete host name.

    Supported patterns: ``*`` (any host), ``*.suffix``, ``prefix*`` and
    exact names.  Matching is case-insensitive.
    """
    pattern = pattern.strip().lower()
    host = host.strip().lower()
    if not pattern:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.com"
        return host == suffix[1:] or host.endswith(suffix)
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return host.startswith(prefix)
    return host == pattern


def is_leaking(resolver_rules: str, expected_host: str) -> bool:
    """Return ``True`` when ``expected_host`` would bypass the proxy.

    A host leaks when its name resolution escapes the proxy path, i.e. when

    * an ``EXCLUDE`` rule exempts it (Chromium resolves it with the system
      resolver), or
    * no ``MAP`` rule applies to it (Chromium falls back to the system
      resolver), or
    * a ``MAP`` rule redirects it to a concrete resolver address, which is a
      direct (un-proxied) lookup rather than a proxy-side lookup.

    ``MAP * ~NOTFOUND`` is the safe configuration: the name is never
    resolved locally and the proxy performs the lookup.

    Rule precedence follows Chromium: ``EXCLUDE`` removes a host from the
    mapping entirely, while ``INCLUDE`` re-adds it.
    """
    host = expected_host.strip().lower()
    if not host:
        return True

    rules = parse_host_resolver_rules(resolver_rules)
    if not rules:
        # No rules at all -> Chromium uses the system resolver.
        return True

    # EXCLUDE / INCLUDE are evaluated first: they decide whether the host
    # takes part in the host-resolver mapping at all.
    excluded = False
    for kind, args in rules:
        if kind == "EXCLUDE" and _host_matches(args[0], host):
            excluded = True
        elif kind == "INCLUDE" and _host_matches(args[0], host):
            excluded = False

    if excluded:
        return True

    for kind, args in rules:
        if kind != "MAP":
            continue
        pattern, replacement = args[0], args[1]
        if not _host_matches(pattern, host):
            continue
        # "~NOTFOUND" -> the name is resolved by the proxy, never locally.
        # A concrete replacement address means a direct lookup.
        return not replacement.startswith("~")

    # No MAP rule matched the host -> system resolver -> leak.
    return True


# --------------------------------------------------------------------------
# Flag builder
# --------------------------------------------------------------------------


class DnsGuard:
    """Build Chromium DNS flags and reason about DNS leaks."""

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg

    # -- configuration accessors ------------------------------------------

    @property
    def mode(self) -> str:
        return str(getattr(self._cfg, "mode", "system") or "system")

    @property
    def prevent_dns_leak(self) -> bool:
        return bool(getattr(self._cfg, "prevent_dns_leak", False))

    @property
    def custom_server(self) -> str | None:
        """The custom UDP DNS server address (``host`` or ``host:port``)."""
        return _first_attr(
            self._cfg, "custom_server", "dns_server", "server", "custom_dns", "resolver"
        )

    @property
    def servers(self) -> list[str]:
        """Every configured DNS server address."""
        raw = getattr(self._cfg, "servers", None)
        if raw is None:
            single = _first_attr(self._cfg, "custom_server", "dns_server", "server")
            return [single] if single else []
        if isinstance(raw, str):
            return [raw.strip()] if raw.strip() else []
        return [str(item).strip() for item in raw if str(item).strip()]

    @property
    def doh_template(self) -> str | None:
        """The full DoH endpoint template URL."""
        template = _first_attr(self._cfg, "doh_template", "doh_url", "doh_endpoint")
        if template:
            return template
        host = _first_attr(self._cfg, "doh_host")
        return f"https://{host}/dns-query" if host else None

    @property
    def dot_host(self) -> str | None:
        """The DNS-over-TLS host (optionally ``host:port``)."""
        return _first_attr(self._cfg, "dot_host", "dot_server", "tls_server")

    @property
    def hosts_mapping(self) -> str | None:
        """Extra static ``host ip`` entries appended to the resolver rules."""
        return _first_attr(self._cfg, "hosts_mapping")

    # -- flag building -----------------------------------------------------

    def build(self) -> list[str]:
        """Build the Chromium flags for the configured DNS mode."""
        return build_dns_args(self._cfg)

    def host_resolver_rules(self) -> str:
        """Return the ``--host-resolver-rules`` value for the current config."""
        for flag in self.build():
            if flag.startswith("--host-resolver-rules="):
                return flag.partition("=")[2]
        return ""

    def leaks_for(self, expected_host: str) -> bool:
        """Return ``True`` when ``expected_host`` escapes the proxy path."""
        return is_leaking(self.host_resolver_rules(), expected_host)


def build_dns_args(cfg: Any) -> list[str]:
    """Build the ordered Chromium flags implementing the configured DNS mode.

    The output is deterministic: the same configuration always yields the
    exact same list, in the same order.
    """
    mode = str(getattr(cfg, "mode", "system") or "system")
    prevent_leak = bool(getattr(cfg, "prevent_dns_leak", False))

    custom_server = _first_attr(
        cfg, "custom_server", "dns_server", "server", "custom_dns", "resolver"
    )
    servers = _server_list(cfg)
    if servers and custom_server is None:
        custom_server = servers[0]
    doh_template = _first_attr(cfg, "doh_template", "doh_url", "doh_endpoint")
    if doh_template is None:
        doh_host = _first_attr(cfg, "doh_host")
        doh_template = f"https://{doh_host}/dns-query" if doh_host else None
    dot_host = _first_attr(cfg, "dot_host", "dot_server", "tls_server")
    hosts_mapping = _first_attr(cfg, "hosts_mapping")

    args: list[str] = []

    if mode == "system":
        # No resolver override.  Built-in DoH is disabled so that Chromium
        # cannot silently start using a hard-coded DoH server.
        args.append("--dns-over-https-mode=off")
        if prevent_leak:
            args.append(f"--host-resolver-rules={HOST_RESOLVER_RULES_EXCLUDE_LOCALHOST}")

    elif mode == "proxy_remote":
        args.append(f"--host-resolver-rules={HOST_RESOLVER_RULES_EXCLUDE_LOCALHOST}")
        args.append("--dns-over-https-mode=off")

    elif mode == "custom_udp":
        server = custom_server or "1.1.1.1"
        rules = f"MAP * {server}, EXCLUDE localhost"
        if hosts_mapping:
            rules = f"{hosts_mapping}, {rules}"
        args.append(f"--host-resolver-rules={rules}")
        args.append("--disable-features=AsyncDns")
        args.append("--dns-over-https-mode=off")

    elif mode == "doh":
        template = doh_template or "https://dns.google/dns-query"
        args.append("--dns-over-https-mode=secure")
        args.append(f"--dns-over-https-templates={template}")
        args.append("--enable-features=AsyncDns")
        if prevent_leak:
            args.append(f"--host-resolver-rules={HOST_RESOLVER_RULES_EXCLUDE_LOCALHOST}")

    elif mode == "dot":
        server = dot_host or "1.1.1.1"
        rules = f"MAP * {server}, EXCLUDE localhost"
        if hosts_mapping:
            rules = f"{hosts_mapping}, {rules}"
        args.append(f"--host-resolver-rules={rules}")
        args.append("--disable-features=AsyncDns")
        args.append("--dns-over-https-mode=off")

    else:
        raise ValueError(f"unsupported dns mode: {mode!r}")

    return args


def _server_list(cfg: Any) -> list[str]:
    """Return the list of configured DNS server addresses."""
    raw = getattr(cfg, "servers", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    try:
        return [str(item).strip() for item in raw if str(item).strip()]
    except TypeError:
        return []


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
        if isinstance(value, dict):
            entries = [f"{host} {ip}" for host, ip in value.items()]
            if entries:
                return ", ".join(entries)
            continue
        text = str(value)
        if text.strip():
            return text.strip()
    return None
