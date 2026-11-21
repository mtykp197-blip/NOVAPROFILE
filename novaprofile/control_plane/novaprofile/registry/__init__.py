"""Registry package: persistence for NOVA browser profiles.

Exposes :class:`~novaprofile.registry.store.ProfileStore`, the SQLite-backed
store for profiles and fingerprint signatures, together with the query helper
types in :mod:`novaprofile.registry.models`.
"""

from __future__ import annotations

from novaprofile.registry.models import PaginatedResult, ProfileFilter, SignatureRecord
from novaprofile.registry.store import (
    DuplicateProfileError,
    DuplicateSignatureError,
    ProfileNotFoundError,
    ProfileStore,
    StoreError,
    default_db_path,
)

__all__ = [
    "ProfileStore",
    "StoreError",
    "DuplicateProfileError",
    "DuplicateSignatureError",
    "ProfileNotFoundError",
    "default_db_path",
    "ProfileFilter",
    "PaginatedResult",
    "SignatureRecord",
]
