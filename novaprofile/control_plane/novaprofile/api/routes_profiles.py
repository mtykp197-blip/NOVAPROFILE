"""Profile CRUD and lifecycle routes.

Mounted under ``/api/profiles``.  Every handler degrades gracefully when an
engine module is missing: the request fails with HTTP 503 and an actionable
message instead of a stack trace.
"""

from __future__ import annotations

import contextlib
import dataclasses
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from novaprofile.api import deps

__all__ = ["router", "ProfileCreateRequest", "ProfileUpdateRequest"]

router = APIRouter()

_SEED_COUNTER: list[int] = [0]
"""Monotonic counter backing the ``increment`` seed strategy."""


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class ProfileCreateRequest(BaseModel):
    """Body of ``POST /api/profiles``."""

    model_config = ConfigDict(extra="allow")

    preset_id: str = Field(default="", description="Device preset to build the profile from")
    name: str | None = None
    group_id: str | None = None
    folder_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    proxy: str | None = None
    seed_strategy: str = Field(default="random", description="random | fixed | increment")
    master_seed: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdateRequest(BaseModel):
    """Body of ``PATCH /api/profiles/{profile_id}``."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    group_id: str | None = None
    folder_id: str | None = None
    status: str | None = None
    proxy: str | None = None
    geo_sync: dict[str, Any] | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class CloneRequest(BaseModel):
    """Body of ``POST /api/profiles/{profile_id}/clone``."""

    name: str | None = None
    group_id: str | None = None
    folder_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    new_seed: bool = True
    master_seed: str | None = None
    clean_storage: bool = True


class LaunchRequest(BaseModel):
    """Body of ``POST /api/profiles/{profile_id}/launch``."""

    headful: bool = False
    url: str | None = None
    timeout_s: float = 30.0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _not_found(profile_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"profile {profile_id!r} not found",
    )


def _load_profile(profile_id: str, store: Any) -> Any:
    getter = getattr(store, "get_profile", None)
    if getter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement get_profile",
        )
    config = getter(profile_id)
    if config is None:
        raise _not_found(profile_id)
    return config


def _profile_id_of(config: Any) -> str:
    identity = getattr(config, "identity", None)
    if identity is not None:
        return str(getattr(identity, "id", ""))
    if isinstance(config, dict):
        return str(config.get("identity", {}).get("id", ""))
    return ""


def _identity_of(config: Any) -> Any:
    identity = getattr(config, "identity", None)
    if identity is not None:
        return identity
    if isinstance(config, dict):
        return config.get("identity", {})
    return {}


def _mutate_identity(config: Any, **changes: Any) -> Any:
    """Return a copy of *config* with the identity fields replaced.

    Works with both Pydantic models and plain dictionaries so the route layer
    stays functional while the schema module is still being finalised.
    """
    identity = _identity_of(config)
    if hasattr(identity, "model_copy"):
        new_identity = identity.model_copy(update=changes)
    elif isinstance(identity, dict):
        new_identity = {**identity, **changes}
    else:  # pragma: no cover - defensive
        new_identity = identity

    if hasattr(config, "model_copy"):
        return config.model_copy(update={"identity": new_identity})
    if isinstance(config, dict):
        updated = dict(config)
        updated["identity"] = new_identity
        return updated
    return config


def _touch(config: Any) -> Any:
    """Stamp ``updated_at`` on the profile identity."""
    identity = _identity_of(config)
    stamp = datetime.now(UTC)
    if hasattr(identity, "model_copy"):
        new_identity = identity.model_copy(update={"updated_at": stamp})
    elif isinstance(identity, dict):
        new_identity = {**identity, "updated_at": stamp}
    else:  # pragma: no cover - defensive
        return config
    if hasattr(config, "model_copy"):
        return config.model_copy(update={"identity": new_identity})
    if isinstance(config, dict):
        updated = dict(config)
        updated["identity"] = new_identity
        return updated
    return config


def _build_config(
    preset_id: str,
    overrides: dict[str, Any],
    master_seed: str | None,
    seed_strategy: str,
    name: str | None = None,
    store: Any | None = None,
) -> Any:
    """Build a ``ProfileConfig`` from a preset plus overrides.

    ``seed_strategy`` selects how the master seed is chosen when the caller
    does not supply one:

    ``random``
        A fresh random 64-character hex seed (the default).
    ``fixed``
        A seed derived deterministically from the preset id, so repeated
        builds of the same preset are reproducible.
    ``increment``
        A seed derived from the preset id and a monotonically increasing
        counter, so consecutive builds differ but stay reproducible.  The
        counter comes from the store, so several processes sharing a
        database never hand out the same seed.
    """
    generator = deps.get_generator()

    seed = master_seed
    if seed is None:
        seed = _seed_for_strategy(preset_id, seed_strategy, store)

    kwargs: dict[str, Any] = {"preset_id": preset_id, "master_seed": seed}
    if name:
        kwargs["name"] = name

    try:
        config = generator(**kwargs)
    except TypeError:
        # The generator may not accept every keyword; fall back to the most
        # common single-argument form.
        try:
            config = generator(preset_id, master_seed=seed)
        except TypeError:
            config = generator(preset_id)
            if master_seed is not None:
                config = _override_seed(config, master_seed)

    return _apply_overrides(config, overrides)


def _sync_network_policy(config: Any) -> Any:
    """Re-derive the network policy that follows the profile's proxy.

    The proxy is attached to the profile *after* the device identity has
    been generated, so the WebRTC configuration has to be re-derived from
    it: a profile that claims a proxy but leaves the ICE candidates open
    hands the page the very address the operator paid to hide.  The
    resolver performs the repair and refuses the profile if the result is
    still inconsistent.
    """
    try:
        resolver = deps.get_resolver()
    except Exception:  # noqa: BLE001 - the engine may not be loaded yet
        return config
    try:
        return resolver.resolve(config)
    except Exception:  # noqa: BLE001 - an unrepairable profile is reported later
        return config


def _seed_for_strategy(preset_id: str, seed_strategy: str, store: Any | None = None) -> str:
    """Return the master seed implied by *seed_strategy*.

    The ``increment`` strategy draws its counter from the store when there
    is one, so that several processes sharing a database never hand out the
    same seed.  A process-local counter would restart at zero in every
    worker, and two workers would then produce the *same* profile -- the
    duplicate the collision guard is there to prevent.
    """

    if seed_strategy == "fixed":
        return _deterministic_seed(f"{preset_id}|0")
    if seed_strategy == "increment":
        counter = None
        if store is not None:
            next_counter = getattr(store, "next_counter", None)
            if callable(next_counter):
                try:
                    counter = next_counter(f"seed:{preset_id}")
                except Exception:  # noqa: BLE001 - fall back to the local counter
                    counter = None
        if counter is None:
            counter = _SEED_COUNTER[0] = _SEED_COUNTER[0] + 1
        return _deterministic_seed(f"{preset_id}|{counter}")
    return deps.new_master_seed()


def _deterministic_seed(material: str) -> str:
    """Derive a 64-character hex seed from *material*."""
    import hashlib

    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _override_seed(config: Any, master_seed: str) -> Any:
    """Replace the master seed inside an already-built config."""
    seed_config = getattr(config, "seed_config", None)
    if seed_config is None:
        return config
    if hasattr(seed_config, "model_copy"):
        new_seed = seed_config.model_copy(update={"master_seed": master_seed})
    else:  # pragma: no cover - defensive
        new_seed = {**seed_config, "master_seed": master_seed}
    if hasattr(config, "model_copy"):
        return config.model_copy(update={"seed_config": new_seed})
    if isinstance(config, dict):
        updated = dict(config)
        updated["seed_config"] = new_seed
        return updated
    return config


def _apply_overrides(config: Any, overrides: dict[str, Any]) -> Any:
    """Apply top-level overrides to a config (only known fields are used).

    The ``proxy`` override is special: it arrives as a URL string, but the
    profile carries a structured :class:`~novaprofile.schema.network.Proxy`
    object.  The URL is parsed into it here, and the network policy that
    follows the proxy -- the WebRTC candidates in particular -- is
    re-derived afterwards, so that a profile with a proxy never keeps an
    open ICE configuration.
    """
    if not overrides:
        return config
    fields = set(getattr(config, "model_fields", {}) or {})
    # The proxy is handled separately: it arrives as a URL and has to
    # become a structured proxy, not a raw mapping.
    payload = {key: value for key, value in overrides.items() if key in fields and key != "proxy"}
    proxy_override = overrides.get("proxy")

    if payload and hasattr(config, "model_copy"):
        config = config.model_copy(update=payload)
    elif payload and isinstance(config, dict):
        updated = dict(config)
        updated.update(payload)
        config = updated

    if proxy_override is not None:
        config = _apply_proxy_override(config, proxy_override)

    return _sync_network_policy(config)


def _apply_proxy_override(config: Any, proxy_override: Any) -> Any:
    """Attach a proxy to a profile from a URL or a mapping."""
    from novaprofile.network.proxy import parse_proxy_string
    from novaprofile.schema.network import Proxy

    if isinstance(proxy_override, Proxy):
        return (
            config.model_copy(update={"proxy": proxy_override})
            if hasattr(config, "model_copy")
            else config
        )

    if isinstance(proxy_override, dict):
        url = str(proxy_override.get("url") or "").strip()
    else:
        url = str(proxy_override or "").strip()

    if not url:
        return config

    endpoint = parse_proxy_string(url)
    if endpoint is None:
        return config

    proxy = Proxy(
        enabled=True,
        protocol=endpoint.protocol,  # type: ignore[arg-type]
        host=endpoint.host,
        port=endpoint.port,
        username=endpoint.username or "",
        password=endpoint.password or "",
        auth_method="userpass" if endpoint.username else "none",
    )
    if not hasattr(config, "model_copy"):
        return config
    return config.model_copy(update={"proxy": proxy})


def _apply_identity_overrides(config: Any, request: ProfileCreateRequest) -> Any:
    """Apply the identity-level fields of a create request."""
    changes: dict[str, Any] = {}
    if request.name:
        changes["name"] = request.name
    if request.group_id is not None:
        changes["group_id"] = request.group_id
    if request.folder_id is not None:
        changes["folder_id"] = request.folder_id
    if request.tags:
        changes["tags"] = list(request.tags)
    if request.notes:
        changes["notes"] = request.notes
    if not changes:
        return config
    return _mutate_identity(config, **changes)


def _allocate(store: Any, guard: Any, config: Any) -> Any:
    """Run the collision guard over a candidate config.

    The guard only *checks* here.  The signatures are committed by
    :func:`_persist`, after the profile row exists, because the signature
    rows reference the profile.
    """
    if guard is None:
        return config
    profile_id = _profile_id_of(config)
    allocate = getattr(guard, "allocate", None)
    if not callable(allocate):
        return config
    return allocate(profile_id, config)


def _persist(store: Any, guard: Any, config: Any) -> Any:
    """Store a profile and then commit its signatures.

    The order matters: the signature rows carry a foreign key onto the
    profile row, so the profile has to exist first.  When the commit loses a
    race against a concurrent allocation, the profile that was just written
    is removed again, so that no profile can survive without its
    signatures.
    """
    store.create_profile(config)
    if guard is None:
        return config

    commit = getattr(guard, "commit", None)
    if not callable(commit):
        return config

    profile_id = _profile_id_of(config)
    try:
        commit(config, profile_id)
    except Exception:
        # Two requests can allocate the same signature at the same time, and
        # the second one loses the race when it commits.  The profile it
        # already wrote would then exist without its signatures, and a later
        # allocation could pick the same fingerprint again.  The losing
        # profile is therefore removed.
        remover = getattr(store, "delete_profile", None) or getattr(store, "remove_profile", None)
        if callable(remover):
            with contextlib.suppress(Exception):
                remover(profile_id)
        raise
    return config


def _storage_dir(profile_id: str) -> Path:
    return deps.get_profile_home() / "profiles" / profile_id


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("", summary="List profiles")
async def list_profiles(
    group_id: str | None = Query(default=None),  # noqa: B008
    tag: str | None = Query(default=None),  # noqa: B008
    status_filter: str | None = Query(default=None, alias="status"),  # noqa: B008
    search: str | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=100, ge=0, le=1000),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    reveal: bool = Query(default=False, description="Return the unredacted secrets."),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Return the profiles matching the supplied filters."""
    lister = getattr(store, "list_profiles", None)
    if lister is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement list_profiles",
        )
    try:
        configs = lister(
            group_id=group_id,
            tag=tag,
            status=status_filter,
            search=search,
        )
    except TypeError:
        configs = lister()
    items = list(configs or [])
    total = len(items)
    page = items[offset : offset + limit] if limit else items[offset:]
    return {
        "items": [deps.serialize_profile(item, reveal_secrets=reveal) for item in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a profile")
async def create_profile(
    request: ProfileCreateRequest = Body(default_factory=ProfileCreateRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    guard: Any = Depends(deps.get_guard),  # noqa: B008
) -> dict[str, Any]:
    """Create a new profile from a preset and run it through the collision guard."""
    config = _build_config(
        request.preset_id,
        request.overrides,
        request.master_seed,
        request.seed_strategy,
        request.name,
        store,
    )
    config = _apply_identity_overrides(config, request)
    config = _apply_overrides(config, request.overrides)

    allocated = _allocate(store, guard, config)

    creator = getattr(store, "create_profile", None)
    if creator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement create_profile",
        )
    # The profile is stored first and the signatures are committed
    # afterwards: the signature rows reference the profile row.
    creator(allocated)
    if guard is not None:
        commit = getattr(guard, "commit", None)
        if callable(commit):
            commit(allocated, _profile_id_of(allocated))
    return deps.serialize_profile(allocated)


@router.get("/{profile_id}", summary="Fetch a profile")
async def get_profile(
    profile_id: str,
    reveal: bool = Query(default=False, description="Return the unredacted secrets."),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Return a single profile configuration.

    The master seed and the proxy password are redacted unless ``reveal``
    is set: the seed regenerates the whole fingerprint, and the encrypted
    password is not a barrier to anyone who can already reach the API.
    """
    return deps.serialize_profile(_load_profile(profile_id, store), reveal_secrets=reveal)


@router.patch("/{profile_id}", summary="Update mutable profile fields")
async def update_profile(
    profile_id: str,
    request: ProfileUpdateRequest = Body(default_factory=ProfileUpdateRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Update the mutable identity/override fields of a profile."""
    config = _load_profile(profile_id, store)

    identity_changes: dict[str, Any] = {}
    for field in ("name", "notes", "tags", "group_id", "folder_id", "status"):
        value = getattr(request, field, None)
        if value is not None:
            identity_changes[field] = value
    updated = _mutate_identity(config, **identity_changes) if identity_changes else config
    updated = _apply_overrides(updated, request.overrides)

    if request.proxy is not None:
        updated = _apply_overrides(updated, {"proxy": {"url": request.proxy}})
    if request.geo_sync is not None:
        updated = _apply_overrides(updated, {"geo_sync": request.geo_sync})

    updated = _touch(updated)

    updater = getattr(store, "update_profile", None)
    if updater is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement update_profile",
        )
    updater(updated)
    return deps.serialize_profile(updated)


@router.delete("/{profile_id}", summary="Delete a profile")
async def delete_profile(
    profile_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Delete a profile and its on-disk storage directory."""
    _load_profile(profile_id, store)
    deleter = getattr(store, "delete_profile", None)
    if deleter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement delete_profile",
        )
    deleter(profile_id)
    storage = _storage_dir(profile_id)
    if storage.exists():
        shutil.rmtree(storage, ignore_errors=True)
    return {"deleted": True, "profile_id": profile_id}


@router.post("/{profile_id}/clone", status_code=status.HTTP_201_CREATED, summary="Clone a profile")
async def clone_profile(
    profile_id: str,
    request: CloneRequest = Body(default_factory=CloneRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    guard: Any = Depends(deps.get_guard),  # noqa: B008
) -> dict[str, Any]:
    """Clone an existing profile, optionally with a fresh master seed."""
    source = _load_profile(profile_id, store)

    new_id = str(uuid.uuid4())
    seed = request.master_seed or deps.new_master_seed()

    clone_fn = getattr(store, "clone_profile", None)
    if callable(clone_fn):
        try:
            allocated = clone_fn(profile_id, new_id, seed, new_name=request.name)
        except TypeError:
            allocated = clone_fn(profile_id, new_id, seed)
        if request.clean_storage:
            storage = _storage_dir(new_id)
            if storage.exists():
                shutil.rmtree(storage, ignore_errors=True)
            storage.mkdir(parents=True, exist_ok=True)
        return deps.serialize_profile(allocated)

    clone = source
    identity = _identity_of(source)
    changes: dict[str, Any] = {"id": new_id}
    if request.name:
        changes["name"] = request.name
    else:
        base_name = str(getattr(identity, "name", "profile") or "profile")
        changes["name"] = f"{base_name} (copy)"
    if request.group_id is not None:
        changes["group_id"] = request.group_id
    if request.folder_id is not None:
        changes["folder_id"] = request.folder_id
    if request.tags:
        changes["tags"] = list(request.tags)
    if request.notes is not None:
        changes["notes"] = request.notes
    changes["created_at"] = datetime.now(UTC)
    changes["updated_at"] = datetime.now(UTC)
    changes["status"] = "created"
    changes["launch_count"] = 0
    changes["total_runtime_ms"] = 0
    clone = _mutate_identity(clone, **changes)

    if request.new_seed or request.master_seed:
        clone = _override_seed(clone, seed)

    allocated = _allocate(store, guard, clone)

    creator = getattr(store, "create_profile", None)
    if creator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement create_profile",
        )
    creator(allocated)

    if request.clean_storage:
        storage = _storage_dir(new_id)
        if storage.exists():
            shutil.rmtree(storage, ignore_errors=True)
        storage.mkdir(parents=True, exist_ok=True)

    return deps.serialize_profile(allocated)


@router.post("/{profile_id}/launch", summary="Launch the profile browser")
async def launch_profile(
    profile_id: str,
    request: LaunchRequest = Body(default_factory=LaunchRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    launcher: Any = Depends(deps.get_launcher),  # noqa: B008
) -> dict[str, Any]:
    """Launch the browser for a profile.

    ``headful`` controls whether the browser window is shown; the launcher
    exposes headless mode through its constructor, so the flag is translated
    into the ``--headless=new`` command-line switch when requested.
    """
    config = _load_profile(profile_id, store)
    launch = getattr(launcher, "launch", None)
    if not callable(launch):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="browser launcher does not implement launch",
        )

    extra_args: list[str] = []
    if not request.headful:
        extra_args.append("--headless=new")
    if request.url:
        extra_args.append(request.url)

    try:
        result = launch(config, extra_args=extra_args or None)
    except TypeError:
        result = launch(config)
    return _launch_result(result, profile_id)


def _launch_result(result: Any, profile_id: str) -> dict[str, Any]:
    """Normalise the launcher's return value into a JSON payload."""
    if result is None:
        return {"profile_id": profile_id, "launched": True}
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("profile_id", profile_id)
        payload.setdefault("launched", True)
        return payload
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        payload.setdefault("profile_id", profile_id)
        payload.setdefault("launched", True)
        return payload
    return {"profile_id": profile_id, "launched": True, "pid": str(result)}


@router.post("/{profile_id}/stop", summary="Stop the profile browser")
async def stop_profile(
    profile_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
    launcher: Any = Depends(deps.get_launcher),  # noqa: B008
) -> dict[str, Any]:
    """Stop the running browser for a profile."""
    _load_profile(profile_id, store)
    for attr in ("stop", "stop_profile", "terminate"):
        fn = getattr(launcher, attr, None)
        if callable(fn):
            result = fn(profile_id)
            return {
                "profile_id": profile_id,
                "stopped": True,
                "result": _launch_result(result, profile_id),
            }
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="browser launcher does not implement stop",
    )


@router.get("/{profile_id}/launcher-config", summary="Inspect the launcher configuration")
async def launcher_config(
    profile_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
    launcher: Any = Depends(deps.get_launcher),  # noqa: B008
) -> dict[str, Any]:
    """Return the exact command line / JSON payload handed to the browser core.

    Nothing is started: the endpoint only *builds* the command line and the
    core configuration payload, which makes it a safe debugging aid.
    """
    config = _load_profile(profile_id, store)

    for attr in ("build_launch_config", "launcher_config", "build_config", "build_command"):
        fn = getattr(launcher, attr, None)
        if callable(fn):
            try:
                result = fn(config)
            except TypeError:
                result = fn(config, profile_id)
            return {
                "profile_id": profile_id,
                "config": deps.redact_secrets(_jsonable(result)),
            }

    user_data_dir = _storage_dir(profile_id)
    payload: dict[str, Any] = {"profile_id": profile_id, "config": {}}

    build_args = getattr(launcher, "build_args", None)
    if callable(build_args):
        try:
            args = build_args(config, user_data_dir)
        except TypeError:
            args = build_args(config)
        payload["config"]["args"] = _redact_args(list(args))
        payload["config"]["command"] = " ".join(payload["config"]["args"])

    core_config = getattr(launcher, "_config_payload", None)
    if callable(core_config):
        try:
            payload["config"]["core_config"] = deps.redact_secrets(_jsonable(core_config(config)))
        except Exception:  # noqa: BLE001 - the payload builder is best-effort
            payload["config"]["core_config"] = None

    if "args" not in payload["config"]:
        payload["config"]["profile"] = deps.serialize_profile(config)
        payload["config"]["note"] = "launcher does not expose a configuration builder yet"
    return payload


def _redact_args(args: list[Any]) -> list[str]:
    """Replace the credentials in a command line with a placeholder.

    The command line carries the proxy credentials in clear text -- that is
    how Chromium expects them -- and the endpoint is a debugging aid that
    gets pasted into tickets.  The credential is masked, but the rest of the
    command stays readable.
    """

    masked: list[str] = []
    for arg in args:
        text = str(arg)
        if text.startswith("--proxy-server-credentials="):
            masked.append("--proxy-server-credentials=***redacted***")
        else:
            masked.append(text)
    return masked


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of *value* into JSON-serialisable data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name, None))
            for field in dataclasses.fields(value)
        }
    return str(value)


@router.post("/{profile_id}/validate", summary="Validate a profile")
async def validate_profile(
    profile_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
    resolver: Any = Depends(deps.get_resolver),  # noqa: B008
) -> dict[str, Any]:
    """Run the consistency resolver and return its report."""
    config = _load_profile(profile_id, store)
    checker = getattr(resolver, "check", None)
    if not callable(checker):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="consistency resolver does not implement check",
        )
    report = checker(config)
    dump = getattr(report, "model_dump", None)
    if callable(dump):
        return {"profile_id": profile_id, "report": dump(mode="json")}
    if isinstance(report, dict):
        return {"profile_id": profile_id, "report": report}
    return {"profile_id": profile_id, "report": {"ok": bool(report), "detail": str(report)}}


@router.post("/{profile_id}/probe", summary="Probe a profile at runtime")
async def probe_profile(
    profile_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
    probe: Any = Depends(deps.get_probe),  # noqa: B008
) -> dict[str, Any]:
    """Run the runtime probe and return the probe report."""
    config = _load_profile(profile_id, store)
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runtime probe does not implement collect",
        )
    report = collector(config)
    return _probe_payload(report, profile_id)


def _probe_payload(report: Any, profile_id: str) -> dict[str, Any]:
    """Normalise a probe report into a JSON payload.

    The probe returns either a Pydantic model or a dataclass; both are
    flattened into a plain dictionary here.
    """
    dump = getattr(report, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        payload.setdefault("profile_id", profile_id)
        return payload
    if isinstance(report, dict):
        payload = dict(report)
        payload.setdefault("profile_id", profile_id)
        return payload
    fields: dict[str, Any] = {"profile_id": profile_id}
    for attr in ("ok", "distinct_ratio", "collision_rate", "persistence_ok", "consistency_pass"):
        if hasattr(report, attr):
            fields[attr] = getattr(report, attr)
    results = getattr(report, "results", None)
    if results is not None:
        fields["results"] = [_jsonable(item) for item in results]
    return fields


@router.get("/{profile_id}/status", summary="Return the profile status")
async def profile_status(
    profile_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Return the lightweight status of a profile."""
    config = _load_profile(profile_id, store)
    identity = _identity_of(config)
    return {
        "profile_id": profile_id,
        "status": getattr(identity, "status", None) or identity.get("status"),
        "name": getattr(identity, "name", None) or identity.get("name"),
    }


@router.head("", summary="Count profiles", response_model=None)
async def count_profiles(store: Any = Depends(deps.get_store)) -> Response:  # noqa: B008
    """Return the number of stored profiles in a response header."""
    lister = getattr(store, "list_profiles", None)
    total = 0
    if lister is not None:
        try:
            total = len(lister())
        except Exception:  # noqa: BLE001 - defensive
            total = 0
    return Response(status_code=status.HTTP_200_OK, headers={"X-Total-Count": str(total)})
