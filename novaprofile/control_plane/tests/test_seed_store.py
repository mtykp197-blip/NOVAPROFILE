"""Tests for the compact, seed-first store."""

from __future__ import annotations

import pytest

from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.registry.seed_store import SeedStore

PRESETS = [p.preset_id for p in list_presets()]


@pytest.fixture()
def store(tmp_path) -> SeedStore:
    with SeedStore(tmp_path / "fleet.db") as store:
        yield store


def test_a_stored_profile_regenerates_identically(store):
    """The seed tuple is enough to reproduce every fingerprint axis."""

    generator = ProfileGenerator()
    config = generator.generate("pixel-8-pro-android14", master_seed="a" * 64)
    store.save(config)

    restored = store.get_config(config.identity.id)
    assert restored is not None
    assert restored.android_build.serial == config.android_build.serial
    assert restored.android_build.fingerprint == config.android_build.fingerprint
    assert restored.locale.primary == config.locale.primary
    assert restored.screen.width == config.screen.width
    assert restored.webgl.image_noise.value == config.webgl.image_noise.value
    assert restored.identity.id == config.identity.id


def test_a_stored_profile_keeps_its_operator_fields(store):
    """The name, group and notes belong to the operator, not the seed."""

    generator = ProfileGenerator()
    config = generator.generate("pixel-8-pro-android14", master_seed="b" * 64)
    config = config.model_copy(
        update={
            "identity": config.identity.model_copy(
                update={"name": "Batch 7 / slot 3", "notes": "checked by QA"}
            )
        }
    )
    store.save(config)

    restored = store.get_config(config.identity.id)
    assert restored is not None
    assert restored.identity.name == "Batch 7 / slot 3"
    assert restored.identity.notes == "checked by QA"


def test_a_materialised_profile_round_trips_exactly(store):
    """A materialised row is read back byte for byte."""

    generator = ProfileGenerator()
    config = generator.generate("galaxy-s23-android14", master_seed="c" * 64)
    store.save(config, materialise=True)

    restored = store.get_config(config.identity.id)
    assert restored is not None
    assert restored.model_dump_json() == config.model_dump_json()


def test_the_row_is_far_smaller_than_the_configuration(store):
    """The whole point of the seed store: a small row."""

    generator = ProfileGenerator()
    config = generator.generate("pixel-8-pro-android14", master_seed="d" * 64)
    store.save(config)

    row = store.get_row(config.identity.id)
    assert row is not None
    config_bytes = len(config.model_dump_json().encode())
    # The stored tuple must be an order of magnitude smaller than the JSON.
    assert row.size_bytes * 10 < config_bytes


def test_duplicate_active_signatures_are_refused(store):
    """The database, not the application, enforces the uniqueness."""

    import sqlite3

    store.record_signature("canvas", "abc", "p1")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_signature("canvas", "abc", "p2")


def test_a_fleet_of_rows_round_trips(store):
    """Many profiles are stored and read back."""

    generator = ProfileGenerator()
    configs = [
        generator.generate(PRESETS[i % len(PRESETS)], master_seed=f"{i:064x}") for i in range(50)
    ]
    for config in configs:
        store.save(config)

    assert store.count() == 50
    for config in configs:
        restored = store.get_config(config.identity.id)
        assert restored is not None
        assert restored.android_build.serial == config.android_build.serial
