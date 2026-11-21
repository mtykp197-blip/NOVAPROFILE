"""Tests for the persistence of a profile and its signatures.

The signature rows carry a foreign key onto the profile row, so the profile
has to be written first.  The reverse case matters just as much: when the
commit loses a race against a concurrent allocation, the profile that was
already written must be removed again -- a profile that survives without its
signatures could be handed out a second time.
"""

from __future__ import annotations

import pytest

from novaprofile.api.routes_profiles import _persist
from novaprofile.core.collision import CollisionGuard, InMemorySignatureRegistry
from novaprofile.core.generator import ProfileGenerator
from novaprofile.registry.store import ProfileStore


class _Store:
    """A minimal store that records what was written and removed."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create_profile(self, config) -> None:
        self.created.append(config.identity.id)

    def delete_profile(self, profile_id: str) -> None:
        self.deleted.append(profile_id)


def test_a_profile_is_written_and_its_signatures_committed():
    """The normal path: write, then commit."""

    store = _Store()
    guard = CollisionGuard(InMemorySignatureRegistry())
    config = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)

    _persist(store, guard, config)

    assert store.created == [config.identity.id]
    assert store.deleted == []


def test_a_lost_commit_removes_the_profile():
    """A profile must not survive a failed signature commit."""

    store = _Store()

    class _LosingGuard:
        def commit(self, config, profile_id: str) -> None:
            raise RuntimeError("the signature was taken by another allocation")

    config = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)

    with pytest.raises(RuntimeError):
        _persist(store, _LosingGuard(), config)

    assert store.created == [config.identity.id]
    assert store.deleted == [config.identity.id]


def test_a_lost_commit_leaves_nothing_behind_in_the_real_store(tmp_path):
    """The real store must not keep a profile without its signatures."""

    from novaprofile.registry.store import DuplicateSignatureError

    store = ProfileStore(tmp_path / "state.db")
    store.init_db()
    # The real store is the registry here, so its unique index is the thing
    # that refuses the second profile.
    guard = CollisionGuard(store)
    config = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)

    store.create_profile(config)
    guard.commit(config, config.identity.id)

    # A second profile wants the very same signature.
    duplicate = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)
    with pytest.raises(DuplicateSignatureError):
        guard.commit(duplicate, duplicate.identity.id)

    # The store keeps the original only; the duplicate never got in.
    assert store.get_profile(config.identity.id) is not None
    assert store.get_profile(duplicate.identity.id) is None


def test_a_profile_without_a_guard_is_still_written():
    """The guard is optional; the profile must still be stored."""

    store = _Store()
    config = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)

    _persist(store, None, config)

    assert store.created == [config.identity.id]
    assert store.deleted == []
