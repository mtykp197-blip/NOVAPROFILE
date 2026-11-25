"""A profile that an older build wrote must not break the fleet.

The schema of a profile tightens over time: a panel that used to be
accepted is given a bound, a reading is renamed, a model leaves the
catalogue.  A profile that was written before the tightening stays in
the store, and it is no longer readable.

The rule this file pins down is the one an operator needs.  A profile
that cannot be read is a problem with *that profile*, and the fleet
around it has to keep working: the list has to come back, the count
has to be right, and the operator has to be told what was left out.
The alternative -- one unreadable row takes the whole listing down --
turns a cosmetic problem into an outage, and it does it at exactly
the moment the operator opens the application.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novaprofile.api import auth, deps  # noqa: E402
from novaprofile.api.app import create_app  # noqa: E402
from novaprofile.registry.store import ProfileStore  # noqa: E402

#: A payload that is valid JSON, valid for the build that wrote it,
#: and outside the bounds the schema has now.
_STALE_PAYLOAD = json.dumps(
    {
        "identity": {"id": "unreadable-1", "name": "Old Handset"},
        "seed_config": {
            "master_seed": "0" * 64,
            "preset_id": "pixel-6-pro-android13",
        },
        "screen": {"width": 157, "height": 320},
    }
)


def _write_a_stale_row(store: ProfileStore) -> None:
    with store._lock:  # noqa: SLF001 - the fixture lives in the package
        store.conn.execute(
            """
            INSERT INTO profiles (
                profile_id, name, group_id, folder_id, tags, notes,
                status, created_at, updated_at, last_started_at,
                last_closed_at, total_runtime_ms, launch_count,
                master_seed, seed_epoch, nudge_step, hkdf_version,
                preset_id, core_version, config_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "unreadable-1",
                "Old Handset",
                None,
                None,
                "[]",
                "",
                "created",
                "2024-01-01T00:00:00.000000Z",
                "2024-01-01T00:00:00.000000Z",
                None,
                None,
                0,
                0,
                "0" * 64,
                0,
                0,
                "hkdf-sha256-v1",
                "pixel-6-pro-android13",
                "1.0.0",
                _STALE_PAYLOAD,
            ),
        )


@pytest.fixture()
def store(tmp_path: Path) -> ProfileStore:
    """A store with its schema in place."""

    created = ProfileStore(str(tmp_path / "state.db"))
    created.init_db()
    return created


def _client(store: ProfileStore) -> tuple[TestClient, dict[str, str]]:
    app = create_app()
    key = auth.generate_key()
    os.environ[auth.API_KEY_ENV_VAR] = key
    client = TestClient(app)
    client.app.dependency_overrides[deps.get_store] = lambda: store
    return client, {"x-nova-key": key}


def test_the_listing_survives_a_profile_it_cannot_read(store: ProfileStore) -> None:
    _write_a_stale_row(store)
    client, headers = _client(store)

    response = client.get("/api/profiles?limit=50", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unreadable"] == 1, body
    assert body["total"] == 0, body


def test_a_good_profile_is_still_listed_next_to_a_bad_one(store: ProfileStore) -> None:
    client, headers = _client(store)

    created = client.post(
        "/api/batch/create",
        headers=headers,
        json={"count": 2, "name_prefix": "unit", "preset_id": "pixel-8-pro-android14"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["created_count"] == 2

    _write_a_stale_row(store)

    response = client.get("/api/profiles?limit=50", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2, body
    assert body["unreadable"] == 1, body
    assert len(body["items"]) == 2, body


def test_fetching_the_unreadable_profile_says_so_instead_of_failing(
    store: ProfileStore,
) -> None:
    _write_a_stale_row(store)
    client, headers = _client(store)

    response = client.get("/api/profiles/unreadable-1", headers=headers)

    # A conflict, not a server fault: the row is there, this build
    # just cannot read it.
    assert response.status_code == 409, response.text
    assert "cannot read" in response.text


def test_the_signatures_of_the_good_profiles_are_intact(store: ProfileStore) -> None:
    # The unreadable row is skipped, not silently consumed.  The
    # profiles that *were* read keep their signatures, because the
    # guard depends on them: a signature that was quietly dropped
    # would let a later allocation hand out the same fingerprint
    # again.
    from novaprofile.core.collision import CollisionGuard, InMemorySignatureRegistry

    guard = CollisionGuard(registry=InMemorySignatureRegistry())
    client, headers = _client(store)
    client.app.dependency_overrides[deps.get_guard] = lambda: guard

    client.post(
        "/api/batch/create",
        headers=headers,
        json={"count": 3, "name_prefix": "unit", "preset_id": "pixel-8-pro-android14"},
    )

    listing = client.get("/api/profiles?limit=50", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 3

    # The listing did not disturb the registry the guard keeps.
    rows = store.conn.execute("SELECT COUNT(*) FROM profiles").fetchone()
    assert rows and rows[0] == 3, "the listing did not lose or duplicate a profile"
