"""The licence subsystem of NOVA PROFILE.

A commercial build of the engine is licensed, not sold: a machine runs
the product when a licence server says that the machine may run it, and
the server's answer is a *signed lease* that the client verifies
offline.  The verification is the part that matters.  A lease that the
client could edit would not be a licence at all, so the lease carries
an Ed25519 signature made with a key that never leaves the server, and
the client holds only the public half.

The pieces fit together like this:

* a **device key** -- an Ed25519 key pair generated on the machine and
  protected with the Windows DPAPI, so that the private half cannot be
  lifted off the disk and used on another machine;
* a **challenge** -- a nonce the server hands out, which stops a
  captured request from being replayed;
* a **lease** -- a signed token with the claims the client needs
  (``exp``, ``license_exp``, the device, the account), which the client
  verifies and then seals with the DPAPI for the next start.

What the client *cannot* do is decide for itself that it is licensed.
The lease is signed by the server and the client refuses to work
without a valid one, and it checks the lease against the server on
every call that would create a fingerprint -- so a machine whose
licence has been withdrawn stops being able to make new ones, and a
machine that has no network keeps the lease it already had until the
lease itself expires.

The policy file that configures all of this lives beside the package as
``commercial-policy.json``.  A deployment without that file runs the
product without a licence requirement, which is what the open build
does.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

__all__ = [
    "CommercialClient",
    "LicenseError",
    "LicenseRejected",
    "LicenseRequired",
    "LicenseStatus",
    "ServerBusy",
    "configured_client",
    "default_policy_path",
    "digest",
]

POLICY_FILENAME = "commercial-policy.json"
MAX_TOKEN_LENGTH = 4096
MAX_SESSION_LENGTH = 16384


class LicenseError(RuntimeError):
    """Base class for the licence errors."""


class LicenseRequiredError(LicenseError):
    """The product needs a licence that this machine does not have."""


class LicenseRejectedError(LicenseError):
    """The licence server refused this machine's lease."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ServerBusyError(LicenseError):
    """The licence server cannot take another call right now."""


#: The historical names of the errors, kept so that older callers work.
LicenseRequired = LicenseRequiredError
LicenseRejected = LicenseRejectedError
ServerBusy = ServerBusyError


def digest(value: str) -> str:
    """The SHA-256 of ``value``, as hexadecimal."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64(value: bytes) -> str:
    """Base64url without the padding."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    """The inverse of :func:`_b64`."""

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class _Blob(ctypes.Structure):
    """The ``DATA_BLOB`` structure of the Windows cryptography API."""

    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def windows_protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Protect or unprotect ``data`` with the current user's DPAPI.

    The DPAPI binds the blob to the Windows user account, so the
    material cannot be read by another account on the same machine and
    cannot be moved to another machine.  It is *not* protection against
    code that runs as the user, and it is not TPM storage -- the
    difference matters, so the docstring says so rather than implying
    more than the API gives.

    On a platform without the Windows cryptography API the data is
    returned unchanged.  That is a deliberate weakening rather than a
    crash: a developer on Linux should still be able to run the engine.
    """

    if os.name != "nt":
        return data
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
        )
    if not ok:
        raise LicenseError(
            "the Windows cryptography API refused the request "
            f"(error {ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        with contextlib.suppress(Exception):
            kernel.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


class _NoRedirect(HTTPRedirectHandler):
    """A redirect handler that refuses to redirect.

    A licence call that is redirected is a call that went somewhere the
    policy did not name, and the answer it gets back is not the
    answer of the licence server.  The redirect is therefore refused
    rather than followed.
    """

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class LicenseStatus:
    """What the client knows about the licence."""

    required: bool
    active: bool
    device_id: str = ""
    key_provider: str = ""
    telegram_user_id: str | None = None
    owner_registration: bool = False
    expires: float | None = None


class CommercialClient:
    """The client of the licence server.

    The client holds a device key, asks the server for a lease and
    verifies the lease it gets back.  It never decides on its own that
    the product may run.
    """

    QUEUE_ATTEMPTS = 8
    QUEUE_BASE_WAIT = 1.0
    QUEUE_MAX_WAIT = 15.0

    def __init__(
        self,
        config: dict[str, Any],
        root: str | Path,
        *,
        transport: Any = None,
        protect: Any = windows_protect,
        clock: Any = time.time,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._clock = clock
        self._monotonic = monotonic
        self._server_clock: tuple[float, float] | None = None
        self._lock = threading.RLock()
        self._root = Path(root)
        self._protect = protect
        self._server = str(config["server_url"]).rstrip("/")
        self._public = _load_public_key(config.get("signing_public_key", ""))
        self._transport = transport if transport is not None else self._http_transport

        provider = str(config.get("device_key_provider", "dpapi"))
        if provider not in {"dpapi", "tpm"}:
            raise LicenseRequiredError("the policy names an unknown device key provider")
        self._key_provider = provider
        self._core_manifest = config.get("core_manifest")

        self._private, self._device = self._load_or_create_device_key()
        self._token: str | None = None
        self._claims: dict[str, Any] = {}
        self._pending_login: dict[str, Any] | None = None
        self._telegram_ticket: str | None = None
        self._telegram_user_id: str | None = None

    # -- the clock --------------------------------------------------------

    def license_time(self) -> float:
        """The time the licence is measured against.

        The server's ``Date`` header is the authority: the local clock
        can be moved, and a licence that expires must not be extended by
        moving the clock of the machine that holds it.  When the server
        has been seen, its clock is used; the anchor is kept in memory
        and is never written to disk.
        """

        if self._server_clock is None:
            return float(self._clock())
        epoch, started = self._server_clock
        return epoch + max(0.0, self._monotonic() - started)

    def sync_server_clock(self, date_header: str | None, elapsed: float = 0.0) -> None:
        """Anchor the clock to the server's ``Date`` header."""

        if not date_header:
            return
        try:
            stamp = parsedate_to_datetime(date_header)
            if stamp.tzinfo is None:
                return
            self._server_clock = (
                stamp.timestamp() + max(0.0, elapsed),
                self._monotonic(),
            )
        except (TypeError, ValueError, OverflowError):
            return

    # -- the transport ----------------------------------------------------

    def _http_transport(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        """The default transport: an HTTP POST to the licence server."""

        started = self._monotonic()
        request = Request(
            f"{self._server}{route}",
            data=json.dumps(body, separators=(",", ":")).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "NOVA-Profiles/0.5",
            },
            method="POST",
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=12) as response:
                raw = response.read(MAX_SESSION_LENGTH + 1)
                if len(raw) > MAX_SESSION_LENGTH:
                    raise LicenseRequiredError("the licence server's reply is too large")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise LicenseRequiredError("the licence server's reply is not readable")
                self.sync_server_clock(
                    response.headers.get("Date"), self._monotonic() - started
                )
                return result
        except HTTPError as exc:
            code: str | None = None
            reason: str | None = None
            try:
                value = json.loads(exc.read(4096))
                if value.get("error"):
                    code = str(value["error"])[:120]
            except Exception:  # pragma: no cover - the body may not be JSON
                pass
            if exc.code == 429:
                raise ServerBusyError("the licence server is busy, waiting its turn") from exc
            if exc.code in (400, 401, 403) and code is not None:
                raise LicenseRejectedError(
                    f"the licence server refused this machine ({code})", code
                ) from exc
            raise LicenseRequiredError(
                "the licence server did not answer; please try again"
            ) from exc
        except LicenseError:
            raise
        except (URLError, OSError, ValueError) as exc:
            raise LicenseRequiredError(
                "the licence server could not be reached"
            ) from exc

    def _post_with_queue(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        """Call the server and wait its turn when the server is busy.

        Many tabs of one machine ask the server at the same time.  When
        the server says that it is full, the call waits a little and
        tries again, and the wait grows, so the calls do not come back
        together and block each other again.
        """

        attempt = 0
        while True:
            attempt += 1
            try:
                return self._transport(route, body)
            except ServerBusyError:
                if attempt >= self.QUEUE_ATTEMPTS:
                    raise LicenseRequiredError(
                        "the licence server is still busy; please try again"
                    ) from None
                wait = min(self.QUEUE_MAX_WAIT, self.QUEUE_BASE_WAIT * attempt)
                time.sleep(wait)

    # -- the device key ---------------------------------------------------

    def _device_key_path(self) -> Path:
        return self._root / "device-key.dpapi"

    def _load_or_create_device_key(self) -> tuple[Ed25519PrivateKey, str]:
        """Load the device key, or make one and store it."""

        path = self._device_key_path()
        if path.is_file():
            try:
                sealed = path.read_bytes()
                if len(sealed) > MAX_SESSION_LENGTH:
                    raise ValueError("the sealed device key is too large")
                raw = self._protect(sealed, decrypt=True)
                private = Ed25519PrivateKey.from_private_bytes(raw)
                return private, self._device_id(private)
            except Exception:
                with contextlib.suppress(OSError):
                    path.unlink()

        private = Ed25519PrivateKey.generate()
        raw = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._root.mkdir(parents=True, exist_ok=True)
        sealed = self._protect(raw)
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(sealed)
            os.replace(temporary, path)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink()
            # Another process won the race: read the key it wrote.
            sealed = path.read_bytes()
            private = Ed25519PrivateKey.from_private_bytes(
                self._protect(sealed, decrypt=True)
            )
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return private, self._device_id(private)

    def _device_id(self, private: Ed25519PrivateKey) -> str:
        public = private.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(public).hexdigest()

    @property
    def device(self) -> str:
        """The identifier of this machine."""

        return self._device

    def _public_pem(self) -> str:
        """The device's public key, as PEM."""

        return (
            self._private.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )

    # -- the signed proofs ------------------------------------------------

    def signed(self, action: str, binding: str) -> dict[str, Any]:
        """Build the signed proof of one action.

        The proof covers the action, the nonce and the binding, so it
        cannot be reused for another action or for another request.
        """

        challenge = self._transport("/v1/challenge", {"public_key": self._public_pem()})
        nonce = challenge.get("nonce")
        if not isinstance(nonce, str) or len(nonce) > 100:
            raise LicenseRejectedError("the challenge is not valid")
        message = f"NOVA/1\n{action}\n{nonce}\n{binding}".encode()
        proof = _b64(self._private.sign(message))
        return {
            "public_key": self._public_pem(),
            "nonce": nonce,
            "proof": proof,
        }

    def accept(self, result: dict[str, Any]) -> LicenseStatus:
        """Verify a lease and keep it."""

        token = result.get("token")
        if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
            raise LicenseRejectedError("the lease is not valid")
        try:
            encoded, signature = token.split(".")
        except ValueError as exc:
            raise LicenseRejectedError("the lease is not valid") from exc

        try:
            self._public.verify(_unb64(signature), encoded.encode("ascii"))
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise LicenseRejectedError("the lease's signature does not verify") from exc

        try:
            claims = json.loads(_unb64(encoded).decode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise LicenseRejectedError("the lease's claims are not readable") from exc
        if not isinstance(claims, dict):
            raise LicenseRejectedError("the lease's claims are not readable")
        if claims.get("device") != self._device:
            raise LicenseRejectedError("the lease belongs to another device")

        self._token = token
        self._claims = claims
        self._save_session()
        return self.status()

    # -- the session ------------------------------------------------------

    def _session_path(self) -> Path:
        return self._root / "session.dpapi"

    def _save_session(self) -> None:
        """Store the lease, sealed with the DPAPI.

        Only the signed lease is stored -- never a key and never a login
        ticket -- so the file on the disk is not worth anything to
        anybody who copies it.
        """

        if self._token is None:
            return
        payload = json.dumps(
            {
                "server": self._server,
                "device": self._device,
                "token": self._token,
            }
        ).encode("utf-8")
        sealed = self._protect(payload)
        target = self._session_path()
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(sealed)
            os.replace(temporary, target)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()

    def forget_session(self) -> None:
        """Drop the stored lease."""

        with self._lock:
            self._token = None
            self._claims = {}
            with contextlib.suppress(OSError):
                self._session_path().unlink(missing_ok=True)

    def _restore_session(self) -> bool:
        """Read the stored lease back, if there is one."""

        target = self._session_path()
        if not target.is_file():
            return False
        try:
            sealed = target.read_bytes()
            if len(sealed) > MAX_SESSION_LENGTH:
                raise ValueError("the sealed session is too large")
            payload = json.loads(self._protect(sealed, decrypt=True).decode("utf-8"))
            if payload.get("device") != self._device:
                raise ValueError("the session belongs to another device")
            if payload.get("server") != self._server:
                raise ValueError("the session belongs to another server")
            token = payload["token"]
        except Exception:
            self.forget_session()
            return False
        try:
            self.accept({"token": token})
            return True
        except LicenseRejectedError:
            self.forget_session()
            return False
        except Exception:
            self._token = None
            self._claims = {}
            return False

    def _restore_device(self) -> bool:
        """Ask the server to authorise this device again."""

        try:
            self.accept(
                self._transport(
                    "/v1/device-login",
                    self.signed("device-login", digest("NOVA saved device login")),
                )
            )
            return True
        except LicenseRejectedError:
            self.forget_session()
            return False
        except Exception:
            self._token = None
            self._claims = {}
            return False

    def restore_login(self) -> LicenseStatus:
        """Return the current status, restoring the lease when needed."""

        with self._lock:
            if self.status().active:
                return self.status()
            if not self._restore_session():
                self._restore_device()
            return self.status()

    # -- the public operations --------------------------------------------

    def require(self) -> LicenseStatus:
        """Make sure the product may run, and return the status.

        A network failure is *not* a licence.  When the server cannot
        be reached the lease that is already held is kept, so a machine
        that loses its connection keeps working until the lease itself
        expires -- but a machine that never had one still cannot run.
        """

        with self._lock:
            if not self._token:
                if not self._restore_session() and not self._restore_device():
                    raise LicenseRequiredError(
                        "a licence is required and this machine does not have one"
                    )
            return self.status()

    def reserve(
        self,
        signature: str,
        pid: str,
        groups: dict[str, Any] | None = None,
        legacy_signature: str | None = None,
    ) -> bool:
        """Ask the server whether a fingerprint may be reserved.

        The reservation is the anti-collision guarantee: the server
        knows which signatures the whole fleet has used and refuses one
        that has been used before.  A client that cannot reach the
        server cannot make a *new* reservation, which is what keeps a
        machine from quietly farming duplicates while it is offline.
        """

        with self._lock:
            self.require()
            token = self._token or ""
            if groups is None:
                binding = digest(f"{token}\n{signature}\n{pid}")
                request: dict[str, Any] = {
                    "token": token,
                    "signature": signature,
                    "profile_id": pid,
                }
            else:
                groups_json = json.dumps(groups, sort_keys=True, separators=(",", ":"))
                legacy_signature = legacy_signature or ""
                binding = digest(
                    f"{token}\n{signature}\n{pid}\n{groups_json}\n{legacy_signature}"
                )
                request = {
                    "token": token,
                    "signature": signature,
                    "profile_id": pid,
                    "schema_version": "2",
                    "groups_json": groups_json,
                    "legacy_signature": legacy_signature,
                }
            request.update(self.signed("reserve", binding))
            result = self._transport("/v1/fingerprints/reserve", request)
            if type(result.get("accepted")) is not bool:
                raise LicenseRequiredError("the anti-collision answer is not readable")
            return bool(result["accepted"])

    def device_catalog(self) -> dict[str, Any]:
        """Fetch the device catalogue the server publishes."""

        with self._lock:
            self.require()
            token = self._token or ""
            request = {"token": token}
            request.update(self.signed("catalog", digest(token)))
            return self._transport("/v1/catalog", request)

    def app_update(self) -> dict[str, Any]:
        """Ask the server which version of the product is current."""

        with self._lock:
            self.require()
            token = self._token or ""
            request = {"token": token}
            request.update(self.signed("catalog", digest(token)))
            return self._transport("/v1/app-update", request)

    def status(self) -> LicenseStatus:
        """What the client knows about the licence."""

        now = self.license_time()
        claims = self._claims or {}
        try:
            license_exp = float(claims.get("license_exp", 0) or 0)
        except (TypeError, ValueError):
            license_exp = 0.0
        try:
            token_exp = float(claims.get("exp", 0) or 0)
        except (TypeError, ValueError):
            token_exp = 0.0
        active = bool(self._token) and token_exp > now and license_exp > now
        return LicenseStatus(
            required=True,
            active=active,
            device_id=self._device,
            key_provider=self._key_provider,
            telegram_user_id=claims.get("telegram_user_id"),
            owner_registration=claims.get("owner_registration") is True,
            expires=license_exp or None,
        )


def _load_public_key(pem: str) -> Ed25519PublicKey:
    """Load the server's public key from its PEM form."""

    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise LicenseError("the policy's signing public key is not valid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("the policy's signing public key is not an Ed25519 key")
    return key


def default_policy_path(base: str | Path | None = None) -> Path:
    """Where the policy file is looked for."""

    if base is None:
        base = Path(__file__).resolve().parent.parent.parent
    return Path(base) / POLICY_FILENAME


def configured_client(
    base: str | Path | None = None,
    root: str | Path | None = None,
    *,
    required: bool = False,
) -> CommercialClient | None:
    """Build a client from the policy file, if there is one.

    A deployment without a policy file runs the product without a
    licence requirement.  A deployment that *requires* a licence and
    has no policy file cannot start, and says so rather than starting
    unlocked.
    """

    config_path = default_policy_path(base)
    if not config_path.exists():
        if required:
            raise LicenseRequiredError(
                "the commercial configuration is missing and the product cannot start"
            )
        return None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if root is None:
        root = Path(config_path).parent / "license"
    return CommercialClient(config, Path(root))
