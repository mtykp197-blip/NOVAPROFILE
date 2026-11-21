"""SQLite-backed persistence for NOVA profiles and collision signatures.

The registry owns two things:

* the **profiles** table -- one row per browser profile, holding the identity
  metadata plus the complete :class:`~novaprofile.schema.profile.ProfileConfig`
  serialised as JSON in ``config_json``;
* the **signatures** table -- one row per tracked fingerprint axis, used by the
  collision guard to detect and prevent duplicate signatures.

Storage layout follows the contract::

    %LOCALAPPDATA%\\NovaProfile\\state.db

The database is opened in WAL mode with foreign keys enabled.  The
``ProfileConfig`` model itself is imported lazily so this module stays
importable even while the schema package is still being assembled.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novaprofile.schema.enums import SignatureKind, SignatureState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from novaprofile.registry.models import ProfileFilter
    from novaprofile.schema.profile import ProfileConfig

__all__ = [
    "ProfileStore",
    "StoreError",
    "DuplicateProfileError",
    "DuplicateSignatureError",
    "ProfileNotFoundError",
    "default_db_path",
    "utc_now_iso",
]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class StoreError(RuntimeError):
    """Base class for registry persistence errors."""


class DuplicateProfileError(StoreError):
    """Raised when inserting a profile whose id already exists."""


class DuplicateSignatureError(StoreError):
    """Raised when inserting an ACTIVE signature that already exists."""


class ProfileNotFoundError(StoreError):
    """Raised when a profile id does not resolve to a stored profile."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def default_db_path() -> Path:
    """Return the default location of the state database.

    Resolution order:

    1. ``$NOVA_PROFILE_HOME`` if set (used by tests).
    2. ``%LOCALAPPDATA%\\NovaProfile`` on Windows.
    3. ``~/.local/share/NovaProfile`` elsewhere.
    """
    override = os.environ.get("NOVA_PROFILE_HOME")
    if override:
        return Path(override).expanduser() / "state.db"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "NovaProfile" / "state.db"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "NovaProfile" / "state.db"


def _load_profile_models() -> tuple[type[Any], type[Any], type[Any]]:
    """Import ``ProfileConfig``, ``ProfileIdentity`` and ``SeedConfig``.

    Imported lazily through :mod:`importlib` so that importing this module
    never fails because of the schema package's import order.
    """
    module = importlib.import_module("novaprofile.schema.profile")
    profile_config = module.ProfileConfig
    profile_identity = module.ProfileIdentity
    seed_config = module.SeedConfig
    return profile_config, profile_identity, seed_config


def _try_load_profile_models() -> tuple[type[Any], type[Any], type[Any]] | None:
    """Like :func:`_load_profile_models` but returns ``None`` when unavailable.

    The schema package may not have been delivered yet; callers use this to
    fall back to duck-typed handling instead of failing outright.
    """
    try:
        return _load_profile_models()
    except (ImportError, AttributeError):
        return None


def _enum_value(value: object) -> str:
    """Return the persisted string form of *value*.

    Accepts plain strings and ``str``-derived enums; anything else is rejected
    so the database never receives a surprising representation.
    """
    if isinstance(value, str):
        return value
    raw = getattr(value, "value", None)
    if isinstance(raw, str):
        return raw
    raise StoreError(f"cannot persist {value!r} as a string")


def _iso(value: object) -> str:
    """Return the ISO-8601 representation of a datetime-ish value."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise StoreError(f"cannot persist {value!r} as a timestamp")


def _optional_iso(value: object) -> str | None:
    """Return the ISO-8601 representation of an optional datetime."""
    if value is None:
        return None
    return _iso(value)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

_SCHEMA_VERSION: int = 1

_CREATE_SQL: tuple[str, ...] = (
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
        master_seed       TEXT NOT NULL DEFAULT '',
        seed_epoch        INTEGER NOT NULL DEFAULT 0,
        nudge_step        INTEGER NOT NULL DEFAULT 0,
        hkdf_version      TEXT NOT NULL DEFAULT '',
        preset_id         TEXT NOT NULL DEFAULT '',
        core_version      TEXT NOT NULL DEFAULT '',
        config_json       TEXT NOT NULL
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
        created_at  TEXT NOT NULL,
        FOREIGN KEY (profile_id) REFERENCES profiles(profile_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS groups (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS folders (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )
    """,
    # The seed allocator's counters live in the database rather than in the
    # process memory: a counter kept in memory restarts at zero in every
    # worker, so two processes would hand out the very same seeds.
    """
    CREATE TABLE IF NOT EXISTS counters (
        name        TEXT PRIMARY KEY,
        value       INTEGER NOT NULL DEFAULT 0
    )
    """,
    # Collision detection must be atomic, so the uniqueness constraint is
    # enforced by the database rather than by a read-then-write in Python.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_signatures_active_unique
        ON signatures(kind, signature)
        WHERE state = 'active'
    """,
    "CREATE INDEX IF NOT EXISTS idx_profiles_group ON profiles(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_folder ON profiles(folder_id)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_created ON profiles(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_profiles_name ON profiles(name)",
    """
    CREATE INDEX IF NOT EXISTS idx_signatures_profile
        ON signatures(profile_id, kind)
    """,
    "CREATE INDEX IF NOT EXISTS idx_signatures_kind ON signatures(kind, state)",
)


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class ProfileStore:
    """Persistence layer for browser profiles and fingerprint signatures.

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
        """Open the database with WAL journaling and foreign keys enabled."""
        if self.db_path.parent and str(self.db_path.parent) not in ("", "."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init_db(self) -> None:
        """Create the schema if it does not exist yet."""
        with self._lock:
            conn = self.conn
            with conn:
                for statement in _CREATE_SQL:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def next_counter(self, name: str) -> int:
        """Return the next value of a named counter.

        The counter lives in the database, so it is shared by every process
        that opens the store.  A counter kept in the process memory would
        restart at zero in every worker, and two workers would then hand
        out the very same seeds -- which is exactly the duplicate the
        collision guard has to prevent.

        The increment is atomic: SQLite serialises the write, so two
        concurrent callers never receive the same value.
        """

        with self._lock:
            conn = self.conn
            with conn:
                conn.execute(
                    """
                    INSERT INTO counters (name, value) VALUES (?, 0)
                    ON CONFLICT(name) DO NOTHING
                    """,
                    (name,),
                )
                conn.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))
                row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return int(row["value"]) if row else 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> ProfileStore:
        self.init_db()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- profile CRUD -----------------------------------------------------

    def create_profile(self, config: ProfileConfig) -> None:
        """Insert a new profile row.

        Raises:
            DuplicateProfileError: If a profile with the same id already exists.
        """
        identity = config.identity
        seed = config.seed_config
        payload = config.model_dump_json()

        with self._lock:
            conn = self.conn
            with conn:
                try:
                    conn.execute(
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
                            identity.id,
                            identity.name,
                            identity.group_id,
                            identity.folder_id,
                            json.dumps(list(identity.tags), ensure_ascii=False),
                            identity.notes,
                            _enum_value(identity.status),
                            _iso(identity.created_at),
                            _iso(identity.updated_at),
                            _optional_iso(identity.last_started_at),
                            _optional_iso(identity.last_closed_at),
                            int(identity.total_runtime_ms),
                            int(identity.launch_count),
                            seed.master_seed,
                            int(seed.seed_epoch),
                            int(seed.nudge_step),
                            seed.hkdf_version,
                            seed.preset_id,
                            seed.core_version,
                            payload,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DuplicateProfileError(f"profile {identity.id!r} already exists") from exc

    def get_profile(self, profile_id: str) -> ProfileConfig | None:
        """Return the profile with *profile_id*, or ``None`` when absent."""
        with self._lock:
            row = self.conn.execute(
                "SELECT config_json FROM profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize(row["config_json"])

    def update_profile(self, config: ProfileConfig) -> None:
        """Overwrite the stored row for ``config.identity.id``.

        Raises:
            ProfileNotFoundError: If the profile does not exist.
        """
        identity = config.identity
        seed = config.seed_config
        payload = config.model_dump_json()

        with self._lock:
            conn = self.conn
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE profiles SET
                        name = ?, group_id = ?, folder_id = ?, tags = ?, notes = ?,
                        status = ?, created_at = ?, updated_at = ?,
                        last_started_at = ?, last_closed_at = ?,
                        total_runtime_ms = ?, launch_count = ?,
                        master_seed = ?, seed_epoch = ?, nudge_step = ?,
                        hkdf_version = ?, preset_id = ?, core_version = ?,
                        config_json = ?
                    WHERE profile_id = ?
                    """,
                    (
                        identity.name,
                        identity.group_id,
                        identity.folder_id,
                        json.dumps(list(identity.tags), ensure_ascii=False),
                        identity.notes,
                        _enum_value(identity.status),
                        _iso(identity.created_at),
                        _iso(identity.updated_at),
                        _optional_iso(identity.last_started_at),
                        _optional_iso(identity.last_closed_at),
                        int(identity.total_runtime_ms),
                        int(identity.launch_count),
                        seed.master_seed,
                        int(seed.seed_epoch),
                        int(seed.nudge_step),
                        seed.hkdf_version,
                        seed.preset_id,
                        seed.core_version,
                        payload,
                        identity.id,
                    ),
                )
            if cursor.rowcount == 0:
                raise ProfileNotFoundError(f"profile {identity.id!r} not found")

    def delete_profile(self, profile_id: str) -> None:
        """Delete the profile and, by cascade, its signatures.

        Raises:
            ProfileNotFoundError: If the profile does not exist.
        """
        with self._lock:
            conn = self.conn
            with conn:
                cursor = conn.execute("DELETE FROM profiles WHERE profile_id = ?", (profile_id,))
            if cursor.rowcount == 0:
                raise ProfileNotFoundError(f"profile {profile_id!r} not found")

    # -- listing / filtering ---------------------------------------------

    def list_profiles(
        self,
        *,
        group_id: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        search: str | None = None,
        folder_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ProfileConfig]:
        """Return profiles matching the supplied criteria.

        Args:
            group_id: Restrict to one group.
            tag: Restrict to profiles carrying this tag.
            status: Restrict to one lifecycle status.
            search: Case-insensitive substring over name, notes and tags.
            folder_id: Restrict to one folder.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.
        """
        where, params = self._build_where(
            group_id=group_id,
            tag=tag,
            status=status,
            search=search,
            folder_id=folder_id,
        )
        sql = f"SELECT config_json FROM profiles{where} ORDER BY created_at ASC, profile_id ASC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = [*params, int(limit), int(offset)]
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params = [*params, int(offset)]

        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._deserialize(row["config_json"]) for row in rows]

    def list_profiles_filtered(self, filt: ProfileFilter) -> list[ProfileConfig]:
        """Return profiles matching a :class:`ProfileFilter`."""
        return self.list_profiles(
            group_id=filt.group_id,
            tag=filt.tags[0] if filt.tags else None,
            status=filt.status,
            search=filt.search,
            folder_id=filt.folder_id,
            limit=filt.limit,
            offset=filt.offset,
        )

    def count_profiles(
        self,
        *,
        group_id: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        search: str | None = None,
        folder_id: str | None = None,
    ) -> int:
        """Return the number of profiles matching the supplied criteria."""
        where, params = self._build_where(
            group_id=group_id,
            tag=tag,
            status=status,
            search=search,
            folder_id=folder_id,
        )
        with self._lock:
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM profiles{where}", params).fetchone()
        return int(row["n"])

    @staticmethod
    def _build_where(
        *,
        group_id: str | None,
        tag: str | None,
        status: str | None,
        search: str | None,
        folder_id: str | None,
    ) -> tuple[str, list[Any]]:
        """Build the SQL ``WHERE`` clause and its parameters."""
        clauses: list[str] = []
        params: list[Any] = []

        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if folder_id is not None:
            clauses.append("folder_id = ?")
            params.append(folder_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if tag is not None:
            # ``tags`` holds a JSON array; match the exact element so that
            # ``"eu"`` does not match the tag ``"europe"``.
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(profiles.tags) WHERE json_each.value = ?)"
            )
            params.append(tag)
        if search:
            needle = f"%{search.lower()}%"
            clauses.append("(LOWER(name) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(tags) LIKE ?)")
            params.extend([needle, needle, needle])

        if not clauses:
            return "", []
        return " WHERE " + " AND ".join(clauses), params

    # -- lifecycle counters ----------------------------------------------

    def increment_runtime(self, profile_id: str, duration_ms: int) -> None:
        """Add *duration_ms* to the profile's accumulated runtime.

        Raises:
            ProfileNotFoundError: If the profile does not exist.
        """
        if duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        config = self._require_profile(profile_id)
        identity = config.identity
        updated = self._with_identity(
            config,
            {
                "total_runtime_ms": identity.total_runtime_ms + int(duration_ms),
                "updated_at": datetime.now(UTC),
            },
        )
        self.update_profile(updated)

    def mark_launched(self, profile_id: str) -> None:
        """Record a launch: bump ``launch_count`` and stamp ``last_started_at``."""
        config = self._require_profile(profile_id)
        now = datetime.now(UTC)
        updated = self._with_identity(
            config,
            {
                "launch_count": config.identity.launch_count + 1,
                "last_started_at": now,
                "updated_at": now,
            },
        )
        self.update_profile(updated)

    def mark_closed(self, profile_id: str) -> None:
        """Record a shutdown: stamp ``last_closed_at``."""
        config = self._require_profile(profile_id)
        now = datetime.now(UTC)
        updated = self._with_identity(
            config,
            {"last_closed_at": now, "updated_at": now},
        )
        self.update_profile(updated)

    def set_status(self, profile_id: str, status: str) -> None:
        """Set the lifecycle status of a profile.

        Raises:
            ProfileNotFoundError: If the profile does not exist.
        """
        value = _enum_value(status)
        config = self._require_profile(profile_id)
        updated = self._with_identity(
            config,
            {"status": value, "updated_at": datetime.now(UTC)},
        )
        self.update_profile(updated)

    @staticmethod
    def _with_identity(config: ProfileConfig, changes: dict[str, Any]) -> Any:
        """Return a copy of *config* whose identity carries *changes*.

        ``ProfileConfig`` and ``ProfileIdentity`` are frozen pydantic models,
        so the identity is rebuilt with :meth:`model_copy` and swapped into a
        copy of the config.  Mutable stand-ins are updated in place instead.
        """
        identity = config.identity
        if hasattr(identity, "model_copy"):
            new_identity = identity.model_copy(update=changes)
            if hasattr(config, "model_copy"):
                return config.model_copy(update={"identity": new_identity})
            config.identity = new_identity
            return config
        for key, value in changes.items():
            setattr(identity, key, value)
        return config

    def get_status(self, profile_id: str) -> str | None:
        """Return the lifecycle status of a profile, or ``None`` when absent."""
        with self._lock:
            row = self.conn.execute(
                "SELECT status FROM profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        return None if row is None else str(row["status"])

    def _require_profile(self, profile_id: str) -> ProfileConfig:
        """Return the profile or raise :class:`ProfileNotFoundError`."""
        config = self.get_profile(profile_id)
        if config is None:
            raise ProfileNotFoundError(f"profile {profile_id!r} not found")
        return config

    # -- cloning ---------------------------------------------------------

    def clone_profile(
        self,
        profile_id: str,
        new_profile_id: str,
        new_master_seed: str,
        *,
        new_name: str | None = None,
    ) -> ProfileConfig:
        """Duplicate a profile row under a new id with a fresh master seed.

        The clone starts with a clean signature footprint: no signature rows
        are copied, because the new seed produces different fingerprints and
        the clone must not inherit the original's collision history.  The
        clone's runtime counters and launch timestamps are reset.

        Args:
            profile_id: The source profile.
            new_profile_id: The id of the clone; must not already exist.
            new_master_seed: The 64-char hex master seed for the clone.
            new_name: Optional display name for the clone; defaults to the
                source name with a ``" (copy)"`` suffix.

        Returns:
            The newly created clone config.

        Raises:
            ProfileNotFoundError: If the source profile does not exist.
            DuplicateProfileError: If the new id already exists.
        """
        source = self.get_profile(profile_id)
        if source is None:
            raise ProfileNotFoundError(f"profile {profile_id!r} not found")

        models = _try_load_profile_models()
        if models is not None:
            profile_config, profile_identity, seed_config = models
        else:
            raise StoreError("cannot clone: novaprofile.schema.profile is not importable")
        now = datetime.now(UTC)

        clone_identity = self._clone_identity(
            source, new_profile_id, new_name, now, profile_identity
        )
        clone_seed = self._clone_seed(source, new_master_seed, seed_config)
        clone = self._assemble_clone(source, clone_identity, clone_seed, profile_config)
        self.create_profile(clone)
        return clone

    @staticmethod
    def _clone_identity(
        source: ProfileConfig,
        new_profile_id: str,
        new_name: str | None,
        now: datetime,
        profile_identity: type[Any],
    ) -> Any:
        """Build the clone's identity, resetting counters and timestamps."""
        identity = source.identity
        return profile_identity(
            id=new_profile_id,
            name=(new_name if new_name is not None else f"{identity.name} (copy)"),
            group_id=identity.group_id,
            folder_id=identity.folder_id,
            tags=list(identity.tags),
            notes=identity.notes,
            status=identity.status,
            created_at=now,
            updated_at=now,
            last_started_at=None,
            last_closed_at=None,
            total_runtime_ms=0,
            launch_count=0,
        )

    @staticmethod
    def _clone_seed(source: ProfileConfig, new_master_seed: str, seed_config: type[Any]) -> Any:
        """Build the clone's seed configuration with a fresh master seed."""
        seed = source.seed_config
        return seed_config(
            master_seed=new_master_seed,
            seed_epoch=seed.seed_epoch,
            nudge_step=seed.nudge_step,
            hkdf_version=seed.hkdf_version,
            preset_id=seed.preset_id,
            core_version=seed.core_version,
        )

    @staticmethod
    def _assemble_clone(
        source: ProfileConfig,
        clone_identity: Any,
        clone_seed: Any,
        profile_config: type[Any],
    ) -> Any:
        """Combine the clone's identity and seed with the source's payload.

        Every axis other than the identity and the seed is carried over
        unchanged, so the clone matches the source device exactly while
        producing a different fingerprint from its new master seed.
        """
        payload = {
            key: value
            for key, value in source.model_dump().items()
            if key not in ("identity", "seed_config")
        }
        return profile_config(identity=clone_identity, seed_config=clone_seed, **payload)

    # -- signatures ------------------------------------------------------

    def record_signature(self, record: Any) -> None:
        """Insert a signature record.

        The record may be a
        :class:`novaprofile.core.collision.SignatureRecord` dataclass or any
        object exposing ``kind``, ``signature``, ``profile_id``,
        ``seed_epoch``, ``nudge_step``, ``state`` and ``created_at``.

        Raises:
            DuplicateSignatureError: If an ACTIVE signature with the same
                ``(kind, signature)`` pair already exists.  The uniqueness
                constraint is enforced by SQLite, so the check is atomic even
                under concurrency.
            ProfileNotFoundError: If ``profile_id`` does not resolve.
        """
        kind = _enum_value(record.kind)
        signature = record.signature
        profile_id = record.profile_id
        seed_epoch = int(getattr(record, "seed_epoch", 0))
        nudge_step = int(getattr(record, "nudge_step", 0))
        state = _enum_value(getattr(record, "state", SignatureState.ACTIVE))
        created_at = getattr(record, "created_at", None)
        created_at_iso = utc_now_iso() if created_at is None else _iso(created_at)

        if not isinstance(signature, str) or not signature:
            raise StoreError("signature must be a non-empty string")
        if not isinstance(profile_id, str) or not profile_id:
            raise StoreError("signature profile_id must be a non-empty string")

        with self._lock:
            conn = self.conn
            with conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO signatures (
                            kind, signature, profile_id, seed_epoch,
                            nudge_step, state, created_at
                        ) VALUES (?,?,?,?,?,?,?)
                        """,
                        (
                            kind,
                            signature,
                            profile_id,
                            seed_epoch,
                            nudge_step,
                            state,
                            created_at_iso,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    # SQLite reports the unique-index violation and the
                    # foreign-key violation with the same exception type, so
                    # the two cases must be told apart explicitly.
                    message = str(exc)
                    if "FOREIGN KEY" in message:
                        raise ProfileNotFoundError(f"profile {profile_id!r} not found") from exc
                    if "UNIQUE" in message:
                        raise DuplicateSignatureError(
                            f"an active {kind} signature {signature!r} already exists"
                        ) from exc
                    raise StoreError(f"could not record the {kind} signature: {exc}") from exc

    def find_signature(self, kind: SignatureKind | str, signature: str) -> Any | None:
        """Return the ACTIVE signature matching ``(kind, signature)``.

        Only ACTIVE rows are considered: archived and revoked signatures are
        historical records and must not block a new allocation.
        """
        kind_value = _enum_value(kind)
        with self._lock:
            row = self.conn.execute(
                """
                SELECT kind, signature, profile_id, seed_epoch, nudge_step,
                       state, created_at
                FROM signatures
                WHERE kind = ? AND signature = ? AND state = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (kind_value, signature, SignatureState.ACTIVE.value),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_signature(row)

    def count_signatures(self, kind: SignatureKind | str) -> int:
        """Return the number of ACTIVE signatures of the given *kind*."""
        kind_value = _enum_value(kind)
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM signatures WHERE kind = ? AND state = ?",
                (kind_value, SignatureState.ACTIVE.value),
            ).fetchone()
        return int(row["n"])

    def list_signatures(self, kind: SignatureKind | str | None = None) -> list[Any]:
        """Return all signature rows, optionally filtered by *kind*."""
        params: list[Any] = []
        sql = (
            "SELECT kind, signature, profile_id, seed_epoch, nudge_step, "
            "state, created_at FROM signatures"
        )
        if kind is not None:
            sql += " WHERE kind = ?"
            params.append(_enum_value(kind))
        sql += " ORDER BY id ASC"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_signature(row) for row in rows]

    def archive_signature(self, kind: SignatureKind | str, signature: str) -> None:
        """Move an ACTIVE signature to the ARCHIVED state."""
        kind_value = _enum_value(kind)
        with self._lock:
            conn = self.conn
            with conn:
                conn.execute(
                    """
                    UPDATE signatures SET state = ?
                    WHERE kind = ? AND signature = ? AND state = ?
                    """,
                    (
                        SignatureState.ARCHIVED.value,
                        kind_value,
                        signature,
                        SignatureState.ACTIVE.value,
                    ),
                )

    def _row_to_signature(self, row: sqlite3.Row) -> Any:
        """Convert a signature row into the collision module's record type.

        :class:`novaprofile.core.collision.SignatureRecord` is imported lazily
        so that this module does not depend on the core package; when it is
        unavailable a local dataclass with the same shape is returned.
        """
        created_at = datetime.fromisoformat(str(row["created_at"]))
        try:
            module = importlib.import_module("novaprofile.core.collision")
            record_type = module.SignatureRecord
        except (ImportError, AttributeError):
            record_type = None

        if record_type is not None:
            try:
                return record_type(
                    kind=SignatureKind(row["kind"]),
                    signature=str(row["signature"]),
                    profile_id=str(row["profile_id"]),
                    seed_epoch=int(row["seed_epoch"]),
                    nudge_step=int(row["nudge_step"]),
                    state=SignatureState(row["state"]),
                    created_at=created_at,
                )
            except (ValueError, TypeError):
                pass

        from novaprofile.registry.models import SignatureRecord as LocalRecord

        return LocalRecord(
            kind=SignatureKind(row["kind"]),
            signature=str(row["signature"]),
            profile_id=str(row["profile_id"]),
            seed_epoch=int(row["seed_epoch"]),
            nudge_step=int(row["nudge_step"]),
            state=SignatureState(row["state"]),
            created_at=created_at,
        )

    # -- groups and folders ----------------------------------------------

    def create_group(self, group_id: str, name: str) -> None:
        """Insert or update a group."""
        with self._lock:
            conn = self.conn
            with conn:
                conn.execute(
                    """
                    INSERT INTO groups (id, name, created_at) VALUES (?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name = excluded.name
                    """,
                    (group_id, name, utc_now_iso()),
                )

    def list_groups(self) -> list[tuple[str, str]]:
        """Return ``(id, name)`` pairs for every group."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, name FROM groups ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [(str(row["id"]), str(row["name"])) for row in rows]

    def delete_group(self, group_id: str) -> None:
        """Delete a group and detach the profiles that referenced it."""
        self._detach_profiles("group_id", group_id)
        with self._lock:
            conn = self.conn
            with conn:
                conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))

    def _detach_profiles(self, column: str, value: str) -> None:
        """Clear ``column`` on every profile that references *value*.

        Both the denormalised column and the stored ``config_json`` are
        updated so the two representations never disagree.
        """
        if column not in ("group_id", "folder_id"):
            raise ValueError(f"unsupported detach column {column!r}")
        with self._lock:
            rows = self.conn.execute(
                f"SELECT profile_id FROM profiles WHERE {column} = ?", (value,)
            ).fetchall()
        for row in rows:
            profile_id = str(row["profile_id"])
            config = self.get_profile(profile_id)
            if config is None:
                continue
            updated = self._with_identity(
                config,
                {column: None, "updated_at": datetime.now(UTC)},
            )
            self.update_profile(updated)

    def create_folder(self, folder_id: str, name: str) -> None:
        """Insert or update a folder."""
        with self._lock:
            conn = self.conn
            with conn:
                conn.execute(
                    """
                    INSERT INTO folders (id, name, created_at) VALUES (?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name = excluded.name
                    """,
                    (folder_id, name, utc_now_iso()),
                )

    def list_folders(self) -> list[tuple[str, str]]:
        """Return ``(id, name)`` pairs for every folder."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, name FROM folders ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [(str(row["id"]), str(row["name"])) for row in rows]

    def delete_folder(self, folder_id: str) -> None:
        """Delete a folder and detach the profiles that referenced it."""
        self._detach_profiles("folder_id", folder_id)
        with self._lock:
            conn = self.conn
            with conn:
                conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))

    # -- serialisation ----------------------------------------------------

    def _deserialize(self, payload: str) -> ProfileConfig:
        """Rebuild a :class:`ProfileConfig` from its stored JSON.

        The concrete model class is resolved at call time so that importing
        this module never depends on the schema package's import order.
        """
        models = _try_load_profile_models()
        if models is None:
            raise StoreError(
                "cannot deserialize the stored profile: "
                "novaprofile.schema.profile is not importable"
            )
        profile_config, _identity, _seed = models
        try:
            return profile_config.model_validate_json(payload)
        except Exception as exc:  # noqa: BLE001 - re-raised as a store error
            raise StoreError(f"stored profile payload is not valid: {exc}") from exc
