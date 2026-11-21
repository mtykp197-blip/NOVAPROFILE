"""Proxy rotation manager.

Implements the five rotation modes declared by the profile schema:

``manual``
    The operator picks the endpoint; the manager never contacts the provider.
``on_launch``
    A fresh endpoint is fetched once, right before the browser starts.
``on_interval``
    A fresh endpoint is fetched every ``interval_sec`` seconds.
``on_api_call``
    A fresh endpoint is fetched before every outbound API call the profile
    makes.  The launcher/control-plane calls :meth:`ProxyRotationManager.
    before_api_call` at the relevant seams.
``on_error``
    A fresh endpoint is fetched only when the current endpoint failed.

The provider response may use any of the following formats; all of them are
normalised into a :class:`~novaprofile.network.proxy.ProxyEndpoint`:

``ip:port``
    ``"198.51.100.7:1080"``
``ip:port:user:pass``
    ``"198.51.100.7:1080:alice:s3cret"``
``scheme://user:pass@ip:port``
    ``"socks5://alice:s3cret@198.51.100.7:1080"``
``{"proxy": "..."}`` / ``{"proxies": ["...", "..."]}``
    JSON wrappers around any of the formats above.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import httpx

try:  # pragma: no cover - lead-owned module
    from novaprofile.network.proxy import ProxyEndpoint, ProxyResolver
except Exception:  # pragma: no cover - parallel-development fallback
    # ``novaprofile.network.proxy`` is written by the lead agent.  Until it
    # lands we fall back to a local dataclass with the documented shape so
    # this module stays importable standalone and unit-testable.
    @dataclass(frozen=True)
    class ProxyEndpoint:  # type: ignore[no-redef]
        """A concrete proxy endpoint (protocol/host/port/credentials)."""

        protocol: str
        host: str
        port: int
        username: str | None = None
        password: str | None = None

        @property
        def is_direct(self) -> bool:
            return self.protocol == "direct"

        def to_url(self, redact: bool = False) -> str:
            scheme = self.protocol
            if self.username:
                if redact:
                    auth = f"{self.username}:***"
                else:
                    auth = self.username
                    if self.password:
                        auth = f"{auth}:{self.password}"
                return f"{scheme}://{auth}@{self.host}:{self.port}"
            return f"{scheme}://{self.host}:{self.port}"

        def chrome_switch(self) -> str:
            return f"--proxy-server={self.protocol}://{self.host}:{self.port}"

    class ProxyResolver:  # type: ignore[no-redef]
        """Resolve a profile's proxy configuration into an endpoint."""

        def resolve(self, cfg: Any) -> ProxyEndpoint | None:
            return None


__all__ = [
    "PROXY_FORMATS",
    "ProxyEndpoint",
    "ProxyResolver",
    "ProxyRotationError",
    "ProxyRotationManager",
    "RotationEvent",
    "RotationState",
    "parse_proxy_response",
]


#: Human readable description of every supported provider response format.
PROXY_FORMATS: Final[tuple[str, ...]] = (
    "ip:port",
    "ip:port:user:pass",
    "scheme://user:pass@ip:port",
    'json:{"proxy": "..."}',
    'json:{"proxies": ["..."]}',
)


class ProxyRotationError(RuntimeError):
    """Raised when a rotation could not produce a usable endpoint."""


class _Clock(Protocol):
    """Minimal monotonic clock abstraction (mockable in tests)."""

    def __call__(self) -> float: ...


@dataclass(frozen=True)
class RotationEvent:
    """One entry of the rotation audit history."""

    timestamp: datetime
    endpoint: ProxyEndpoint
    mode: str
    trigger: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "endpoint": self.endpoint.to_url(redact=True),
            "mode": self.mode,
            "trigger": self.trigger,
        }


@dataclass(frozen=True)
class RotationState:
    """Snapshot of the manager's runtime state."""

    endpoint: ProxyEndpoint | None
    mode: str
    last_rotation_at: float | None
    next_rotation_at: float | None
    rotation_count: int
    history: tuple[RotationEvent, ...]


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def parse_proxy_response(body: str | bytes) -> list[ProxyEndpoint]:
    """Parse a provider response body into zero or more endpoints.

    The body may be plain text (``ip:port``) or JSON.  JSON bodies may wrap
    the value under ``proxy`` / ``proxies`` / ``data`` / ``ip`` keys.
    """
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    stripped = text.strip()
    if not stripped:
        raise ProxyRotationError("empty rotation response")

    candidates: list[str] = []
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ProxyRotationError(f"rotation response is not valid JSON: {exc}") from exc
        candidates.extend(_candidates_from_json(payload))
    else:
        candidates.append(stripped)

    endpoints: list[ProxyEndpoint] = []
    for candidate in candidates:
        endpoint = _parse_endpoint(candidate)
        if endpoint is not None:
            endpoints.append(endpoint)
    if not endpoints:
        raise ProxyRotationError(f"rotation response contained no usable proxy: {stripped!r}")
    return endpoints


def _candidates_from_json(payload: Any) -> list[str]:
    """Extract proxy strings from a decoded JSON payload."""
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, list):
        out: list[str] = []
        for item in payload:
            out.extend(_candidates_from_json(item))
        return out
    if isinstance(payload, dict):
        for key in ("proxy", "proxies", "proxy_list", "data", "result", "ip", "server"):
            if key in payload:
                return _candidates_from_json(payload[key])
        return []
    return []


def _parse_endpoint(candidate: str) -> ProxyEndpoint | None:
    """Parse one proxy string into a :class:`ProxyEndpoint`."""
    text = candidate.strip()
    if not text:
        return None

    # scheme://user:pass@host:port
    if "://" in text:
        scheme, _, remainder = text.partition("://")
        scheme = scheme.strip().lower()
        if not scheme:
            scheme = "http"
        if "@" in remainder:
            credentials, _, hostport = remainder.rpartition("@")
            username, _, password = credentials.partition(":")
            host, port = _split_host_port(hostport)
            if host is None:
                return None
            return ProxyEndpoint(
                protocol=scheme,
                host=host,
                port=port,
                username=username or None,
                password=password or None,
            )
        host, port = _split_host_port(remainder)
        if host is None:
            return None
        return ProxyEndpoint(protocol=scheme, host=host, port=port)

    parts = text.split(":")
    if len(parts) == 2:
        host, port = _split_host_port(text)
        if host is None:
            return None
        return ProxyEndpoint(protocol="http", host=host, port=port)
    if len(parts) == 4:
        host, port = _split_host_port(f"{parts[0]}:{parts[1]}")
        if host is None:
            return None
        return ProxyEndpoint(
            protocol="http",
            host=host,
            port=port,
            username=parts[2] or None,
            password=parts[3] or None,
        )
    # Hostnames with more colons (IPv6) are not supported in the bare form.
    return None


def _split_host_port(value: str) -> tuple[str | None, int]:
    """Split ``host:port`` into a host string and an integer port."""
    text = value.strip()
    if not text or ":" not in text:
        return (None, 0)
    host, _, port_text = text.rpartition(":")
    host = host.strip()
    port_text = port_text.strip()
    if not host:
        return (None, 0)
    try:
        port = int(port_text)
    except ValueError:
        return (None, 0)
    if not 0 < port <= 65535:
        return (None, 0)
    return (host, port)


# --------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------


class ProxyRotationManager:
    """Manage proxy endpoint rotation according to the profile's mode."""

    def __init__(
        self,
        cfg: Any,
        *,
        proxy_cfg: Any = None,
        resolver: ProxyResolver | None = None,
        client: httpx.AsyncClient | None = None,
        clock: _Clock | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._cfg = cfg
        self._proxy_cfg = proxy_cfg
        self._resolver: ProxyResolver = resolver or ProxyResolver()
        self._client: httpx.AsyncClient | None = client
        self._clock: _Clock = clock or time.monotonic
        self._timeout: float = timeout
        self._endpoint: ProxyEndpoint | None = None
        self._history: list[RotationEvent] = []
        self._rotation_count: int = 0
        self._last_rotation_at: float | None = None
        self._next_rotation_at: float | None = None
        self._failed: bool = False

    # -- configuration accessors (duck-typed on the schema model) ----------

    @property
    def mode(self) -> str:
        return str(getattr(self._cfg, "mode", "manual") or "manual")

    @property
    def rotate_url(self) -> str | None:
        value = getattr(self._cfg, "rotate_url", None)
        return str(value) if value else None

    @property
    def http_method(self) -> str:
        return str(getattr(self._cfg, "http_method", "GET") or "GET").upper()

    @property
    def headers(self) -> dict[str, str]:
        raw = getattr(self._cfg, "headers", None) or {}
        return {str(key): str(value) for key, value in dict(raw).items()}

    @property
    def body(self) -> str | None:
        value = getattr(self._cfg, "body", None)
        if value is None:
            return None
        return str(value)

    @property
    def cooldown_sec(self) -> float:
        return float(getattr(self._cfg, "cooldown_sec", 0) or 0)

    @property
    def interval_sec(self) -> float:
        return float(getattr(self._cfg, "interval_sec", 0) or 0)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._cfg, "enabled", True))

    # -- runtime state -----------------------------------------------------

    @property
    def endpoint(self) -> ProxyEndpoint | None:
        return self._endpoint

    @property
    def rotation_count(self) -> int:
        return self._rotation_count

    @property
    def history(self) -> list[RotationEvent]:
        return list(self._history)

    @property
    def last_rotation_at(self) -> float | None:
        return self._last_rotation_at

    @property
    def next_rotation_at(self) -> float | None:
        return self._next_rotation_at

    def state(self) -> RotationState:
        """Return an immutable snapshot of the manager's state."""
        return RotationState(
            endpoint=self._endpoint,
            mode=self.mode,
            last_rotation_at=self._last_rotation_at,
            next_rotation_at=self._next_rotation_at,
            rotation_count=self._rotation_count,
            history=tuple(self._history),
        )

    # -- lifecycle ---------------------------------------------------------

    async def on_launch(self) -> ProxyEndpoint | None:
        """Resolve the endpoint to use for a browser launch.

        For ``manual`` mode the statically configured endpoint is returned.
        For ``on_launch``/``on_interval`` a fresh endpoint is fetched when the
        cooldown has expired.
        """
        mode = self.mode
        if mode == "manual":
            self._endpoint = self._resolve_static()
            return self._endpoint
        if mode in ("on_launch", "on_interval"):
            return await self.rotate(trigger=f"launch:{mode}")
        if mode == "on_api_call":
            # The endpoint is fetched lazily on the first API call.
            return self._endpoint or self._resolve_static()
        if mode == "on_error":
            return self._endpoint or self._resolve_static()
        return self._endpoint or self._resolve_static()

    async def before_api_call(self) -> ProxyEndpoint | None:
        """Return the endpoint to use for the next outbound API call.

        In ``on_api_call`` mode a fresh endpoint is fetched (subject to the
        cooldown); otherwise the current endpoint is reused.
        """
        if self.mode == "on_api_call":
            return await self.rotate(trigger="api_call")
        return self._endpoint or self._resolve_static()

    async def on_error(self, error: BaseException | None = None) -> ProxyEndpoint | None:
        """Handle a transport failure.

        In ``on_error`` mode a fresh endpoint is fetched.  In every other
        mode the failure is recorded but no rotation happens.
        """
        self._failed = True
        if self.mode == "on_error":
            return await self.rotate(trigger="error", ignore_cooldown=True)
        return self._endpoint

    async def rotate(
        self, *, trigger: str = "manual", ignore_cooldown: bool = False
    ) -> ProxyEndpoint | None:
        """Fetch a new endpoint from the configured rotation provider."""
        if not self.enabled:
            return self._endpoint
        url = self.rotate_url
        if not url:
            raise ProxyRotationError("proxy rotation is enabled but rotate_url is not set")
        if not ignore_cooldown and not self._cooldown_expired():
            return self._endpoint

        body = await self._fetch(url)
        endpoints = parse_proxy_response(body)
        endpoint = endpoints[0]
        self._endpoint = endpoint
        self._rotation_count += 1
        now = self._clock()
        self._last_rotation_at = now
        # In interval mode the next rotation is scheduled by the interval,
        # in every other mode by the cooldown.
        if self.mode == "on_interval" and self.interval_sec > 0:
            self._next_rotation_at = now + self.interval_sec
        else:
            self._next_rotation_at = now + self.cooldown_sec
        self._history.append(
            RotationEvent(
                timestamp=datetime.now(UTC),
                endpoint=endpoint,
                mode=self.mode,
                trigger=trigger,
            )
        )
        self._failed = False
        return endpoint

    def should_rotate_now(self) -> bool:
        """Return ``True`` when the interval schedule says a rotation is due."""
        if self.mode != "on_interval":
            return False
        if self.interval_sec <= 0:
            return False
        if self._next_rotation_at is None:
            return True
        return self._clock() >= self._next_rotation_at

    def mark_failed(self) -> None:
        """Record that the current endpoint failed."""
        self._failed = True

    def clear_history(self) -> None:
        """Drop the rotation audit history."""
        self._history.clear()

    # -- internals ---------------------------------------------------------

    def _cooldown_expired(self) -> bool:
        if self.cooldown_sec <= 0:
            return True
        if self._last_rotation_at is None:
            return True
        return (self._clock() - self._last_rotation_at) >= self.cooldown_sec

    def _resolve_static(self) -> ProxyEndpoint | None:
        """Resolve the statically configured proxy from the schema config."""
        proxy_cfg = self._proxy_cfg
        if proxy_cfg is None:
            proxy_cfg = getattr(self._cfg, "proxy", None)
        if proxy_cfg is None:
            return None
        try:
            return self._resolver.resolve(proxy_cfg)
        except Exception:  # noqa: BLE001 - a broken static proxy is not fatal here
            return None

    async def _fetch(self, url: str) -> str:
        """Perform the rotation HTTP request."""
        method = self.http_method
        headers = self.headers
        body = self.body
        client = self._client
        if client is not None:
            response = await client.request(
                method, url, headers=headers, content=body, timeout=self._timeout
            )
            response.raise_for_status()
            return response.text

        async with httpx.AsyncClient(timeout=self._timeout) as owned:
            response = await owned.request(
                method, url, headers=headers, content=body, timeout=self._timeout
            )
            response.raise_for_status()
            return response.text
