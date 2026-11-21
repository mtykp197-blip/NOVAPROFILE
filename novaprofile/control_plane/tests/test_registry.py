"""Tests for :mod:`novaprofile.registry.store`.

The store is exercised against the real
:class:`novaprofile.schema.profile.ProfileConfig` produced by
:class:`novaprofile.core.generator.ProfileGenerator`, so the persistence layer
is verified against the shipped schema rather than a stand-in.

Everything is deterministic and isolated: the database lives in ``tmp_path``
and no network access is performed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from novaprofile.core.collision import SignatureRecord
from novaprofile.core.generator import ProfileGenerator
from novaprofile.registry.models import PaginatedResult, ProfileFilter
from novaprofile.registry.store import (
    DuplicateProfileError,
    DuplicateSignatureError,
    ProfileNotFoundError,
    ProfileStore,
)
from novaprofile.schema.enums import SignatureKind, SignatureState
from novaprofile.schema.profile import ProfileConfig

PRESET_ID = "pixel-8-pro-android14"


@pytest.fixture(scope="module")
def generator() -> ProfileGenerator:
    """Return a profile generator bound to a known device preset."""
    return ProfileGenerator()


@pytest.fixture()
def make_config(generator: ProfileGenerator):
    """Return a factory that builds real ``ProfileConfig`` objects."""
    counter = {"n": 0}

    def _make(
        name: str = "profile",
        *,
        group_id: str | None = None,
        folder_id: str | None = None,
        tags: list[str] | None = None,
        notes: str = "",
        status: str = "created",
        master_seed: str | None = None,
        profile_id: str | None = None,
    ) -> ProfileConfig:
        counter["n"] += 1
        config = generator.generate(
            PRESET_ID,
            name=name,
            master_seed=master_seed,
        )
        # ``generate`` does not expose group/folder/tags/notes, so the
        # identity is rebuilt with the requested metadata.
        identity = config.identity.model_copy(
            update={
                "name": name,
                "group_id": group_id,
                "folder_id": folder_id,
                "tags": list(tags or []),
                "notes": notes,
                "status": status,
            }
        )
        return config.model_copy(update={"identity": identity})

    return _make


@pytest.fixture()
def store(tmp_path: Path) -> ProfileStore:
    """Return an initialised store backed by a temporary database."""
    instance = ProfileStore(tmp_path / "state.db")
    instance.init_db()
    yield instance
    instance.close()


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def test_init_db_creates_tables(store: ProfileStore) -> None:
    rows = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert {"profiles", "signatures", "groups", "folders"} <= names


def test_wal_and_foreign_keys_enabled(store: ProfileStore) -> None:
    mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_store_reopens_existing_db(tmp_path: Path, make_config) -> None:
    path = tmp_path / "state.db"
    first = ProfileStore(path)
    first.init_db()
    first.create_profile(make_config("Alpha"))
    first.close()

    second = ProfileStore(path)
    second.init_db()
    assert second.get_profile(first_profile_id(second)) is not None
    second.close()


def first_profile_id(store: ProfileStore) -> str:
    """Return the id of the single stored profile."""
    found = store.list_profiles()
    assert len(found) == 1
    return found[0].identity.id


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


def test_create_and_get_profile(store: ProfileStore, make_config) -> None:
    config = make_config("Alpha", group_id="g1", tags=["eu", "mobile"], notes="hello")
    store.create_profile(config)

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.id == config.identity.id
    assert loaded.identity.name == "Alpha"
    assert loaded.identity.group_id == "g1"
    assert list(loaded.identity.tags) == ["eu", "mobile"]
    assert loaded.identity.notes == "hello"
    assert loaded.seed_config.master_seed == config.seed_config.master_seed


def test_round_trip_preserves_every_axis(store: ProfileStore, make_config) -> None:
    """A stored profile must serialise back to an identical model."""
    config = make_config("Alpha")
    store.create_profile(config)

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.model_dump_json() == config.model_dump_json()


def test_get_missing_profile_returns_none(store: ProfileStore) -> None:
    assert store.get_profile("nope") is None


def test_duplicate_profile_id_raises(store: ProfileStore, make_config) -> None:
    config = make_config("First")
    store.create_profile(config)
    with pytest.raises(DuplicateProfileError):
        store.create_profile(config)


def test_update_profile(store: ProfileStore, make_config) -> None:
    config = make_config("Before")
    store.create_profile(config)

    updated = config.model_copy(
        update={
            "identity": config.identity.model_copy(update={"name": "After", "notes": "changed"})
        }
    )
    store.update_profile(updated)

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.name == "After"
    assert loaded.identity.notes == "changed"


def test_update_missing_profile_raises(store: ProfileStore, make_config) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.update_profile(make_config("missing"))


def test_delete_profile(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.delete_profile(config.identity.id)
    assert store.get_profile(config.identity.id) is None


def test_delete_missing_profile_raises(store: ProfileStore) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.delete_profile("missing")


# --------------------------------------------------------------------------
# Listing / filtering / search
# --------------------------------------------------------------------------


def test_list_profiles_returns_all(store: ProfileStore, make_config) -> None:
    for index in range(3):
        store.create_profile(make_config(f"P{index}"))
    assert len(store.list_profiles()) == 3


def test_list_profiles_filters_by_group(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", group_id="g1"))
    store.create_profile(make_config("b", group_id="g2"))
    store.create_profile(make_config("c", group_id="g1"))

    found = store.list_profiles(group_id="g1")
    assert len(found) == 2
    assert {p.identity.group_id for p in found} == {"g1"}


def test_list_profiles_filters_by_tag(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", tags=["eu", "mobile"]))
    store.create_profile(make_config("b", tags=["us"]))

    found = store.list_profiles(tag="eu")
    assert len(found) == 1
    assert found[0].identity.name == "a"


def test_list_profiles_filters_by_status(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", status="ready"))
    store.create_profile(make_config("b", status="running"))

    found = store.list_profiles(status="running")
    assert len(found) == 1
    assert found[0].identity.name == "b"


def test_list_profiles_search_matches_name(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("Alpha Browser"))
    store.create_profile(make_config("Beta Browser"))
    found = store.list_profiles(search="alpha")
    assert len(found) == 1
    assert found[0].identity.name == "Alpha Browser"


def test_list_profiles_search_matches_notes(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", notes="uses residential proxy"))
    store.create_profile(make_config("b", notes="clean"))
    found = store.list_profiles(search="residential")
    assert len(found) == 1
    assert found[0].identity.name == "a"


def test_list_profiles_search_matches_tags(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", tags=["datacenter"]))
    store.create_profile(make_config("b", tags=["residential"]))
    found = store.list_profiles(search="datacenter")
    assert len(found) == 1
    assert found[0].identity.name == "a"


def test_list_profiles_pagination(store: ProfileStore, make_config) -> None:
    for index in range(5):
        store.create_profile(make_config(f"P{index}"))
    assert len(store.list_profiles(limit=2)) == 2
    assert len(store.list_profiles(limit=2, offset=2)) == 2
    assert len(store.list_profiles(limit=2, offset=4)) == 1


def test_count_profiles(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", group_id="g1"))
    store.create_profile(make_config("b", group_id="g2"))
    assert store.count_profiles() == 2
    assert store.count_profiles(group_id="g1") == 1


def test_list_profiles_filtered_helper(store: ProfileStore, make_config) -> None:
    store.create_profile(make_config("a", group_id="g1", tags=["eu"]))
    store.create_profile(make_config("b", group_id="g2"))

    filt = ProfileFilter(group_id="g1")
    found = store.list_profiles_filtered(filt)
    assert len(found) == 1
    assert found[0].identity.name == "a"


def test_paginated_result_helpers() -> None:
    result = PaginatedResult(items=[1, 2, 3], total=10, limit=3, offset=0)
    assert result.has_more is True
    assert result.page == 0

    last = PaginatedResult(items=[10], total=10, limit=3, offset=9)
    assert last.has_more is False


# --------------------------------------------------------------------------
# Lifecycle counters
# --------------------------------------------------------------------------


def test_set_status(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.set_status(config.identity.id, "running")
    assert store.get_status(config.identity.id) == "running"

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.status == "running"


def test_set_status_missing_profile_raises(store: ProfileStore) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.set_status("missing", "running")


def test_status_transitions(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    for status in ("ready", "starting", "running", "stopping", "ready"):
        store.set_status(config.identity.id, status)
        assert store.get_status(config.identity.id) == status


def test_mark_launched_increments_count(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.mark_launched(config.identity.id)
    store.mark_launched(config.identity.id)

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.launch_count == 2
    assert loaded.identity.last_started_at is not None


def test_mark_closed_sets_timestamp(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.mark_closed(config.identity.id)
    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.last_closed_at is not None


def test_increment_runtime_accumulates(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.increment_runtime(config.identity.id, 1_000)
    store.increment_runtime(config.identity.id, 2_500)

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.total_runtime_ms == 3_500


def test_increment_runtime_rejects_negative(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    with pytest.raises(ValueError):
        store.increment_runtime(config.identity.id, -1)


def test_increment_runtime_missing_profile_raises(store: ProfileStore) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.increment_runtime("missing", 10)


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------


def test_record_and_find_signature(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    record = SignatureRecord(
        kind=SignatureKind.CANVAS,
        seed_epoch=0,
        nudge_step=0,
        signature="canvas-sig-1",
        profile_id=config.identity.id,
    )
    store.record_signature(record)

    found = store.find_signature(SignatureKind.CANVAS, "canvas-sig-1")
    assert found is not None
    assert found.signature == "canvas-sig-1"
    assert found.profile_id == config.identity.id
    assert found.state == SignatureState.ACTIVE
    assert found.kind == SignatureKind.CANVAS


def test_find_signature_missing_returns_none(store: ProfileStore) -> None:
    assert store.find_signature(SignatureKind.CANVAS, "absent") is None


def test_count_signatures(store: ProfileStore, make_config) -> None:
    first = make_config()
    second = make_config()
    store.create_profile(first)
    store.create_profile(second)
    store.record_signature(SignatureRecord(SignatureKind.CANVAS, "a", first.identity.id, 0, 0))
    store.record_signature(SignatureRecord(SignatureKind.CANVAS, "b", second.identity.id, 0, 0))
    store.record_signature(SignatureRecord(SignatureKind.AUDIO, "c", first.identity.id, 0, 0))

    assert store.count_signatures(SignatureKind.CANVAS) == 2
    assert store.count_signatures(SignatureKind.AUDIO) == 1
    assert store.count_signatures(SignatureKind.FULL) == 0


def test_duplicate_signature_raises(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.record_signature(SignatureRecord(SignatureKind.CANVAS, "dup", config.identity.id, 0, 0))
    with pytest.raises(DuplicateSignatureError):
        store.record_signature(
            SignatureRecord(SignatureKind.CANVAS, "dup", config.identity.id, 0, 0)
        )


def test_duplicate_signature_across_kinds_allowed(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.record_signature(SignatureRecord(SignatureKind.CANVAS, "same", config.identity.id, 0, 0))
    store.record_signature(SignatureRecord(SignatureKind.AUDIO, "same", config.identity.id, 0, 0))
    assert store.count_signatures(SignatureKind.CANVAS) == 1
    assert store.count_signatures(SignatureKind.AUDIO) == 1


def test_archived_signature_does_not_block(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.record_signature(
        SignatureRecord(SignatureKind.CANVAS, "reused", config.identity.id, 0, 0)
    )
    store.archive_signature(SignatureKind.CANVAS, "reused")

    assert store.find_signature(SignatureKind.CANVAS, "reused") is None
    assert store.count_signatures(SignatureKind.CANVAS) == 0

    # The uniqueness index only covers ACTIVE rows, so re-recording works.
    store.record_signature(
        SignatureRecord(SignatureKind.CANVAS, "reused", config.identity.id, 0, 0)
    )
    assert store.count_signatures(SignatureKind.CANVAS) == 1


def test_signature_for_unknown_profile_raises(store: ProfileStore) -> None:
    """A missing profile is a foreign-key violation, not a duplicate."""
    with pytest.raises(ProfileNotFoundError):
        store.record_signature(SignatureRecord(SignatureKind.CANVAS, "orphan", "missing", 0, 0))


def test_delete_profile_cascades_signatures(store: ProfileStore, make_config) -> None:
    config = make_config()
    store.create_profile(config)
    store.record_signature(
        SignatureRecord(SignatureKind.CANVAS, "cascaded", config.identity.id, 0, 0)
    )
    store.delete_profile(config.identity.id)
    assert store.count_signatures(SignatureKind.CANVAS) == 0


def test_concurrent_duplicate_signatures_are_atomic(store: ProfileStore, make_config) -> None:
    """Only one of many concurrent inserts of the same signature wins."""
    config = make_config()
    store.create_profile(config)
    rejected: list[int] = []
    accepted: list[int] = []

    def worker() -> None:
        try:
            store.record_signature(
                SignatureRecord(SignatureKind.CANVAS, "race", config.identity.id, 0, 0)
            )
        except DuplicateSignatureError:
            rejected.append(1)
        else:
            accepted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert len(rejected) == 7
    assert store.count_signatures(SignatureKind.CANVAS) == 1


def test_store_satisfies_signature_registry_protocol(store: ProfileStore, make_config) -> None:
    """The store must expose the collision guard's registry interface."""
    from novaprofile.core.collision import SignatureRegistry

    for method in ("find_signature", "record_signature", "count_signatures"):
        assert hasattr(SignatureRegistry, method), method

    # The guard's protocol methods must exist on the store with compatible
    # semantics.
    config = make_config()
    store.create_profile(config)
    record = SignatureRecord(SignatureKind.CANVAS, "proto-sig", config.identity.id, 0, 0)

    # ``find_signature``/``record_signature``/``count_signatures`` are the
    # protocol names; the store exposes them directly, so verify the
    # behaviour those protocol calls rely upon.
    assert store.find_signature(SignatureKind.CANVAS, "proto-sig") is None
    store.record_signature(record)
    assert store.find_signature(SignatureKind.CANVAS, "proto-sig") is not None
    assert store.count_signatures(SignatureKind.CANVAS) == 1


# --------------------------------------------------------------------------
# Cloning
# --------------------------------------------------------------------------


def test_clone_profile_uses_new_id_and_seed(store: ProfileStore, make_config) -> None:
    source = make_config("Original")
    store.create_profile(source)
    clone_id = "11111111-1111-4111-8111-111111111111"
    clone = store.clone_profile(source.identity.id, clone_id, "b" * 64)

    assert clone.identity.id == clone_id
    assert clone.seed_config.master_seed == "b" * 64
    assert store.get_profile(clone_id) is not None
    assert store.get_profile(source.identity.id) is not None


def test_clone_profile_resets_counters(store: ProfileStore, make_config) -> None:
    source = make_config()
    store.create_profile(source)
    store.mark_launched(source.identity.id)
    store.increment_runtime(source.identity.id, 5_000)

    clone = store.clone_profile(
        source.identity.id, "22222222-2222-4222-8222-222222222222", "b" * 64
    )
    assert clone.identity.launch_count == 0
    assert clone.identity.total_runtime_ms == 0
    assert clone.identity.last_started_at is None
    assert clone.identity.last_closed_at is None


def test_clone_profile_has_clean_signature_footprint(store: ProfileStore, make_config) -> None:
    source = make_config()
    store.create_profile(source)
    store.record_signature(SignatureRecord(SignatureKind.CANVAS, "sig", source.identity.id, 0, 0))
    store.clone_profile(source.identity.id, "33333333-3333-4333-8333-333333333333", "b" * 64)
    assert store.count_signatures(SignatureKind.CANVAS) == 1


def test_clone_missing_source_raises(store: ProfileStore) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.clone_profile("missing", "44444444-4444-4444-8444-444444444444", "b" * 64)


# --------------------------------------------------------------------------
# Groups and folders
# --------------------------------------------------------------------------


def test_group_lifecycle(store: ProfileStore) -> None:
    store.create_group("g1", "Europe")
    assert store.list_groups() == [("g1", "Europe")]
    store.delete_group("g1")
    assert store.list_groups() == []


def test_folder_lifecycle(store: ProfileStore) -> None:
    store.create_folder("f1", "Batch A")
    assert store.list_folders() == [("f1", "Batch A")]
    store.delete_folder("f1")
    assert store.list_folders() == []


def test_deleting_group_detaches_profiles(store: ProfileStore, make_config) -> None:
    store.create_group("g1", "Europe")
    config = make_config(group_id="g1")
    store.create_profile(config)
    store.delete_group("g1")

    loaded = store.get_profile(config.identity.id)
    assert loaded is not None
    assert loaded.identity.group_id is None
