"""Tests for the control-plane API's exposure control.

The control plane holds proxy credentials, seeds and the configuration of
every profile.  These tests check that the API refuses a request without a
credential, that the comparison does not leak the key, and that the
configuration reports how exposed it is.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from novaprofile.api import auth
from novaprofile.api.app import create_app


@pytest.fixture(autouse=True)
def _clean_environment() -> Iterator[None]:
    """Every test starts from a known environment."""

    saved = {
        key: os.environ.get(key)
        for key in (
            auth.API_KEY_ENV_VAR,
            auth.API_HOST_ENV_VAR,
            auth.API_PORT_ENV_VAR,
            auth.API_REQUIRE_AUTH_ENV_VAR,
            "NOVA_CORS_ORIGINS",
        )
    }
    for key in saved:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_without_a_key_no_credential_is_required():
    """A control plane without a key accepts requests."""

    assert auth.require_auth() is False
    assert auth.verify_key(None) is True


def test_with_a_key_a_credential_is_required():
    """A configured key turns the check on."""

    os.environ[auth.API_KEY_ENV_VAR] = "s3cret"
    assert auth.require_auth() is True
    assert auth.verify_key("s3cret") is True
    assert auth.verify_key("wrong") is False
    assert auth.verify_key(None) is False


def test_the_authentication_can_be_switched_off_explicitly():
    """The operator can turn the check off, for a loopback-only server."""

    os.environ[auth.API_KEY_ENV_VAR] = "s3cret"
    os.environ[auth.API_REQUIRE_AUTH_ENV_VAR] = "0"
    assert auth.require_auth() is False


def test_the_server_binds_to_the_loopback_by_default():
    """The control plane must not be reachable from the network by accident."""

    assert auth.api_host() == "127.0.0.1"
    assert auth.is_loopback() is True


def test_the_exposure_report_names_the_problems():
    """A network-bound server without a key is reported as exposed."""

    os.environ[auth.API_HOST_ENV_VAR] = "0.0.0.0"
    report = auth.exposure_report(("*",))

    assert report.exposed_to_network is True
    warnings = " ".join(report.warnings)
    assert "non-loopback" in warnings
    assert "every browser origin" in warnings


def test_a_request_without_a_credential_is_refused():
    """The API returns 401 rather than the profile list."""

    os.environ[auth.API_KEY_ENV_VAR] = "s3cret"
    client = TestClient(create_app())

    response = client.get("/api/profiles")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_a_request_with_the_credential_is_served():
    """The credential opens the API."""

    os.environ[auth.API_KEY_ENV_VAR] = "s3cret"
    client = TestClient(create_app())

    response = client.get("/api/profiles", headers={auth.API_KEY_HEADER: "s3cret"})
    assert response.status_code == 200


def test_a_bearer_token_is_accepted():
    """A bearer token is an alternative way to present the key."""

    os.environ[auth.API_KEY_ENV_VAR] = "s3cret"
    client = TestClient(create_app())

    response = client.get("/api/profiles", headers={"Authorization": "Bearer s3cret"})
    assert response.status_code == 200


def test_the_health_endpoint_stays_public():
    """Liveness must not require a credential, or a monitor cannot work."""

    os.environ[auth.API_KEY_ENV_VAR] = "s3cret"
    client = TestClient(create_app())

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_health_endpoint_reports_the_exposure():
    """The operator can see how exposed the API is."""

    client = TestClient(create_app())

    payload = client.get("/api/health").json()
    assert "exposure" in payload
    assert payload["exposure"]["loopback"] is True
    assert payload["exposure"]["exposed_to_network"] is False


def test_a_generated_key_is_long_and_random():
    """A generated key carries enough entropy to matter."""

    first = auth.generate_key()
    second = auth.generate_key()
    assert first != second
    assert len(first) >= 32
