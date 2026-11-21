"""Security package: encryption at rest and key management.

Exposes the :mod:`novaprofile.security.crypto` primitives (AES-256-GCM
encryption with a DPAPI-protected master key on Windows) and the
:class:`novaprofile.security.keys.KeyManager` helper for purpose-scoped
sub-key derivation.
"""

from __future__ import annotations

from novaprofile.security.crypto import (
    SecretError,
    decrypt_secret,
    encrypt_secret,
    get_master_key,
    master_key_id,
    master_key_path,
)
from novaprofile.security.keys import InvalidPurposeError, KeyManager

__all__ = [
    "SecretError",
    "encrypt_secret",
    "decrypt_secret",
    "get_master_key",
    "master_key_id",
    "master_key_path",
    "KeyManager",
    "InvalidPurposeError",
]
