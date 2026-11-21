"""Authentication and exposure control for the control-plane API.

The control plane stores proxy credentials, seeds and the configuration of
every profile.  Any of those is sensitive: a leaked seed regenerates the
exact fingerprint, and a leaked proxy credential hands over the operator's
egress.

The API therefore refuses to serve a request that carries no credential as
soon as a key is configured, and it *binds to the loopback interface* by
default so that the machine's network cannot reach it at all.

What is protected
-----------------

``NOVA_API_KEY``
    A shared secret.  When it is set, every request must carry it in the
    ``X-Nova-Key`` header (or as a bearer token).  The comparison is
    constant-time so that the endpoint cannot be used as an oracle to guess
    the key one character at a time.

``NOVA_API_HOST``
    The interface the server binds to.  The default is the loopback
    address; a control plane that is reachable from the network by accident
    is the most common way a local tool leaks.

``NOVA_CORS_ORIGINS``
    The origins allowed to call the API from a browser.  The default is
    empty -- no browser origin is allowed -- because the API is normally
    called by a native desktop application, not by a web page.

``NOVA_API_REQUIRE_AUTH``
    Set to ``0`` to disable the credential check.  This is only sensible
    when the server is bound to the loopback interface and no browser
    origin is allowed.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

#: The environment variable holding the shared API key.
API_KEY_ENV_VAR: str = "NOVA_API_KEY"

#: The environment variable holding the interface to bind to.
API_HOST_ENV_VAR: str = "NOVA_API_HOST"

#: The environment variable holding the port to bind to.
API_PORT_ENV_VAR: str = "NOVA_API_PORT"

#: The environment variable that can switch the credential check off.
API_REQUIRE_AUTH_ENV_VAR: str = "NOVA_API_REQUIRE_AUTH"

#: The header the API key is read from.
API_KEY_HEADER: str = "x-nova-key"

#: The loopback address the control plane binds to by default.
DEFAULT_HOST: str = "127.0.0.1"

#: The port the control plane listens on by default.
DEFAULT_PORT: int = 8756


def api_key() -> str:
    """Return the configured API key, or an empty string."""

    return os.environ.get(API_KEY_ENV_VAR, "").strip()


def api_host() -> str:
    """Return the interface to bind to.

    The default is the loopback address, so the control plane is not
    reachable from the network unless the operator asks for it.
    """

    return os.environ.get(API_HOST_ENV_VAR, "").strip() or DEFAULT_HOST


def api_port() -> int:
    """Return the port to listen on."""

    raw = os.environ.get("NOVA_API_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def require_auth() -> bool:
    """Whether a credential is required.

    Authentication is required whenever a key is configured.  It can be
    switched off explicitly, but only makes sense on the loopback
    interface.
    """

    raw = os.environ.get(API_REQUIRE_AUTH_ENV_VAR, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(api_key())


def is_loopback(host: str | None = None) -> bool:
    """Whether the server is bound to the loopback interface."""

    target = (host or api_host()).strip().lower()
    return target in {"127.0.0.1", "::1", "localhost"}


def verify_key(candidate: str | None) -> bool:
    """Compare a presented key with the configured one, in constant time."""

    expected = api_key()
    if not expected:
        # No key is configured: authentication is disabled, so every
        # candidate is accepted.
        return True
    if candidate is None:
        return False
    return hmac.compare_digest(candidate.strip(), expected)


@dataclass(frozen=True)
class ExposureReport:
    """How exposed the control plane currently is.

    The report is what the ``/api/health`` endpoint and the startup banner
    show, so that an operator can see at a glance whether the API is
    reachable from outside the machine.
    """

    host: str
    port: int
    auth_required: bool
    loopback: bool
    cors_origins: tuple[str, ...]
    key_configured: bool

    @property
    def exposed_to_network(self) -> bool:
        """Whether the API can be reached from outside the machine."""

        return not self.loopback

    @property
    def warnings(self) -> list[str]:
        """The problems this configuration has."""

        out: list[str] = []
        if self.exposed_to_network and not self.auth_required:
            out.append(
                "the API is bound to a non-loopback interface and does not require "
                "authentication: every profile, seed and proxy credential on this "
                "machine is reachable from the network"
            )
        if self.exposed_to_network and not self.key_configured:
            out.append("the API is bound to a non-loopback interface without an API key")
        if "*" in self.cors_origins:
            out.append(
                "the API allows every browser origin: a web page in the operator's "
                "browser can call the control plane"
            )
        return out

    def as_dict(self) -> dict[str, object]:
        """A JSON-friendly representation."""

        return {
            "host": self.host,
            "port": self.port,
            "auth_required": self.auth_required,
            "loopback": self.loopback,
            "exposed_to_network": self.exposed_to_network,
            "cors_origins": list(self.cors_origins),
            "key_configured": self.key_configured,
            "warnings": self.warnings,
        }


def exposure_report(cors_origins: tuple[str, ...] | list[str] = ()) -> ExposureReport:
    """Build the report describing the current exposure."""

    return ExposureReport(
        host=api_host(),
        port=api_port(),
        auth_required=require_auth(),
        loopback=is_loopback(),
        cors_origins=tuple(cors_origins),
        key_configured=bool(api_key()),
    )


def generate_key() -> str:
    """Generate a fresh API key.

    The key is 32 bytes of entropy, URL-safe so that it can be pasted into
    an ``Authorization`` header or a query string without escaping.
    """

    import secrets

    return secrets.token_urlsafe(32)


__all__ = [
    "API_HOST_ENV_VAR",
    "API_KEY_ENV_VAR",
    "API_KEY_HEADER",
    "API_PORT_ENV_VAR",
    "API_REQUIRE_AUTH_ENV_VAR",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ExposureReport",
    "api_host",
    "api_key",
    "api_port",
    "exposure_report",
    "generate_key",
    "is_loopback",
    "require_auth",
    "verify_key",
]
