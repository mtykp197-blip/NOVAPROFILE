"""Tests for :mod:`novaprofile.security.crypto` and :mod:`novaprofile.security.keys`.

Every test is deterministic and never touches the real ``%LOCALAPPDATA%``
directory: ``NOVA_PROFILE_HOME`` is redirected into ``tmp_path`` before the
master key is first read.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from novaprofile.security import crypto
from novaprofile.security.crypto import (
    FORMAT_MAGIC,
    KEY_LENGTH,
    NONCE_LENGTH,
    SecretError,
    decrypt_secret,
    encrypt_secret,
    get_master_key,
)
from novaprofile.security.keys import InvalidPurposeError, KeyManager


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the NOVA data directory into ``tmp_path``."""
    home = tmp_path / "home"
    monkeypatch.setenv("NOVA_PROFILE_HOME", str(home))
    return home


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip() -> None:
    secret = "hunter2:p@ssw0rd! \u00e9\u4e2d\u6587"
    ciphertext = encrypt_secret(secret)
    assert ciphertext != secret
    assert decrypt_secret(ciphertext) == secret


def test_roundtrip_with_explicit_key() -> None:
    key = os.urandom(KEY_LENGTH)
    ciphertext = encrypt_secret("a secret", key=key)
    assert decrypt_secret(ciphertext, key=key) == "a secret"


def test_encrypt_is_non_deterministic() -> None:
    """A fresh nonce per call means the same plaintext yields different blobs."""
    first = encrypt_secret("same")
    second = encrypt_secret("same")
    assert first != second
    assert decrypt_secret(first) == decrypt_secret(second) == "same"


def test_empty_string_roundtrip() -> None:
    assert decrypt_secret(encrypt_secret("")) == ""


def test_unicode_roundtrip() -> None:
    payload = "\u00e9\u4e2d\u6587 \U0001f600 \t\n"
    assert decrypt_secret(encrypt_secret(payload)) == payload


# --------------------------------------------------------------------------
# Container format
# --------------------------------------------------------------------------


def test_ciphertext_format_layout() -> None:
    """The container is ``NOVA1 | version | key_id_len | key_id | nonce | ct+tag``."""
    ciphertext = encrypt_secret("payload")
    raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))

    assert raw[: len(FORMAT_MAGIC)] == FORMAT_MAGIC
    version = raw[len(FORMAT_MAGIC)]
    assert version == 1
    key_id_len = raw[len(FORMAT_MAGIC) + 1]
    assert key_id_len > 0
    key_id = raw[len(FORMAT_MAGIC) + 2 : len(FORMAT_MAGIC) + 2 + key_id_len]
    assert key_id.decode("utf-8") == "master"

    nonce_start = len(FORMAT_MAGIC) + 2 + key_id_len
    nonce = raw[nonce_start : nonce_start + NONCE_LENGTH]
    assert len(nonce) == NONCE_LENGTH
    sealed = raw[nonce_start + NONCE_LENGTH :]
    # AES-GCM tag is 16 bytes.
    assert len(sealed) == len("payload") + 16


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


def test_wrong_key_fails() -> None:
    ciphertext = encrypt_secret("top secret")
    with pytest.raises(SecretError):
        decrypt_secret(ciphertext, key=os.urandom(KEY_LENGTH))


def test_tampered_ciphertext_fails() -> None:
    ciphertext = encrypt_secret("top secret")
    raw = bytearray(base64.urlsafe_b64decode(ciphertext.encode("ascii")))

    # Flip a bit inside the ciphertext body, leaving the header intact.
    raw[-1] ^= 0x01
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(SecretError):
        decrypt_secret(tampered)


def test_tampered_nonce_fails() -> None:
    ciphertext = encrypt_secret("top secret")
    raw = bytearray(base64.urlsafe_b64decode(ciphertext.encode("ascii")))
    raw[len(FORMAT_MAGIC) + 3] ^= 0x40
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(SecretError):
        decrypt_secret(tampered)


def test_truncated_ciphertext_fails() -> None:
    ciphertext = encrypt_secret("top secret")
    with pytest.raises(SecretError):
        decrypt_secret(ciphertext[:20])


def test_non_base64_ciphertext_fails() -> None:
    with pytest.raises(SecretError):
        decrypt_secret("this is not base64 !!!")


def test_bad_magic_fails() -> None:
    raw = bytearray(base64.urlsafe_b64decode(encrypt_secret("x").encode("ascii")))
    raw[0] = ord("X")
    with pytest.raises(SecretError):
        decrypt_secret(base64.urlsafe_b64encode(bytes(raw)).decode("ascii"))


def test_wrong_key_length_rejected() -> None:
    with pytest.raises(SecretError):
        encrypt_secret("x", key=b"too short")
    with pytest.raises(SecretError):
        decrypt_secret(encrypt_secret("x"), key=b"too short")


def test_secret_error_message_has_no_plaintext() -> None:
    secret = "SUPERSECRETPLAINTEXT"
    ciphertext = encrypt_secret(secret)
    with pytest.raises(SecretError) as excinfo:
        decrypt_secret(ciphertext, key=os.urandom(KEY_LENGTH))
    assert secret not in str(excinfo.value)


# --------------------------------------------------------------------------
# Master key
# --------------------------------------------------------------------------


def test_get_master_key_is_idempotent() -> None:
    first = get_master_key()
    second = get_master_key()
    assert first == second
    assert len(first) == KEY_LENGTH


def test_master_key_persists_to_disk() -> None:
    key = get_master_key()
    path = crypto.master_key_path()
    assert path.exists()
    assert path.parent.name == "keys"

    # A fresh read from disk yields the same key.
    assert crypto._decode_key_file(path.read_bytes()) == key


def test_master_key_created_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "fresh-home"
    monkeypatch.setenv("NOVA_PROFILE_HOME", str(home))
    assert not crypto.master_key_path().exists()
    key = get_master_key()
    assert crypto.master_key_path().exists()
    assert len(key) == KEY_LENGTH


def test_concurrent_processes_share_one_master_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Racing processes must converge on a single key.

    The key file is created with an exclusive create so the *first* writer
    wins.  If the last writer were to win instead, different processes would
    encrypt with different keys and be unable to read each other's secrets.
    """
    home = tmp_path / "race-home"
    monkeypatch.setenv("NOVA_PROFILE_HOME", str(home))
    path = crypto.master_key_path()

    # Simulate the cross-process race: several independent candidates
    # attempt the exclusive create, and every loser must adopt the winner's
    # key rather than its own.
    candidates = [os.urandom(KEY_LENGTH) for _ in range(8)]
    winners: list[bytes] = []
    for candidate in candidates:
        payload = crypto._encode_key_file(candidate)
        try:
            if crypto._write_atomic(path, payload, exclusive=True):
                winners.append(candidate)
        except FileExistsError:
            winners.append(crypto._decode_key_file(path.read_bytes()))

    assert len(set(winners)) == 1
    assert get_master_key() == winners[0]

    # No temporary files may be left behind.
    leftovers = [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_master_key_file_is_not_plaintext_on_windows() -> None:
    """On Windows the key file must be DPAPI-protected, not raw bytes."""
    key = get_master_key()
    payload = crypto.master_key_path().read_bytes()
    if os.name == "nt":
        assert payload != key
    else:
        assert payload == key


# --------------------------------------------------------------------------
# KeyManager
# --------------------------------------------------------------------------


def test_key_manager_loads_same_master_key() -> None:
    manager = KeyManager()
    assert manager.load_master_key() == get_master_key()


def test_derived_subkeys_differ_per_purpose() -> None:
    manager = KeyManager()
    proxy = manager.derive_subkey("proxy.password")
    session = manager.derive_subkey("session.token")

    assert len(proxy) == KEY_LENGTH
    assert len(session) == KEY_LENGTH
    assert proxy != session


def test_derived_subkey_is_stable() -> None:
    manager = KeyManager()
    assert manager.derive_subkey("proxy.password") == manager.derive_subkey("proxy.password")


def test_derived_subkey_differs_between_managers_with_same_master() -> None:
    """Two managers sharing the same master key derive identical sub-keys."""
    first = KeyManager()
    second = KeyManager()
    assert first.derive_subkey("proxy.password") == second.derive_subkey("proxy.password")


def test_derived_subkey_changes_after_rotation() -> None:
    manager = KeyManager()
    before = manager.derive_subkey("proxy.password")
    manager.rotate_master_key()
    after = manager.derive_subkey("proxy.password")
    assert before != after


def test_rotate_master_key_persists() -> None:
    manager = KeyManager()
    rotated = manager.rotate_master_key()
    assert len(rotated) == KEY_LENGTH
    assert crypto.master_key_path().exists()
    assert crypto._decode_key_file(crypto.master_key_path().read_bytes()) == rotated


def test_purpose_must_be_namespaced() -> None:
    manager = KeyManager()
    with pytest.raises(InvalidPurposeError):
        manager.derive_subkey("password")


def test_purpose_rejects_invalid_characters() -> None:
    manager = KeyManager()
    with pytest.raises(InvalidPurposeError):
        manager.derive_subkey("proxy pass/word")
    with pytest.raises(InvalidPurposeError):
        manager.derive_subkey("")


def test_key_manager_cache_returns_same_object() -> None:
    manager = KeyManager()
    assert manager.derive_subkey("proxy.password") is manager.derive_subkey("proxy.password")


def test_key_manager_clear_cache_keeps_value() -> None:
    manager = KeyManager()
    before = manager.derive_subkey("proxy.password")
    manager.clear_cache()
    assert manager.derive_subkey("proxy.password") == before
