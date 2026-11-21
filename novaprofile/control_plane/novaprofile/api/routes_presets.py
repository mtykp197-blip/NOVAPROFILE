"""Preset catalogue routes.

Mounted under ``/api/presets``.  The catalogue itself lives in
``novaprofile.core.devices`` and is imported defensively: when it is not
available the list endpoint returns an empty list instead of failing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from novaprofile.api import deps

__all__ = ["router"]

router = APIRouter()


@router.get("", summary="List available presets")
async def list_presets() -> dict[str, Any]:
    """Return every preset in the device catalogue."""
    presets = deps.list_presets()
    return {"items": presets, "total": len(presets)}


@router.get("/{preset_id}", summary="Fetch a single preset")
async def get_preset(preset_id: str) -> dict[str, Any]:
    """Return the full definition of one preset."""
    preset = deps.get_preset(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"preset {preset_id!r} not found",
        )
    return preset


@router.get("/{preset_id}/recommendation", summary="Recommended config for a preset")
async def preset_recommendation(preset_id: str) -> dict[str, Any]:
    """Return the recommended configuration payload for one preset."""
    preset = deps.get_preset(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"preset {preset_id!r} not found",
        )
    for key in ("recommended", "recommended_config", "config", "template"):
        if key in preset:
            return {"preset_id": preset_id, "recommended": preset[key]}
    return {"preset_id": preset_id, "recommended": preset}


@router.get("/{preset_id}/profiles", summary="Profiles built from a preset")
async def preset_profiles(
    preset_id: str,
    store: Any = Depends(deps.get_store),  # noqa: B008
) -> dict[str, Any]:
    """Return the profiles that were generated from the given preset."""
    lister = getattr(store, "list_profiles", None)
    if lister is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="profile store does not implement list_profiles",
        )
    try:
        configs = lister()
    except TypeError:
        configs = []
    matched = []
    for config in configs or []:
        seed_config = getattr(config, "seed_config", None)
        preset_id_value = getattr(seed_config, "preset_id", None)
        if preset_id_value is None and isinstance(config, dict):
            preset_id_value = config.get("seed_config", {}).get("preset_id")
        if preset_id_value == preset_id:
            matched.append(deps.serialize_profile(config))
    return {"preset_id": preset_id, "items": matched, "total": len(matched)}
