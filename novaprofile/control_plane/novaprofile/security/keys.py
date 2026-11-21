"""Key management helpers for the NOVA Profile Engine.

The :class:`KeyManager` owns the master key lifecycle (load, save, rotate) and
derives purpose-scoped sub-keys from it with HKDF-SHA256.  Deriving a
separate key per purpose means a compromise of one derived key (say, the
proxy-password key) never reveals the key material used for a different
purpose.

Purpose strings are namespaced with a dot separator, e.g. ``"proxy.password"``
or ``"session.token"``.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from novaprofile.security.crypto import (
    KEY_LENGTH,
    SecretError,
    get_master_key,
    master_key_path,
)

__all__ = [
    "KeyManager",
    "InvalidPurposeError",
    "PURPOSE_SEPARATOR",
    "HKDF_SALT",
    "HKDF_HASH",
]

PURPOSE_SEPARATOR: Final[str] = "."
"""Separator used between the namespace and the purpose name."""

HKDF_SALT: Final[bytes] = b"novaprofile/security/keys/v1"
"""Fixed HKDF salt; a constant salt is appropriate because the master key is
already high-entropy random material."""

HKDF_HASH: Final[str] = "sha256"
"""HKDF hash function."""

_CACHE_SIZE: Final[int] = 64
"""Maximum number of derived sub-keys kept in the LRU cache."""


class InvalidPurposeError(ValueError):
    """Raised when a key purpose is not a valid namespaced identifier."""


def _validate_purpose(purpose: str) -> str:
    """Validate and normalise a purpose string.

    A purpose must be a non-empty string of ASCII letters, digits, dots,
    underscores and dashes.  It must contain at least one separator so that
    every purpose lives in a namespace (``"proxy.password"`` not
    ``"password"``).
    """
    if not isinstance(purpose, str):
        raise InvalidPurposeError("purpose must be a string")
    normalized = purpose.strip().lower()
    if not normalized:
        raise InvalidPurposeError("purpose must not be empty")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if not set(normalized).issubset(allowed):
        raise InvalidPurposeError(f"purpose {purpose!r} contains characters outside [a-z0-9._-]")
    if PURPOSE_SEPARATOR not in normalized:
        raise InvalidPurposeError(
            f"purpose {purpose!r} is not namespaced; expected the form 'namespace.name'"
        )
    if normalized.startswith(PURPOSE_SEPARATOR) or normalized.endswith(PURPOSE_SEPARATOR):
        raise InvalidPurposeError(f"purpose {purpose!r} has an empty component")
    if PURPOSE_SEPARATOR * 2 in normalized:
        raise InvalidPurposeError(f"purpose {purpose!r} has an empty component")
    return normalized


class KeyManager:
    """Manages the master key and derives purpose-scoped sub-keys.

    The manager is safe to share between threads.  Derived sub-keys are cached
    in a bounded LRU cache so repeated calls for the same purpose are cheap.
    """

    def __init__(self, key_path: Path | None = None) -> None:
        """Create a key manager.

        Args:
            key_path: Optional override for the master-key file location.  When
                omitted the platform default from
                :func:`novaprofile.security.crypto.master_key_path` is used.
        """
        self._key_path = Path(key_path) if key_path is not None else None
        self._master: bytes | None = None
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.RLock()

    # -- master key ------------------------------------------------------

    @property
    def key_path(self) -> Path:
        """Path of the master-key file this manager uses."""
        if self._key_path is not None:
            return self._key_path
        return master_key_path()

    def load_master_key(self) -> bytes:
        """Return the master key, reading it from disk when needed.

        The key is cached in memory for the lifetime of the manager.
        """
        with self._lock:
            if self._master is None:
                self._master = self._read_master_key()
            return self._master

    def _read_master_key(self) -> bytes:
        """Read the master key from the configured path."""
        if self._key_path is None:
            return get_master_key()
        path = self._key_path
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            # Delegate creation to the shared helper so the DPAPI encoding and
            # the atomic write stay in one place.
            return get_master_key()
        except OSError as exc:
            raise SecretError(f"unable to read master key: {exc}") from exc
        if not payload:
            raise SecretError("master key file is empty")
        if len(payload) == KEY_LENGTH:
            return payload
        from novaprofile.security.crypto import _decode_key_file

        return _decode_key_file(payload)

    def save_master_key(self, key: bytes) -> None:
        """Persist *key* as the master key.

        The file is written atomically.  On Windows the key is DPAPI-protected
        before being written; elsewhere it is stored raw with restrictive
        permissions (see :func:`novaprofile.security.crypto.get_master_key`).
        """
        if not isinstance(key, (bytes, bytearray, memoryview)):
            raise SecretError("master key must be bytes")
        key = bytes(key)
        if len(key) != KEY_LENGTH:
            raise SecretError(f"master key must be {KEY_LENGTH} bytes, got {len(key)}")
        from novaprofile.security.crypto import _encode_key_file, _write_atomic

        path = self.key_path
        _write_atomic(path, _encode_key_file(key))
        with self._lock:
            self._master = key
            self._cache.clear()

    def rotate_master_key(self) -> bytes:
        """Generate a fresh master key, persist it and drop cached sub-keys.

        The previous key is overwritten, so secrets encrypted with it become
        unreadable unless the caller keeps an old copy.  Returns the new key.
        """
        import secrets as _secrets

        key = _secrets.token_bytes(KEY_LENGTH)
        self.save_master_key(key)
        return key

    # -- derived keys ----------------------------------------------------

    def derive_subkey(self, purpose: str) -> bytes:
        """Derive a 32-byte sub-key for *purpose* from the master key.

        The derivation is HKDF-SHA256 with a fixed salt and an ``info`` string
        of ``"novaprofile/security/keys/v1|<purpose>"``, so different purposes
        always yield different keys and the same purpose always yields the
        same key for a given master key.
        """
        normalized = _validate_purpose(purpose)
        with self._lock:
            cached = self._cache.get(normalized)
            if cached is not None:
                self._cache.move_to_end(normalized)
                return cached
            master = self.load_master_key()
            derived = HKDF(
                algorithm=hashes.SHA256(),
                length=KEY_LENGTH,
                salt=HKDF_SALT,
                info=f"novaprofile/security/keys/v1|{normalized}".encode(),
            ).derive(master)
            self._cache[normalized] = derived
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
            return derived

    def clear_cache(self) -> None:
        """Drop all cached derived sub-keys."""
        with self._lock:
            self._cache.clear()

    def forget_master_key(self) -> None:
        """Drop the in-memory master key and the derived-key cache.

        The persisted key file is left untouched.
        """
        with self._lock:
            self._master = None
            self._cache.clear()
