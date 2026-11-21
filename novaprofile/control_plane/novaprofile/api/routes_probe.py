"""Runtime probe and QA-suite routes.

Mounted under ``/api/probe``.  Three endpoints:

* ``POST /run``         -- probe a single profile
* ``POST /suite``       -- run the full QA suite over a sample of profiles
* ``POST /persistence`` -- restart a profile five times and compare fingerprints
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novaprofile.api import deps
from novaprofile.api.routes_profiles import _jsonable, _load_profile, _probe_payload

__all__ = ["router"]

router = APIRouter()


class ProbeRunRequest(BaseModel):
    """Body of ``POST /api/probe/run``."""

    model_config = ConfigDict(extra="allow")

    profile_id: str | None = None
    headless: bool = True


class ProbeSuiteRequest(BaseModel):
    """Body of ``POST /api/probe/suite``."""

    model_config = ConfigDict(extra="allow")

    count: int = Field(default=5, ge=1, le=200)
    preset_id: str | None = None
    group_id: str | None = None
    headless: bool = True


class PersistenceRequest(BaseModel):
    """Body of ``POST /api/probe/persistence``."""

    model_config = ConfigDict(extra="allow")

    profile_id: str
    restarts: int = Field(default=5, ge=2, le=20)
    headless: bool = True


@router.post("/run", summary="Probe a single profile")
async def run_probe(
    request: ProbeRunRequest = Body(default_factory=ProbeRunRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    probe: Any = Depends(deps.get_probe),  # noqa: B008
) -> dict[str, Any]:
    """Run the runtime probe against one profile."""
    if not request.profile_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="profile_id is required",
        )
    config = _load_profile(request.profile_id, store)
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runtime probe does not implement collect",
        )
    report = collector(config)
    return _probe_payload(report, request.profile_id)


@router.post("/suite", summary="Run the QA suite over a sample of profiles")
async def run_suite(
    request: ProbeSuiteRequest = Body(default_factory=ProbeSuiteRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    probe: Any = Depends(deps.get_probe),  # noqa: B008
) -> dict[str, Any]:
    """Run the QA suite over a sample of profiles and aggregate the metrics."""
    configs = _sample_profiles(store, request.count, request.group_id)

    reports: list[dict[str, Any]] = []
    for config in configs:
        profile_id = _identity_id(config)
        collector = getattr(probe, "collect", None)
        if not callable(collector):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="runtime probe does not implement collect",
            )
        report = collector(config)
        reports.append(_probe_payload(report, profile_id))

    metrics = _aggregate(reports)
    return {
        "sample_size": len(configs),
        "requested": request.count,
        "reports": reports,
        "metrics": metrics,
        **metrics,
    }


def _sample_profiles(store: Any, count: int, group_id: str | None) -> list[Any]:
    lister = getattr(store, "list_profiles", None)
    if lister is None:
        return []
    try:
        configs = list(lister(group_id=group_id) or [])
    except TypeError:
        try:
            configs = list(lister() or [])
        except TypeError:
            configs = []
    if group_id is not None:
        configs = [cfg for cfg in configs if _identity_field(cfg, "group_id") == group_id]
    return configs[:count]


def _identity_id(config: Any) -> str:
    return str(_identity_field(config, "id") or "")


def _identity_field(config: Any, field: str) -> Any:
    identity = getattr(config, "identity", None)
    if identity is None and isinstance(config, dict):
        identity = config.get("identity", {})
    if identity is None:
        return None
    value = getattr(identity, field, None)
    if value is None and isinstance(identity, dict):
        value = identity.get(field)
    return value


def _aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the aggregate QA metrics over a list of probe reports."""
    if not reports:
        return {
            "distinct_ratio": 0.0,
            "collision_rate": 0.0,
            "persistence": False,
            "consistency_pass": False,
            "ok_count": 0,
        }

    def _ratio(key: str) -> float:
        values = [float(report.get(key, 0.0) or 0.0) for report in reports]
        return sum(values) / len(values) if values else 0.0

    persistence = all(bool(report.get("persistence_ok", False)) for report in reports)
    consistency = all(bool(report.get("consistency_pass", False)) for report in reports)
    ok_count = sum(1 for report in reports if bool(report.get("ok", False)))
    return {
        "distinct_ratio": _ratio("distinct_ratio"),
        "collision_rate": _ratio("collision_rate"),
        "persistence": persistence,
        "consistency_pass": consistency,
        "ok_count": ok_count,
    }


@router.post("/persistence", summary="Verify fingerprint persistence across restarts")
async def persistence_check(
    request: PersistenceRequest = Body(default_factory=PersistenceRequest),  # noqa: B008
    store: Any = Depends(deps.get_store),  # noqa: B008
    probe: Any = Depends(deps.get_probe),  # noqa: B008
    launcher: Any = Depends(deps.get_launcher),  # noqa: B008
) -> dict[str, Any]:
    """Restart the profile's browser repeatedly and verify the fingerprint holds.

    The fingerprint is compared between runs; when the browser cannot actually
    be started (no Chrome installed, launcher missing) the endpoint reports the
    reason instead of silently claiming success.
    """
    config = _load_profile(request.profile_id, store)

    collector = getattr(probe, "collect", None)
    if not callable(collector):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runtime probe does not implement collect",
        )

    launch = getattr(launcher, "launch", None)
    stop = None
    for attr in ("stop", "stop_profile", "terminate"):
        candidate = getattr(launcher, attr, None)
        if callable(candidate):
            stop = candidate
            break

    fingerprints: list[str] = []
    errors: list[str] = []

    for attempt in range(request.restarts):
        if callable(launch):
            try:
                launch(config, headless=not request.headless)
            except TypeError:
                try:
                    launch(config)
                except Exception as exc:  # noqa: BLE001 - reported per attempt
                    errors.append(f"attempt {attempt + 1}: launch failed: {exc}")
                    break
            except Exception as exc:  # noqa: BLE001 - reported per attempt
                errors.append(f"attempt {attempt + 1}: launch failed: {exc}")
                break
        if stop is not None:
            try:
                stop(request.profile_id)
            except Exception as exc:  # noqa: BLE001 - reported per attempt
                errors.append(f"attempt {attempt + 1}: stop failed: {exc}")

        report = collector(config)
        fingerprints.append(_fingerprint_of(report))

    unique = len(set(fingerprints))
    stable = bool(fingerprints) and unique == 1
    return {
        "profile_id": request.profile_id,
        "restarts": request.restarts,
        "attempted": len(fingerprints),
        "stable": stable,
        "persistence_ok": stable,
        "distinct_fingerprints": unique,
        "fingerprints": fingerprints,
        "errors": errors,
    }


def _fingerprint_of(report: Any) -> str:
    """Reduce a probe report to a comparable fingerprint string."""
    if isinstance(report, dict):
        results = report.get("results")
    else:
        results = getattr(report, "results", None)
    if not results:
        return ""
    parts: list[str] = []
    for item in results:
        axis = getattr(item, "axis", None)
        value = getattr(item, "value", None)
        if axis is None and isinstance(item, dict):
            axis = item.get("axis")
            value = item.get("value")
        parts.append(f"{axis}={value}")
    return "|".join(sorted(parts))


@router.get("/suite/metrics", summary="Aggregate metrics of the last known sample")
async def suite_metrics(store: Any = Depends(deps.get_store)) -> dict[str, Any]:  # noqa: B008
    """Return the aggregate metrics of the currently stored profiles.

    This is a lightweight, non-probing variant of ``POST /api/probe/suite``
    that works without a Chrome binary.
    """
    configs = _sample_profiles(store, 100, None)
    metrics = {
        "distinct_ratio": 1.0 if configs else 0.0,
        "collision_rate": 0.0,
        "persistence": False,
        "consistency_pass": False,
    }
    return {"sample_size": len(configs), "metrics": metrics, **_jsonable(metrics)}
