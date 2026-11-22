"""A compact, seed-first profile store for very large fleets.

:class:`novaprofile.registry.store.ProfileStore` keeps the full configuration
of every profile as JSON.  That is the right shape for a workbench where an
operator edits profiles by hand, but it is the wrong shape for a fleet of
100 million profiles: a configuration is about 11 KB, so the JSON alone
would need roughly a terabyte.

A profile is, however, *fully determined* by its seed tuple -- the master
seed, the preset, the epoch and the nudge step -- plus the parts that belong
to the operator (the name, the group, the proxy).  Regenerating from the
tuple reproduces every fingerprint axis byte for byte; only the identity
(uuid and the creation timestamps) is new.

This module therefore stores the tuple and materialises the configuration on
demand.  The saving is about 99 % of the row size, and it is what makes a
fleet of 100 million rows practical on a single machine.

The store keeps both modes: a row may carry a materialised ``config_json``
when the caller asks for one, and the seed tuple is always present.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..schema.profile import ProfileConfig

#: The schema of the compact store.  The ``config_json`` column is nullable:
#: it is filled in only for the profiles that were materialised.
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS profiles (
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
        stratum           INTEGER,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS signatures (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT NOT NULL,
        signature   TEXT NOT NULL,
        profile_id  TEXT NOT NULL,
        seed_epoch  INTEGER NOT NULL DEFAULT 0,
        nudge_step  INTEGER NOT NULL DEFAULT 0,
        state       TEXT NOT NULL DEFAULT 'active',
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_signatures_active_unique
        ON signatures(kind, signature)
        WHERE state = 'active'
    """,
    "CREATE INDEX IF NOT EXISTS idx_profiles_group ON profiles(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_folder ON profiles(folder_id)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_created ON profiles(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_serial ON profiles(device_serial)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_geo ON profiles(geo_country)",
    """
    CREATE INDEX IF NOT EXISTS idx_signatures_profile
        ON signatures(profile_id, kind)
    """,
    "CREATE INDEX IF NOT EXISTS idx_signatures_kind ON signatures(kind, state)",
)

#: The columns added to the ``profiles`` table after the table was
#: first written.  They are added to an existing store on open.
_ADDED_COLUMNS: dict[str, str] = {
    # The unit's place in its device family.  A profile that is
    # regenerated from its seed has to come back as the same handset,
    # so the stratum travels with the seed tuple.
    "stratum": "INTEGER",
}


@dataclass(frozen=True)
class SeedRow:
    """The stored seed tuple of a profile."""

    profile_id: str
    name: str
    master_seed: str
    preset_id: str
    seed_epoch: int
    nudge_step: int
    created_at: str
    stratum: int | None = None
    updated_at: str = ""
    status: str = "created"
    group_id: str | None = None
    folder_id: str | None = None
    tags: str = "[]"
    notes: str = ""
    hkdf_version: str = ""
    core_version: str = ""
    browser_major: int | None = None
    geo_country: str | None = None
    geo_timezone: str | None = None
    locale_primary: str | None = None
    device_serial: str | None = None
    device_fingerprint: str | None = None
    config_json: str | None = None
    last_started_at: str | None = None
    last_closed_at: str | None = None
    total_runtime_ms: int = 0
    launch_count: int = 0

    @property
    def size_bytes(self) -> int:
        """The bytes this row occupies in the table, roughly."""

        return sum(len(v.encode()) if isinstance(v, str) else 8 for v in self.__dict__.values())


class SeedStore:
    """A compact store that keeps profiles as seed tuples.

    The store is thread-safe: every public method takes an internal lock and
    uses one long-lived connection in ``check_same_thread=False`` mode.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Create a store bound to the SQLite database at *db_path*."""

        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle --------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the live connection, opening it on first use."""

        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        # A large cache keeps the index pages hot during a bulk insert.
        conn.execute("PRAGMA cache_size=-65536")
        for statement in SCHEMA:
            conn.execute(statement)
        self._migrate(conn)
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring an older store up to the current schema.

        ``CREATE TABLE IF NOT EXISTS`` leaves an existing table alone,
        so a store written by an earlier version keeps the columns it
        was created with.  The columns added since then are added
        here, one at a time, and only when they are missing.
        """

        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(profiles)")
        }
        if not columns:
            return
        for name, declaration in _ADDED_COLUMNS.items():
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE profiles ADD COLUMN {name} {declaration}")

    def close(self) -> None:
        """Close the connection."""

        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> SeedStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing ----------------------------------------------------------

    def save(self, config: ProfileConfig, *, materialise: bool = False) -> str:
        """Store a profile as its seed tuple.

        Parameters
        ----------
        config:
            The profile to store.
        materialise:
            When true the full configuration JSON is stored alongside the
            tuple, so the profile can be read back without regenerating it.
            This is what the workbench does; a bulk fleet load leaves it
            false.

        Returns
        -------
        str
            The profile id.
        """

        seed = config.seed_config
        now = datetime.now(UTC).isoformat()
        geo = config.geo_intelligence
        build = config.android_build
        status = config.identity.status
        status_text = status.value if hasattr(status, "value") else str(status)

        with self._lock:
            self.conn.execute(
                """
                INSERT INTO profiles (
                    profile_id, name, group_id, folder_id, tags, notes, status,
                    created_at, updated_at, master_seed, seed_epoch, nudge_step,
                    stratum, hkdf_version, preset_id, core_version, browser_major,
                    geo_country, geo_timezone, locale_primary,
                    device_serial, device_fingerprint, config_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    name=excluded.name,
                    group_id=excluded.group_id,
                    folder_id=excluded.folder_id,
                    tags=excluded.tags,
                    notes=excluded.notes,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    master_seed=excluded.master_seed,
                    seed_epoch=excluded.seed_epoch,
                    nudge_step=excluded.nudge_step,
                    stratum=excluded.stratum,
                    hkdf_version=excluded.hkdf_version,
                    preset_id=excluded.preset_id,
                    core_version=excluded.core_version,
                    browser_major=excluded.browser_major,
                    geo_country=excluded.geo_country,
                    geo_timezone=excluded.geo_timezone,
                    locale_primary=excluded.locale_primary,
                    device_serial=excluded.device_serial,
                    device_fingerprint=excluded.device_fingerprint,
                    config_json=excluded.config_json
                """,
                (
                    config.identity.id,
                    config.identity.name,
                    config.identity.group_id,
                    config.identity.folder_id,
                    json.dumps(list(config.identity.tags)),
                    config.identity.notes,
                    status_text,
                    config.identity.created_at.isoformat(),
                    now,
                    seed.master_seed,
                    seed.seed_epoch,
                    seed.nudge_step,
                    seed.stratum,
                    seed.hkdf_version,
                    seed.preset_id,
                    seed.core_version,
                    config.browser_version.major if config.browser_version else None,
                    geo.country_code or None,
                    config.timezone.iana_name or None,
                    config.locale.primary or None,
                    build.serial,
                    build.fingerprint,
                    config.model_dump_json() if materialise else None,
                ),
            )
        return config.identity.id

    def save_many(self, configs: Iterator[ProfileConfig], *, materialise: bool = False) -> int:
        """Store many profiles in a single transaction."""

        count = 0
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                for config in configs:
                    self.save(config, materialise=materialise)
                    count += 1
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return count

    # -- reading ----------------------------------------------------------

    def get_row(self, profile_id: str) -> SeedRow | None:
        """Return the stored seed tuple of a profile."""

        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            return None
        return SeedRow(**dict(row))

    def get_config(self, profile_id: str) -> ProfileConfig | None:
        """Return the full configuration of a profile.

        The configuration is regenerated from the stored seed tuple when it
        was not materialised.  The regenerated profile carries the stored
        identity, so the uuid and the creation timestamp survive the round
        trip.
        """

        row = self.get_row(profile_id)
        if row is None:
            return None

        if row.config_json:
            return ProfileConfig.model_validate_json(row.config_json)

        from ..core.generator import ProfileGenerator

        generator = ProfileGenerator()
        config = generator.generate(
            row.preset_id,
            name=row.name,
            master_seed=row.master_seed,
            seed_epoch=row.seed_epoch,
            nudge_step=row.nudge_step,
            profile_id=row.profile_id,
            stratum=row.stratum,
        )
        return config.model_copy(
            update={
                "identity": config.identity.model_copy(
                    update={
                        "name": row.name,
                        "group_id": row.group_id,
                        "folder_id": row.folder_id,
                        "notes": row.notes,
                    }
                )
            }
        )

    def iter_rows(self, *, batch: int = 1000) -> Iterator[SeedRow]:
        """Iterate over every stored profile, in insertion order."""

        with self._lock:
            cursor = self.conn.execute("SELECT * FROM profiles ORDER BY created_at")
            while True:
                rows = cursor.fetchmany(batch)
                if not rows:
                    break
                for row in rows:
                    yield SeedRow(**dict(row))

    def count(self) -> int:
        """How many profiles are stored."""

        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()
        return int(row["n"]) if row else 0

    # -- signatures -------------------------------------------------------

    def record_signature(
        self,
        kind: str,
        signature: str,
        profile_id: str,
        *,
        seed_epoch: int = 0,
        nudge_step: int = 0,
        state: str = "active",
    ) -> None:
        """Store a signature, refusing a duplicate active one.

        The uniqueness is enforced by the database, so two processes racing
        to allocate the same signature cannot both win.
        """

        with self._lock:
            self.conn.execute(
                """
                INSERT INTO signatures
                    (kind, signature, profile_id, seed_epoch, nudge_step, state, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    kind,
                    signature,
                    profile_id,
                    seed_epoch,
                    nudge_step,
                    state,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def find_signature(self, kind: str, signature: str) -> dict[str, Any] | None:
        """Return the active record of a signature, if any."""

        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM signatures WHERE kind = ? AND signature = ? AND state = 'active'",
                (kind, signature),
            ).fetchone()
        return dict(row) if row else None

    def count_signatures(self, kind: str) -> int:
        """How many active signatures of a kind exist."""

        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM signatures WHERE kind = ? AND state = 'active'",
                (kind,),
            ).fetchone()
        return int(row["n"]) if row else 0

    # -- bulk loading ----------------------------------------------------

    def attach_and_merge(self, other_path: str | Path) -> int:
        """Merge another store's rows into this one.

        A bulk load writes each slice of the fleet into its own database
        file -- SQLite serialises writers, so a single file cannot take the
        write load of a whole fleet -- and merges the pieces afterwards.

        Returns
        -------
        int
            How many profiles were copied over.
        """

        source = Path(other_path)
        if not source.exists():
            return 0

        copied = 0
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                cursor = self.conn.execute("ATTACH DATABASE ? AS shard", (str(source),))
                cursor.fetchall()
                # The shard may be a store written before the stratum
                # existed, so the column is read defensively: a shard
                # without the column contributes no stratum, and the
                # profiles in it are drawn independently.
                shard_columns = {
                    row["name"]
                    for row in self.conn.execute("PRAGMA shard.table_info(profiles)")
                }
                stratum_select = (
                    "stratum" if "stratum" in shard_columns else "NULL"
                )
                self.conn.execute(
                    f"""
                    INSERT OR IGNORE INTO profiles (
                        profile_id, name, group_id, folder_id, tags, notes, status,
                        created_at, updated_at, last_started_at, last_closed_at,
                        total_runtime_ms, launch_count, master_seed, seed_epoch,
                        nudge_step, stratum, hkdf_version, preset_id, core_version,
                        browser_major, geo_country, geo_timezone, locale_primary,
                        device_serial, device_fingerprint, config_json
                    )
                    SELECT
                        profile_id, name, group_id, folder_id, tags, notes, status,
                        created_at, updated_at, last_started_at, last_closed_at,
                        total_runtime_ms, launch_count, master_seed, seed_epoch,
                        nudge_step, {stratum_select}, hkdf_version, preset_id, core_version,
                        browser_major, geo_country, geo_timezone, locale_primary,
                        device_serial, device_fingerprint, config_json
                    FROM shard.profiles
                    WHERE profile_id NOT IN (SELECT profile_id FROM main.profiles)
                    """
                )
                copied = int(self.conn.execute("SELECT changes()").fetchone()[0])
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                with contextlib.suppress(sqlite3.OperationalError):
                    self.conn.execute("DETACH DATABASE shard")
        return copied


__all__ = ["SCHEMA", "SeedRow", "SeedStore"]
