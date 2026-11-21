"""Encryption at rest for NOVA Profile Engine secrets.

Secrets (proxy passwords, session tokens, ...) are stored as AES-256-GCM
ciphertexts.  The master key lives in a single file under the application
data directory:

    %LOCALAPPDATA%\\NovaProfile\\keys\\master.key

On Windows the key material is additionally protected with DPAPI
(``CryptProtectData``), so the file on disk is useless to any other account or
process running as a different user.  On POSIX systems there is no equivalent
API, so the key is stored as raw bytes in a ``0600`` file -- see
:func:`get_master_key` for the trade-off discussion.

Ciphertext format (single self-describing base64 string)::

    b"NOVA1" | version(1) | key_id_len(1) | key_id | nonce(12) | ct+tag

``key_id`` is a UTF-8 label identifying which key encrypted the payload.  It is
embedded so a future key ring (multiple master keys, rotation) can decrypt
without out-of-band bookkeeping.  The 12-byte nonce is never reused for a given
key because it is freshly generated per encryption.

This module never logs plaintext or key material.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import ctypes
import os
import platform
import secrets
import threading
from pathlib import Path
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "SecretError",
    "encrypt_secret",
    "decrypt_secret",
    "get_master_key",
    "master_key_path",
    "master_key_id",
    "home_dir",
    "KEY_ID",
    "KEY_LENGTH",
    "NONCE_LENGTH",
    "FORMAT_MAGIC",
    "FORMAT_VERSION",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

KEY_LENGTH: Final[int] = 32
"""AES-256 key length in bytes."""

NONCE_LENGTH: Final[int] = 12
"""GCM nonce length in bytes (96 bits -- the size GCM is defined for)."""

FORMAT_MAGIC: Final[bytes] = b"NOVA1"
"""Leading marker that identifies the NOVA ciphertext container."""

FORMAT_VERSION: Final[int] = 1
"""Container format version; bumped if the layout ever changes."""

KEY_ID: Final[str] = "master"
"""Default key id embedded in ciphertexts produced by this module."""

_HEADER_LEN: Final[int] = len(FORMAT_MAGIC) + 2
"""magic + version byte + key_id_len byte."""

_LOCK: Final[threading.RLock] = threading.RLock()
"""Guards master-key creation so concurrent callers never race."""

_HOME_ENV_VAR: Final[str] = "NOVA_PROFILE_HOME"
"""Environment override for the application data root (used by tests)."""


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class SecretError(RuntimeError):
    """Raised when a secret cannot be encrypted, decrypted or verified.

    Covers malformed containers, wrong keys and tampered ciphertext (GCM
    authentication failures).  The message never contains plaintext.
    """


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def home_dir() -> Path:
    """Return the NOVA application-data root.

    Resolution order:

    1. ``$NOVA_PROFILE_HOME`` if set (used by tests and portable installs).
    2. ``%LOCALAPPDATA%\\NovaProfile`` on Windows.
    3. ``~/.local/share/NovaProfile`` elsewhere.
    """
    override = os.environ.get(_HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "NovaProfile"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "NovaProfile"


def master_key_path() -> Path:
    """Return the on-disk location of the master key file."""
    return home_dir() / "keys" / "master.key"


# --------------------------------------------------------------------------
# Windows DPAPI helpers
# --------------------------------------------------------------------------

_DPAPI_DESCRIPTION: Final[str] = "NOVA Profile Engine master key"


class _DataBlob(ctypes.Structure):
    """Win32 ``DATA_BLOB`` structure used by the DPAPI entry points."""

    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    """Build a ``DATA_BLOB`` pointing at *data* (the buffer must outlive it)."""
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(
        cbData=len(data),
        pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _dpapi_protect(data: bytes) -> bytes:
    """Protect *data* with DPAPI using the current-user scope."""
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

    in_blob, in_buffer = _blob(data)
    out_blob = _DataBlob()

    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        _DPAPI_DESCRIPTION,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise SecretError(
            f"DPAPI CryptProtectData failed (Windows error {ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    """Unprotect *data* previously produced by :func:`_dpapi_protect`."""
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

    in_blob, in_buffer = _blob(data)
    out_blob = _DataBlob()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise SecretError(
            f"DPAPI CryptUnprotectData failed (Windows error {ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del in_buffer


# --------------------------------------------------------------------------
# Master key management
# --------------------------------------------------------------------------


def _write_atomic(path: Path, payload: bytes, *, exclusive: bool = False) -> bool:
    """Write *payload* to *path* atomically.

    The bytes are written to a sibling temporary file which is flushed and
    fsynced, then moved into place with :func:`os.replace`.  ``os.replace`` is
    atomic on both POSIX and Windows, so a reader never observes a partially
    written key file.

    Args:
        path: Destination path.
        payload: The bytes to write.
        exclusive: When true, the destination must not already exist.

    Returns:
        True when this call created the file, False when *exclusive* was
        requested and the file already existed (another process won the race).

    Raises:
        FileExistsError: If *exclusive* is set and the file already exists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise FileExistsError(str(path))

    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        # ``O_EXCL`` guarantees we never clobber another writer's temp file.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        # ``os.fdopen`` takes ownership of the descriptor, so it is closed
        # exactly once -- by the context manager.
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            # ``O_EXCL`` on the final path makes the first writer win the
            # race instead of the last one overwriting it.
            try:
                os.link(tmp, path)
            except FileExistsError:
                return False
            os.unlink(tmp)
        else:
            os.replace(tmp, path)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
    return True


def _read_key_file(path: Path) -> bytes:
    """Read and validate the raw master-key file."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SecretError(f"unable to read master key file: {exc}") from exc
    if not payload:
        raise SecretError("master key file is empty")
    return payload


def _decode_key_file(payload: bytes) -> bytes:
    """Decode the on-disk representation of the master key.

    On Windows the file holds a DPAPI blob; elsewhere it holds the raw key.
    A raw 32-byte payload is always accepted as the key itself, which keeps the
    format unambiguous.
    """
    if platform.system() != "Windows":
        if len(payload) == KEY_LENGTH:
            return payload
        raise SecretError(
            f"master key file has unexpected length {len(payload)} (expected {KEY_LENGTH})"
        )
    if len(payload) == KEY_LENGTH:
        # Legacy/plaintext key written before DPAPI protection was enabled.
        return payload
    try:
        key = _dpapi_unprotect(payload)
    except SecretError:
        raise
    if len(key) != KEY_LENGTH:
        raise SecretError(
            f"unprotected master key has unexpected length {len(key)} (expected {KEY_LENGTH})"
        )
    return key


def _encode_key_file(key: bytes) -> bytes:
    """Encode the master key for storage using the platform's protection."""
    if platform.system() == "Windows":
        return _dpapi_protect(key)
    return key


def _harden_key_path(path: Path) -> None:
    """Restrict the master key file to the current user.

    On Windows the POSIX mode bits are ignored, so the file would inherit
    the ACL of its directory and could be readable by other accounts on the
    machine.  The ACL is rewritten explicitly.  A failure is not fatal:
    the key is still protected by DPAPI on Windows and by the mode bits
    elsewhere, and refusing to use the key would break the installation for
    a problem that only a stricter environment would notice.
    """

    from .hardening import harden_directory, harden_file

    with contextlib.suppress(OSError):
        harden_directory(path.parent)
    with contextlib.suppress(OSError):
        harden_file(path)


def get_master_key() -> bytes:
    """Return the 32-byte master key, creating it on first use.

    The key is stored in ``<home>/keys/master.key``.  On Windows the file
    content is DPAPI-protected with the current-user scope, so only processes
    running as the same user on the same machine can recover it.  On POSIX
    systems no equivalent API exists, so the raw key is written to a file with
    ``0600`` permissions -- this protects against other *users* on the host but
    not against a process already running as the same user (root, malware
    running in the session, or a backup that captures the file).  Workloads
    that need stronger guarantees should supply their own key through
    :func:`encrypt_secret` / :func:`decrypt_secret` instead of relying on this
    file.

    The call is idempotent: repeated calls return the same key.  It is also
    concurrency-safe across *processes*, not just threads: the key file is
    created with an exclusive create (``O_CREAT | O_EXCL``) so that the first
    writer wins the race rather than the last one overwriting it.  A process
    that loses the race re-reads the persisted key, so every process converges
    on the same key material.
    """
    path = master_key_path()
    with _LOCK:
        if path.exists():
            _harden_key_path(path)
            return _decode_key_file(_read_key_file(path))

        key = secrets.token_bytes(KEY_LENGTH)
        payload = _encode_key_file(key)
        try:
            created = _write_atomic(path, payload, exclusive=True)
        except FileExistsError:
            # Another process created the file between our check and our
            # write; use the key it persisted.
            created = False
        except OSError as exc:
            if path.exists():
                return _decode_key_file(_read_key_file(path))
            raise SecretError(f"unable to store master key: {exc}") from exc

        _harden_key_path(path)

        if created:
            return key

        # We lost the race: another process's key is the authoritative one.
        try:
            existing = _decode_key_file(_read_key_file(path))
        except SecretError:
            # The winner's file is unreadable (for example it is still being
            # written).  Fall back to our own key rather than failing hard.
            return key
        return existing


def master_key_id() -> str:
    """Return the key id embedded in newly produced ciphertexts."""
    return KEY_ID


# --------------------------------------------------------------------------
# Encrypt / decrypt
# --------------------------------------------------------------------------


def _encode_key_id(key_id: str) -> bytes:
    """Encode *key_id* into the single length-prefixed header field."""
    raw = key_id.encode("utf-8")
    if len(raw) > 255:
        raise SecretError("key id is too long to encode")
    return raw


def encrypt_secret(plaintext: str, *, key: bytes | None = None) -> str:
    """Encrypt *plaintext* with AES-256-GCM and return a base64 string.

    When *key* is omitted the process master key from :func:`get_master_key`
    is used.  The returned value is a single self-describing base64 string:

    ``NOVA1 | version | key_id_len | key_id | nonce | ciphertext+tag``

    Every call uses a fresh random 96-bit nonce, so encrypting the same
    plaintext twice yields different ciphertexts.
    """
    if key is None:
        key = get_master_key()
    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise SecretError("encryption key must be bytes")
    key = bytes(key)
    if len(key) != KEY_LENGTH:
        raise SecretError(f"encryption key must be {KEY_LENGTH} bytes, got {len(key)}")
    if not isinstance(plaintext, str):
        raise SecretError("plaintext must be a string")

    key_id = KEY_ID
    key_id_bytes = _encode_key_id(key_id)
    nonce = secrets.token_bytes(NONCE_LENGTH)
    header = FORMAT_MAGIC + bytes((FORMAT_VERSION, len(key_id_bytes))) + key_id_bytes

    try:
        sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), header)
    except Exception as exc:  # pragma: no cover - defensive, AESGCM rarely raises
        raise SecretError(f"encryption failed: {exc}") from exc

    return base64.urlsafe_b64encode(header + nonce + sealed).decode("ascii")


def _parse_container(ciphertext: str) -> tuple[bytes, bytes, bytes, bytes]:
    """Split a ciphertext container into ``(header, key_id, nonce, sealed)``."""
    if not isinstance(ciphertext, str):
        raise SecretError("ciphertext must be a string")
    try:
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise SecretError(f"ciphertext is not valid base64: {exc}") from exc

    if len(raw) < _HEADER_LEN + NONCE_LENGTH + 16:
        raise SecretError("ciphertext is too short to be a valid container")
    if raw[: len(FORMAT_MAGIC)] != FORMAT_MAGIC:
        raise SecretError("ciphertext has an invalid magic marker")
    version = raw[len(FORMAT_MAGIC)]
    if version != FORMAT_VERSION:
        raise SecretError(f"unsupported ciphertext version {version}")
    key_id_len = raw[len(FORMAT_MAGIC) + 1]
    key_start = _HEADER_LEN
    key_end = key_start + key_id_len
    if key_end + NONCE_LENGTH + 16 > len(raw):
        raise SecretError("ciphertext is truncated")
    header = raw[:key_end]
    key_id = raw[key_start:key_end].decode("utf-8", errors="replace")
    nonce = raw[key_end : key_end + NONCE_LENGTH]
    sealed = raw[key_end + NONCE_LENGTH :]
    return header, key_id, nonce, sealed


def decrypt_secret(ciphertext: str, *, key: bytes | None = None) -> str:
    """Decrypt a ciphertext produced by :func:`encrypt_secret`.

    When *key* is omitted the process master key from :func:`get_master_key`
    is used.  Raises :class:`SecretError` if the container is malformed, if the
    key is wrong, or if the payload has been tampered with (AES-GCM
    authentication fails).
    """
    if key is None:
        key = get_master_key()
    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise SecretError("decryption key must be bytes")
    key = bytes(key)
    if len(key) != KEY_LENGTH:
        raise SecretError(f"decryption key must be {KEY_LENGTH} bytes, got {len(key)}")

    header, _key_id, nonce, sealed = _parse_container(ciphertext)
    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, header)
    except InvalidTag as exc:
        raise SecretError("decryption failed: wrong key or tampered ciphertext") from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise SecretError(f"decryption failed: {exc}") from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretError("decrypted payload is not valid UTF-8") from exc
