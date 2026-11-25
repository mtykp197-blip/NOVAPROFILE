"""Batch operation routes.

Mounted under ``/api/batch``.  Covers the bulk operations described by the
specification:

* ``POST /create``                  -- create N profiles in one go
* ``POST /proxies/validate``        -- check a list of proxy endpoints
* ``POST /cookies/import``          -- import cookies into a profile
* ``POST /cookies/export``          -- export a profile's cookies
* ``POST /group``                   -- bulk assign group / tags / folder
* ``POST /search``                  -- fast cross-profile search
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novaprofile.api import deps
from novaprofile.api.routes_profiles import (
    _allocate,
    _build_config,
    _mutate_identity,
    _touch,
)

__all__ = ["router"]

router = APIRouter()


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class BatchCreateRequest(BaseModel):
    """Body of ``POST /api/batch/create``."""

    model_config = ConfigDict(extra="allow")

    count: int = Field(default=1, ge=1, le=1000)
    preset_id: str = ""
    group_id: str | None = None
    folder_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    name_prefix: str = "profile"
    seed_strategy: str = "random"
    notes: str = ""
    overrides: dict[str, Any] = Field(default_factory=dict)


class ProxyValidateRequest(BaseModel):
    """Body of ``POST /api/batch/proxies/validate``."""

    proxies: list[str] = Field(default_factory=list)
    timeout_s: float = 5.0
    workers: int = Field(default=8, ge=1, le=64)


class CookieImportRequest(BaseModel):
    """Body of ``POST /api/batch/cookies/import``."""

    profile_id: str
    format: str = "json"
    payload: str = ""
    cookies: list[dict[str, Any]] = Field(default_factory=list)


class CookieExportRequest(BaseModel):
    """Body of ``POST /api/batch/cookies/export``."""

    profile_id: str
    format: str = "json"


class GroupAssignRequest(BaseModel):
    """Body of ``POST /api/batch/group``."""

    profile_ids: list[str] = Field(default_factory=list)
    group_id: str | None = None
    folder_id: str | None = None
    tags: list[str] | None = None
    add_tags: bool = True


class BatchSearchRequest(BaseModel):
    """Body of ``POST /api/batch/search``."""

    query: str = ""
    group_id: str | None = None
    tag: str | None = None
    status: str | None = None
    limit: int = Field(default=100, ge=0, le=1000)
    offset: int = Field(default=0, ge=0)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _store_profiles(store: Any) -> list[Any]:
    lister = getattr(store, "list_profiles", None)
    if lister is None:
        return []
    try:
        return list(lister() or [])
    except TypeError:
        try:
            return list(lister(group_id=None, tag=None, status=None, search=None) or [])
        except TypeError:
            return []


def _text_of(config: Any) -> str:
    """Return the searchable text of a profile (name + notes + tags)."""
    identity = getattr(config, "identity", None)
    if identity is None and isinstance(config, dict):
        identity = config.get("identity", {})
    if identity is None:
        return ""
    name = getattr(identity, "name", None)
    notes = getattr(identity, "notes", None)
    tags = getattr(identity, "tags", None)
    if name is None and isinstance(identity, dict):
        name = identity.get("name")
        notes = identity.get("notes")
        tags = identity.get("tags")
    parts = [str(name or ""), str(notes or "")]
    parts.extend(str(tag) for tag in (tags or []))
    return " ".join(parts).lower()


def _profile_id_of(config: Any) -> str:
    identity = getattr(config, "identity", None)
    if identity is None and isinstance(config, dict):
        identity = config.get("identity", {})
    if identity is None:
        return ""
    value = getattr(identity, "id", None)
    if value is None and isinstance(identity, dict):
        value = identity.get("id")
    return str(value or "")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post("/create", summary="Create many profiles at once")
async def batch_create(
    request: BatchCreateRequest = Body(default_factory=BatchCreateRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    guard: Any = Depends(deps.get_guard),  # noqa: B008
) -> dict[str, Any]:
    """Create *count* profiles and report the outcome of each one.

    When the operator names no device family, the fleet draws one
    machine per profile out of the whole catalogue.  That is the only
    way a bulk of profiles is a bulk of *different* people: a preset
    repeated a hundred times is one handset wearing a hundred hats, and
    a website reads the GPU, the panel, the chip count and the memory
    and puts them in one bucket.
    """

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    # The fleet path: every profile is built on its own machine.
    if not request.preset_id:
        try:
            from ..core.fleet import generate_fleet

            fleet = generate_fleet(
                request.count,
                name_prefix=request.name_prefix,
                registry=None,
            )
        except Exception as exc:  # noqa: BLE001 - reported per item below
            fleet = []
            failed.append({"index": -1, "reason": str(exc)})

        for index, config in enumerate(fleet):
            try:
                config = _mutate_identity(
                    config,
                    name=f"{request.name_prefix}-{index + 1:04d}",
                    group_id=request.group_id,
                    folder_id=request.folder_id,
                    tags=list(request.tags),
                    notes=request.notes,
                )
                allocated = _allocate(store, guard, config)
                creator = getattr(store, "create_profile", None)
                if creator is None:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="profile store does not implement create_profile",
                    )
                creator(allocated)
                created.append(
                    {
                        "index": index,
                        "profile_id": _profile_id_of(allocated),
                        "name": f"{request.name_prefix}-{index + 1:04d}",
                    }
                )
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - per-item failures are reported
                failed.append({"index": index, "reason": str(exc)})

        return {
            "requested": request.count,
            "created": created,
            "failed": failed,
            "created_count": len(created),
            "failed_count": len(failed),
        }

    for index in range(request.count):
        try:
            config = _build_config(
                request.preset_id,
                request.overrides,
                None,
                request.seed_strategy,
                f"{request.name_prefix}-{index + 1:04d}",
                store,
            )
            config = _mutate_identity(
                config,
                name=f"{request.name_prefix}-{index + 1:04d}",
                group_id=request.group_id,
                folder_id=request.folder_id,
                tags=list(request.tags),
                notes=request.notes,
            )
            allocated = _allocate(store, guard, config)
            creator = getattr(store, "create_profile", None)
            if creator is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="profile store does not implement create_profile",
                )
            creator(allocated)
            created.append(
                {
                    "index": index,
                    "profile_id": _profile_id_of(allocated),
                    "name": f"{request.name_prefix}-{index + 1:04d}",
                }
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - per-item failures are reported
            failed.append({"index": index, "reason": str(exc)})

    return {
        "requested": request.count,
        "created": created,
        "failed": failed,
        "created_count": len(created),
        "failed_count": len(failed),
    }


@router.post("/proxies/validate", summary="Validate a list of proxies")
async def validate_proxies(
    request: ProxyValidateRequest = Body(default_factory=ProxyValidateRequest),  # noqa: B008
) -> dict[str, Any]:
    """Check which of the supplied proxies are reachable.

    The network agent owns the actual checking logic; when it is not available
    the endpoint reports the proxies as unverified rather than failing.
    """
    results: list[dict[str, Any]] = []
    checker = _proxy_checker()
    if checker is None:
        return {
            "items": [
                {
                    "proxy": proxy,
                    "alive": False,
                    "checked": False,
                    "reason": "network module not available yet",
                }
                for proxy in request.proxies
            ],
            "total": len(request.proxies),
            "alive_count": 0,
            "checked": False,
        }

    for proxy in request.proxies:
        try:
            verdict = checker(proxy, timeout=request.timeout_s)
        except Exception as exc:  # noqa: BLE001 - per-item failures are reported
            results.append({"proxy": proxy, "alive": False, "reason": str(exc)})
            continue
        if isinstance(verdict, dict):
            payload = {"proxy": proxy, **verdict}
        else:
            payload = {"proxy": proxy, "alive": bool(verdict)}
        payload.setdefault("alive", False)
        results.append(payload)

    alive = sum(1 for item in results if item.get("alive"))
    return {
        "items": results,
        "total": len(results),
        "alive_count": alive,
        "checked": True,
    }


def _proxy_checker() -> Any:
    """Return a callable that probes a proxy string, if such code exists."""
    candidates = (
        ("novaprofile.network.proxy", ("check_proxy", "validate_proxy", "probe_proxy")),
        ("novaprofile.network.proxies", ("check_proxy", "validate_proxy", "probe_proxy")),
        ("novaprofile.network.health", ("check_proxy", "validate_proxy", "probe_proxy")),
    )
    for module_name, attrs in candidates:
        try:
            module = deps._import(module_name)
        except deps.EngineUnavailableError:
            continue
        for attr in attrs:
            fn = getattr(module, attr, None)
            if callable(fn):
                return fn
    return None


@router.post("/cookies/import", summary="Import cookies into a profile")
async def import_cookies(
    request: CookieImportRequest = Body(default_factory=CookieImportRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Import cookies (JSON or Netscape format) into a profile."""
    loader = getattr(store, "get_profile", None)
    if loader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement get_profile",
        )
    config = loader(request.profile_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"profile {request.profile_id!r} not found",
        )

    cookies = request.cookies
    if not cookies and request.payload:
        cookies = _parse_cookie_payload(request.payload, request.format)
    if not cookies:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no cookies supplied",
        )

    imported = _store_cookies(store, request.profile_id, cookies)
    return {
        "profile_id": request.profile_id,
        "imported": imported,
        "count": len(cookies),
    }


def _parse_cookie_payload(payload: str, fmt: str) -> list[dict[str, Any]]:
    """Parse a cookie payload in JSON or Netscape format."""
    if fmt.lower() == "netscape":
        cookies: list[dict[str, Any]] = []
        for line in payload.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                if stripped.startswith("#HttpOnly_"):
                    stripped = stripped[len("#HttpOnly_") :]
                else:
                    continue
            fields = stripped.split("\t")
            if len(fields) < 7:
                continue
            domain, include_sub, path, secure, expires, name, value = fields[:7]
            cookies.append(
                {
                    "domain": domain,
                    "path": path,
                    "secure": secure.upper() == "TRUE",
                    "expirationDate": float(expires) if expires else 0,
                    "name": name,
                    "value": value,
                    "httpOnly": False,
                    "hostOnly": include_sub.upper() != "TRUE",
                }
            )
        return cookies

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"cookie payload is not valid JSON: {exc}",
        ) from exc
    if isinstance(data, dict):
        for key in ("cookies", "items"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return [data]
    if isinstance(data, list):
        return list(data)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="cookie payload must be a JSON object or array",
    )


def _store_cookies(store: Any, profile_id: str, cookies: list[dict[str, Any]]) -> int:
    """Persist cookies into the profile's storage directory."""
    storage = deps.get_profile_home() / "profiles" / profile_id
    storage.mkdir(parents=True, exist_ok=True)
    target = storage / "cookies.json"
    existing: list[dict[str, Any]] = []
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = []
    if not isinstance(existing, list):
        existing = []
    existing.extend(cookies)
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(cookies)


@router.post("/cookies/export", summary="Export a profile's cookies")
async def export_cookies(
    request: CookieExportRequest = Body(default_factory=CookieExportRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Export the cookies of a profile in JSON or Netscape format."""
    loader = getattr(store, "get_profile", None)
    if loader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement get_profile",
        )
    config = loader(request.profile_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"profile {request.profile_id!r} not found",
        )

    cookies = _load_cookies(request.profile_id)
    fmt = request.format.lower()
    if fmt == "netscape":
        content = _to_netscape(cookies)
    else:
        content = json.dumps(cookies, ensure_ascii=False, indent=2)
    return {
        "profile_id": request.profile_id,
        "format": fmt,
        "count": len(cookies),
        "content": content,
    }


def _load_cookies(profile_id: str) -> list[dict[str, Any]]:
    target = deps.get_profile_home() / "profiles" / profile_id / "cookies.json"
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _to_netscape(cookies: list[dict[str, Any]]) -> str:
    """Render cookies in the Netscape ``cookies.txt`` format."""
    lines = ["# Netscape HTTP Cookie File", "# Generated by NOVA Profile Engine", ""]
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        include_sub = "FALSE" if cookie.get("hostOnly", False) else "TRUE"
        path = str(cookie.get("path", "/"))
        secure = "TRUE" if cookie.get("secure", False) else "FALSE"
        expires = str(int(float(cookie.get("expirationDate", 0) or 0)))
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        lines.append("\t".join([domain, include_sub, path, secure, expires, name, value]))
    return "\n".join(lines) + "\n"


@router.post("/group", summary="Bulk assign group / tags / folder")
async def assign_group(
    request: GroupAssignRequest = Body(default_factory=GroupAssignRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Assign a group, folder or tags to many profiles at once."""
    updater = getattr(store, "update_profile", None)
    if updater is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement update_profile",
        )

    updated: list[str] = []
    failed: list[dict[str, str]] = []

    for profile_id in request.profile_ids:
        try:
            loader = getattr(store, "get_profile", None)
            config = loader(profile_id) if loader is not None else None
            if config is None:
                failed.append({"profile_id": profile_id, "reason": "not found"})
                continue
            changes: dict[str, Any] = {}
            if request.group_id is not None:
                changes["group_id"] = request.group_id
            if request.folder_id is not None:
                changes["folder_id"] = request.folder_id
            if request.tags is not None:
                if request.add_tags:
                    identity = getattr(config, "identity", None)
                    current = list(getattr(identity, "tags", []) or [])
                    for tag in request.tags:
                        if tag not in current:
                            current.append(tag)
                    changes["tags"] = current
                else:
                    changes["tags"] = list(request.tags)
            new_config = _mutate_identity(config, **changes) if changes else config
            new_config = _touch(new_config)
            updater(new_config)
            updated.append(profile_id)
        except Exception as exc:  # noqa: BLE001 - per-item failures are reported
            failed.append({"profile_id": profile_id, "reason": str(exc)})

    return {
        "updated": updated,
        "failed": failed,
        "updated_count": len(updated),
        "failed_count": len(failed),
    }


@router.post("/search", summary="Search across all profiles")
async def search_profiles(
    request: BatchSearchRequest = Body(default_factory=BatchSearchRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Perform a fast in-memory search across every stored profile."""
    configs = _store_profiles(store)
    query = request.query.strip().lower()

    matches: list[dict[str, Any]] = []
    for config in configs:
        identity = getattr(config, "identity", None)
        if identity is None and isinstance(config, dict):
            identity = config.get("identity", {})
        status_value = getattr(identity, "status", None)
        group_value = getattr(identity, "group_id", None)
        tags_value = list(getattr(identity, "tags", []) or [])
        if identity is not None and isinstance(identity, dict):
            status_value = identity.get("status")
            group_value = identity.get("group_id")
            tags_value = list(identity.get("tags") or [])

        if request.group_id is not None and group_value != request.group_id:
            continue
        if request.tag is not None and request.tag not in tags_value:
            continue
        if request.status is not None and status_value != request.status:
            continue
        if query and query not in _text_of(config):
            continue
        matches.append(config)

    total = len(matches)
    if request.limit:
        page = matches[request.offset : request.offset + request.limit]
    else:
        page = matches[request.offset :]
    return {
        "items": [deps.serialize_profile(item) for item in page],
        "total": total,
        "limit": request.limit,
        "offset": request.offset,
    }
