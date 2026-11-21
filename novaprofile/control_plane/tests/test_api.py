"""Tests for the NOVA control-plane HTTP API.

The tests never touch the real engine modules: a lightweight in-memory store
is injected through FastAPI's dependency-override mechanism so the suite is
deterministic and runs while the engine is still being assembled.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novaprofile.api import deps
from novaprofile.api.app import create_app

# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class _Identity:
    """Minimal stand-in for :class:`ProfileIdentity`."""

    def __init__(self, **fields: Any) -> None:
        self.id = fields.get("id", "")
        self.name = fields.get("name", "")
        self.group_id = fields.get("group_id")
        self.folder_id = fields.get("folder_id")
        self.tags = list(fields.get("tags", []))
        self.notes = fields.get("notes", "")
        self.status = fields.get("status", "created")
        self.created_at = fields.get("created_at", datetime.now(UTC))
        self.updated_at = fields.get("updated_at", datetime.now(UTC))
        self.last_started_at = fields.get("last_started_at")
        self.last_closed_at = fields.get("last_closed_at")
        self.total_runtime_ms = fields.get("total_runtime_ms", 0)
        self.launch_count = fields.get("launch_count", 0)

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "group_id": self.group_id,
            "folder_id": self.folder_id,
            "tags": list(self.tags),
            "notes": self.notes,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_started_at": self.last_started_at,
            "last_closed_at": self.last_closed_at,
            "total_runtime_ms": self.total_runtime_ms,
            "launch_count": self.launch_count,
        }
        if mode == "json":
            data = {
                key: (value.isoformat() if isinstance(value, datetime) else value)
                for key, value in data.items()
            }
        return data

    def model_copy(self, update: dict[str, Any] | None = None) -> _Identity:
        data = self.model_dump()
        data.update(update or {})
        return _Identity(**data)


class _Seed:
    """Minimal stand-in for :class:`SeedConfig`."""

    def __init__(
        self,
        master_seed: str = "0" * 64,
        preset_id: str = "test-preset",
        **extra: Any,
    ) -> None:
        self.master_seed = master_seed
        self.preset_id = preset_id
        self.seed_epoch = extra.get("seed_epoch", 0)
        self.nudge_step = extra.get("nudge_step", 0)
        self.hkdf_version = extra.get("hkdf_version", "hkdf-sha256-v1")
        self.core_version = extra.get("core_version", "1.0.0")

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "master_seed": self.master_seed,
            "preset_id": self.preset_id,
            "seed_epoch": self.seed_epoch,
            "nudge_step": self.nudge_step,
            "hkdf_version": self.hkdf_version,
            "core_version": self.core_version,
        }

    def model_copy(self, update: dict[str, Any] | None = None) -> _Seed:
        data = self.model_dump()
        data.update(update or {})
        return _Seed(**data)


class _Config:
    """Minimal stand-in for :class:`ProfileConfig`."""

    def __init__(self, profile_id: str, name: str, **extra: Any) -> None:
        self.identity = _Identity(id=profile_id, name=name, **extra)
        self.seed_config = _Seed()
        self.proxy = {"url": None}

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "identity": self.identity.model_dump(mode),
            "seed_config": self.seed_config.model_dump(mode),
            "proxy": self.proxy,
        }

    def model_copy(self, update: dict[str, Any] | None = None) -> _Config:
        clone = _Config(self.identity.id, self.identity.name)
        clone.identity = self.identity.model_copy()
        clone.seed_config = self.seed_config.model_copy()
        clone.proxy = dict(self.proxy)
        for key, value in (update or {}).items():
            if key == "identity" and isinstance(value, _Identity):
                clone.identity = value
            elif key == "seed_config" and isinstance(value, _Seed):
                clone.seed_config = value
            else:
                setattr(clone, key, value)
        return clone


class FakeStore:
    """An in-memory profile store used for the API tests."""

    def __init__(self) -> None:
        self.profiles: dict[str, _Config] = {}

    def init_db(self) -> None:
        return None

    def create_profile(self, config: Any) -> None:
        self.profiles[config.identity.id] = config

    def get_profile(self, profile_id: str) -> _Config | None:
        return self.profiles.get(profile_id)

    def list_profiles(
        self,
        *,
        group_id: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[_Config]:
        result = list(self.profiles.values())
        if group_id is not None:
            result = [item for item in result if item.identity.group_id == group_id]
        if tag is not None:
            result = [item for item in result if tag in item.identity.tags]
        if status is not None:
            result = [item for item in result if item.identity.status == status]
        if search:
            needle = search.lower()
            result = [
                item
                for item in result
                if needle in item.identity.name.lower()
                or needle in item.identity.notes.lower()
                or any(needle in str(entry).lower() for entry in item.identity.tags)
            ]
        return result

    def update_profile(self, config: Any) -> None:
        self.profiles[config.identity.id] = config

    def delete_profile(self, profile_id: str) -> None:
        self.profiles.pop(profile_id, None)


class FakeGuard:
    """A collision guard that simply accepts the candidate."""

    def allocate(self, profile_id: str, candidate: Any) -> Any:
        return candidate


class FakeResolver:
    """A consistency resolver returning a fixed, deterministic report."""

    def check(self, config: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "errors": [],
            "warnings": [{"severity": "warning", "axis": "proxy", "message": "no proxy"}],
        }


class FakeProbe:
    """A runtime probe returning a deterministic report."""

    def collect(self, config: Any) -> dict[str, Any]:
        return {
            "profile_id": config.identity.id,
            "ok": True,
            "results": [{"axis": "canvas", "ok": True, "value": "1234.567"}],
            "distinct_ratio": 1.0,
            "collision_rate": 0.0,
            "persistence_ok": True,
            "consistency_pass": True,
        }


class FakeLauncher:
    """A launcher that records the calls it receives."""

    def __init__(self) -> None:
        self.launched: list[str] = []
        self.stopped: list[str] = []

    def launch(self, config: Any, headful: bool = False, url: str | None = None) -> dict[str, Any]:
        self.launched.append(config.identity.id)
        return {"pid": 4242, "headful": headful}

    def stop(self, profile_id: str) -> dict[str, Any]:
        self.stopped.append(profile_id)
        return {"pid": 4242}

    def build_launch_config(self, config: Any) -> dict[str, Any]:
        return {"args": ["--user-data-dir", f"profiles/{config.identity.id}"]}


class FakeGenerator:
    """A profile generator producing the lightweight test configs."""

    def __init__(self) -> None:
        self.counter = 0

    def __call__(self, preset_id: str = "", master_seed: str | None = None, **_: Any) -> _Config:
        self.counter += 1
        profile_id = f"profile-{self.counter:04d}"
        config = _Config(profile_id, f"profile-{self.counter:04d}")
        config.seed_config = _Seed(master_seed or "0" * 64, preset_id or "test-preset")
        return config


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point every path helper at a temporary directory."""
    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture()
def client(store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a ``TestClient`` wired to the in-memory doubles."""
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    app.dependency_overrides[deps.get_guard] = lambda: FakeGuard()
    app.dependency_overrides[deps.get_resolver] = lambda: FakeResolver()
    app.dependency_overrides[deps.get_probe] = lambda: FakeProbe()
    app.dependency_overrides[deps.get_launcher] = lambda: FakeLauncher()

    generator = FakeGenerator()

    def _build(*args: Any, **kwargs: Any) -> Any:
        preset_id = kwargs.get("preset_id", args[0] if args else "")
        master_seed = kwargs.get("master_seed")
        name = kwargs.get("name")
        config = generator(preset_id=preset_id, master_seed=master_seed)
        if name:
            config.identity = _Identity(id=config.identity.id, name=name, tags=config.identity.tags)
        return config

    monkeypatch.setattr("novaprofile.api.routes_profiles._build_config", _build)
    monkeypatch.setattr("novaprofile.api.routes_batch._build_config", _build)
    return TestClient(app)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_health_endpoint(client: TestClient) -> None:
    """The health endpoint reports the service status and version."""
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "version" in payload


def test_json_schema_endpoint(client: TestClient) -> None:
    """The JSON Schema endpoint responds with a schema object or a 503."""
    response = client.get("/api/schema/json")
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        payload = response.json()
        assert payload["title"] == "ProfileConfig"
        assert "properties" in payload


def test_profile_crud_roundtrip(client: TestClient) -> None:
    """Create, read, update and delete a profile."""
    created = client.post(
        "/api/profiles",
        json={"preset_id": "pixel-7", "name": "alpha", "tags": ["eu", "test"]},
    )
    assert created.status_code == 201
    profile = created.json()
    profile_id = profile["identity"]["id"]
    assert profile["identity"]["name"] == "alpha"
    assert profile["identity"]["tags"] == ["eu", "test"]

    fetched = client.get(f"/api/profiles/{profile_id}")
    assert fetched.status_code == 200
    assert fetched.json()["identity"]["id"] == profile_id

    listing = client.get("/api/profiles")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    filtered = client.get("/api/profiles", params={"tag": "eu"})
    assert filtered.json()["total"] == 1

    missing = client.get("/api/profiles", params={"tag": "nope"})
    assert missing.json()["total"] == 0

    updated = client.patch(
        f"/api/profiles/{profile_id}",
        json={"name": "beta", "notes": "changed"},
    )
    assert updated.status_code == 200
    assert updated.json()["identity"]["name"] == "beta"
    assert updated.json()["identity"]["notes"] == "changed"

    deleted = client.delete(f"/api/profiles/{profile_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/profiles/{profile_id}").status_code == 404


def test_unknown_profile_returns_404(client: TestClient) -> None:
    """An unknown profile id maps onto HTTP 404."""
    response = client.get("/api/profiles/does-not-exist")
    assert response.status_code == 404


def test_validate_endpoint_returns_report(client: TestClient) -> None:
    """The validation endpoint returns the consistency report."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    response = client.post(f"/api/profiles/{profile_id}/validate")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile_id"] == profile_id
    assert payload["report"]["ok"] is True
    assert payload["report"]["errors"] == []


def test_validate_unknown_profile_returns_404(client: TestClient) -> None:
    """Validating an unknown profile returns 404."""
    response = client.post("/api/profiles/nope/validate")
    assert response.status_code == 404


def test_probe_endpoint_returns_report(client: TestClient) -> None:
    """The probe endpoint returns the probe report."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    response = client.post(f"/api/profiles/{profile_id}/probe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["distinct_ratio"] == 1.0


def test_launcher_config_endpoint(client: TestClient) -> None:
    """The launcher-config endpoint returns the payload handed to the core."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    response = client.get(f"/api/profiles/{profile_id}/launcher-config")
    assert response.status_code == 200
    assert response.json()["profile_id"] == profile_id
    assert "config" in response.json()


def test_launch_and_stop_endpoints(client: TestClient) -> None:
    """Launch and stop report success for an existing profile."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    launched = client.post(f"/api/profiles/{profile_id}/launch", json={"headful": True})
    assert launched.status_code == 200
    assert launched.json()["launched"] is True

    stopped = client.post(f"/api/profiles/{profile_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["stopped"] is True


def test_clone_endpoint_creates_new_profile(client: TestClient) -> None:
    """Cloning a profile yields a distinct, new profile id."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7", "name": "src"}).json()
    profile_id = created["identity"]["id"]

    response = client.post(f"/api/profiles/{profile_id}/clone", json={"name": "copy"})
    assert response.status_code == 201
    clone = response.json()
    assert clone["identity"]["id"] != profile_id
    assert clone["identity"]["name"] == "copy"


def test_batch_create_returns_n_results(client: TestClient) -> None:
    """The batch-create endpoint reports one result per requested profile."""
    response = client.post(
        "/api/batch/create",
        json={"count": 4, "preset_id": "pixel-7", "name_prefix": "bulk"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested"] == 4
    assert payload["created_count"] == 4
    assert payload["failed_count"] == 0
    assert len(payload["created"]) == 4
    assert {item["name"] for item in payload["created"]} == {
        "bulk-0001",
        "bulk-0002",
        "bulk-0003",
        "bulk-0004",
    }


def test_batch_group_assignment(client: TestClient) -> None:
    """Bulk group assignment updates every listed profile."""
    first = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    second = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()

    response = client.post(
        "/api/batch/group",
        json={
            "profile_ids": [first["identity"]["id"], second["identity"]["id"]],
            "group_id": "team-a",
            "tags": ["eu"],
        },
    )
    assert response.status_code == 200
    assert response.json()["updated_count"] == 2

    listed = client.get("/api/profiles", params={"group_id": "team-a"})
    assert listed.json()["total"] == 2


def test_batch_search_finds_profiles(client: TestClient) -> None:
    """The batch-search endpoint matches on the profile name."""
    client.post("/api/profiles", json={"preset_id": "pixel-7", "name": "findme"})
    response = client.post("/api/batch/search", json={"query": "findme"})
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_cookie_import_and_export_roundtrip(client: TestClient) -> None:
    """Cookies can be imported and exported again."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    cookies = [{"domain": "example.com", "name": "session", "value": "abc"}]
    imported = client.post(
        "/api/batch/cookies/import",
        json={"profile_id": profile_id, "format": "json", "cookies": cookies},
    )
    assert imported.status_code == 200
    assert imported.json()["count"] == 1

    exported = client.post(
        "/api/batch/cookies/export",
        json={"profile_id": profile_id, "format": "json"},
    )
    assert exported.status_code == 200
    payload = json.loads(exported.json()["content"])
    assert payload[0]["name"] == "session"


def test_netscape_cookie_import(client: TestClient) -> None:
    """A Netscape-format cookie payload is parsed correctly."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    payload = (
        "# Netscape HTTP Cookie File\n\n#HttpOnly_.example.com\tTRUE\t/\tTRUE\t0\tsid\tvalue123\n"
    )
    response = client.post(
        "/api/batch/cookies/import",
        json={"profile_id": profile_id, "format": "netscape", "payload": payload},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_proxy_validation_endpoint(client: TestClient) -> None:
    """The proxy-validation endpoint always returns a result per proxy."""
    response = client.post(
        "/api/batch/proxies/validate",
        json={"proxies": ["http://127.0.0.1:8080"]},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_preset_list_endpoint(client: TestClient) -> None:
    """The preset listing endpoint always responds."""
    response = client.get("/api/presets")
    assert response.status_code == 200
    assert "items" in response.json()


def test_probe_run_endpoint(client: TestClient) -> None:
    """The probe run endpoint returns the report for one profile."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    response = client.post("/api/probe/run", json={"profile_id": profile_id})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_probe_suite_endpoint(client: TestClient) -> None:
    """The suite endpoint aggregates the QA metrics."""
    client.post("/api/profiles", json={"preset_id": "pixel-7"})
    client.post("/api/profiles", json={"preset_id": "pixel-7"})

    response = client.post("/api/probe/suite", json={"count": 5})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sample_size"] == 2
    assert payload["metrics"]["distinct_ratio"] == 1.0
    assert payload["metrics"]["persistence"] is True


def test_persistence_endpoint(client: TestClient) -> None:
    """The persistence endpoint reports a stable fingerprint."""
    created = client.post("/api/profiles", json={"preset_id": "pixel-7"}).json()
    profile_id = created["identity"]["id"]

    response = client.post(
        "/api/probe/persistence",
        json={"profile_id": profile_id, "restarts": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["restarts"] == 5
    assert payload["stable"] is True
    assert payload["distinct_fingerprints"] == 1


def test_cors_allows_no_origin_by_default(client: TestClient) -> None:
    """No browser origin is allowed unless the operator asks for one.

    The control plane holds every profile, seed and proxy credential on the
    machine.  A web page in the operator's browser must not be able to read
    them, so the default is to refuse every origin.
    """
    response = client.get("/api/health", headers={"Origin": "http://example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in {key.lower() for key in response.headers}


def test_cors_allows_a_configured_origin(client: TestClient, monkeypatch) -> None:
    """An explicitly allowed origin does receive the CORS header."""

    monkeypatch.setenv("NOVA_CORS_ORIGINS", "http://example.com")
    from novaprofile.api.app import create_app

    response = TestClient(create_app()).get("/api/health", headers={"Origin": "http://example.com"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://example.com"
