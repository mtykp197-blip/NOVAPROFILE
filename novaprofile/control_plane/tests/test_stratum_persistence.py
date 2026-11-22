"""A profile that is reopened must be the same handset.

The narrow readings are drawn stratified, so the unit's place in its
device family is part of how the profile is built.  That place has
to travel with the seed tuple: a profile that is regenerated from
its seed without it comes back as a different handset, and a page
that sees the browser major change between two visits to the same
account learns that the operator is running something unusual.

The store is where the seed tuple lives, so the stratum has to
survive the round trip through it.
"""

from __future__ import annotations

import json

from novaprofile.core.generator import ProfileGenerator
from novaprofile.js_runtime.builder import JSRuntimeBuilder
from novaprofile.registry.seed_store import SeedStore


def _payload(config) -> dict:
    script = JSRuntimeBuilder().build_payload_only(config)
    return json.loads(script.split("=", 1)[1].rsplit(";", 1)[0])


def _family(preset_id: str = "pixel-8-pro-android14", units: int = 6) -> list:
    generator = ProfileGenerator()
    return [
        generator.generate(
            preset_id,
            master_seed=f"{unit:064x}",
            stratum=unit,
        )
        for unit in range(units)
    ]


def test_the_stratum_is_part_of_the_seed_tuple(tmp_path):
    """The seed tuple carries the unit's place in its family."""

    store = SeedStore(tmp_path / "fleet.db")
    try:
        configs = _family()
        for config in configs:
            store.save(config)

        rows = {row.profile_id: row for row in store.iter_rows()}
        assert len(rows) == len(configs)
        for config in configs:
            row = rows[config.identity.id]
            assert row.stratum == config.seed_config.stratum
            assert row.stratum is not None
    finally:
        store.close()


def test_a_reopened_profile_is_the_same_handset(tmp_path):
    """Regenerating a profile from the store gives the same fingerprint.

    Without the stratum the browser major, the core count and the
    memory would be drawn again from scratch, and the profile would
    come back as a different phone.
    """

    store = SeedStore(tmp_path / "fleet.db")
    try:
        configs = _family()
        for config in configs:
            store.save(config)

        for config in configs:
            reopened = store.get_config(config.identity.id)
            assert reopened is not None
            assert reopened.browser_version.major == config.browser_version.major
            assert (
                reopened.navigator.hardware_concurrency
                == config.navigator.hardware_concurrency
            )
            assert reopened.navigator.device_memory == config.navigator.device_memory
            assert reopened.navigator.language == config.navigator.language
            assert reopened.screen.width == config.screen.width
            assert reopened.screen.height == config.screen.height
            assert (
                _payload(reopened)["navigator"]["userAgent"]
                == _payload(config)["navigator"]["userAgent"]
            )
    finally:
        store.close()


def test_a_materialised_profile_is_unchanged_by_the_round_trip(tmp_path):
    """A profile that was stored with its configuration comes back as is."""

    store = SeedStore(tmp_path / "fleet.db")
    try:
        config = _family(units=1)[0]
        store.save(config, materialise=True)
        reopened = store.get_config(config.identity.id)
        assert reopened is not None
        assert reopened.navigator.user_agent == config.navigator.user_agent
        assert reopened.browser_version.major == config.browser_version.major
    finally:
        store.close()


def test_an_older_store_gains_the_column(tmp_path):
    """A store written before the stratum existed still opens.

    The column is added on open, and the rows that were written
    before it are read back with no stratum -- which means they are
    drawn independently, the way they always were.
    """

    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    # A store written by the version before the stratum existed: the
    # full schema, minus the one column that was added later.
    conn.execute(
        """
        CREATE TABLE profiles (
            profile_id        TEXT PRIMARY KEY,
            name              TEXT NOT NULL DEFAULT '',
            group_id          TEXT,
            folder_id         TEXT,
            tags              TEXT NOT NULL DEFAULT '[]',
            notes             TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'created',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            last_started_at   TEXT,
            last_closed_at    TEXT,
            total_runtime_ms  INTEGER NOT NULL DEFAULT 0,
            launch_count      INTEGER NOT NULL DEFAULT 0,
            master_seed       TEXT NOT NULL,
            seed_epoch        INTEGER NOT NULL DEFAULT 0,
            nudge_step        INTEGER NOT NULL DEFAULT 0,
            hkdf_version      TEXT NOT NULL DEFAULT '',
            preset_id         TEXT NOT NULL,
            core_version      TEXT NOT NULL DEFAULT '',
            browser_major     INTEGER,
            geo_country       TEXT,
            geo_timezone      TEXT,
            locale_primary    TEXT,
            device_serial     TEXT,
            device_fingerprint TEXT,
            config_json       TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    store = SeedStore(path)
    try:
        columns = {
            row["name"]
            for row in store.conn.execute("PRAGMA table_info(profiles)")
        }
        assert "stratum" in columns

        # The store is usable: a profile can be written and read back.
        config = _family(units=1)[0]
        store.save(config)
        assert store.get_config(config.identity.id) is not None
    finally:
        store.close()


def test_a_lone_profile_has_no_stratum(tmp_path):
    """A profile that stands alone carries no stratum.

    The stratum is a statement about a family.  A profile on its own
    has no family, and inventing one would make its fingerprint depend
    on something the page cannot see.
    """

    generator = ProfileGenerator()
    config = generator.generate("pixel-8-pro-android14", master_seed="ab" * 32)
    assert config.seed_config.stratum is None
