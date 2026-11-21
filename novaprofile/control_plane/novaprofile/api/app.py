"""FastAPI application factory for the NOVA Profile Engine control plane.

``create_app`` returns a fully configured :class:`fastapi.FastAPI` instance:

* routers mounted under ``/api/profiles``, ``/api/presets``, ``/api/batch``
  and ``/api/probe``;
* CORS middleware (all origins by default, configurable through the
  ``NOVA_CORS_ORIGINS`` environment variable);
* domain-error handlers mapping engine exceptions onto HTTP status codes;
* ``/api/health`` and ``/api/schema/json`` service endpoints.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from novaprofile import __version__
from novaprofile.api import auth, deps
from novaprofile.api.routes_batch import router as batch_router
from novaprofile.api.routes_presets import router as presets_router
from novaprofile.api.routes_probe import router as probe_router
from novaprofile.api.routes_profiles import router as profiles_router

__all__ = ["create_app", "app", "cors_origins", "auth"]


def cors_origins() -> list[str]:
    """Return the list of origins allowed by the CORS middleware.

    ``NOVA_CORS_ORIGINS`` accepts a comma separated list.  When it is unset
    no browser origin is allowed at all: the control plane is called by a
    native desktop application, and a web page in the operator's browser
    has no business reading the operator's profiles.
    """
    raw = os.environ.get("NOVA_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]


#: The paths that stay reachable without a credential.  The health endpoint
#: reports whether the service is alive, and the schema endpoint only
#: describes the shape of the configuration -- neither discloses a profile.
PUBLIC_PATHS: frozenset[str] = frozenset({"/api/health"})


def _register_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions onto HTTP status codes."""

    @app.exception_handler(deps.EngineUnavailableError)
    async def _engine_unavailable(
        request: Request, exc: deps.EngineUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "engine_unavailable", "detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def _domain_errors(request: Request, exc: Exception) -> JSONResponse:
        from novaprofile.security.crypto import SecretError

        name = type(exc).__name__

        if name == "ProfileNotFoundError":
            return JSONResponse(status_code=404, content={"error": name, "detail": str(exc)})
        if name == "ProfileCollisionError":
            return JSONResponse(status_code=409, content={"error": name, "detail": str(exc)})
        if name == "ValidationError":
            return JSONResponse(status_code=422, content={"error": name, "detail": str(exc)})
        if isinstance(exc, SecretError) or name == "SecretError":
            return JSONResponse(
                status_code=500, content={"error": "SecretError", "detail": str(exc)}
            )
        raise exc


def _register_auth_middleware(app: FastAPI) -> None:
    """Refuse a request that carries no credential.

    The check is skipped for the public paths and for the preflight
    request that the CORS middleware sends.
    """

    @app.middleware("http")
    async def _authenticate(request: Request, call_next):
        if not auth.require_auth():
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        presented = request.headers.get(auth.API_KEY_HEADER, "")
        if not presented:
            authorization = request.headers.get("authorization", "")
            if authorization.lower().startswith("bearer "):
                presented = authorization[7:]

        if not auth.verify_key(presented):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": (
                        f"a credential is required; send it in the "
                        f"{auth.API_KEY_HEADER!r} header or as a bearer token"
                    ),
                },
            )
        return await call_next(request)


def _register_service_routes(app: FastAPI) -> None:
    """Mount the health and JSON-schema service endpoints."""
    service = APIRouter(prefix="/api")
    origins = cors_origins()

    @service.get("/health")
    async def health() -> dict[str, Any]:
        """Return the liveness payload of the control plane."""
        return {
            "status": "ok",
            "version": __version__,
            "modules": deps.available_modules(),
            "exposure": auth.exposure_report(tuple(origins)).as_dict(),
        }

    @service.get("/schema/json")
    async def json_schema() -> dict[str, Any]:
        """Return the generated JSON Schema of the ``ProfileConfig`` model."""
        try:
            model = deps.get_profile_model()
        except deps.EngineUnavailableError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": "schema_unavailable", "detail": str(exc)},
            )
        try:
            schema = model.model_json_schema()
        except Exception as exc:  # noqa: BLE001 - a half-built model is not fatal
            return JSONResponse(
                status_code=503,
                content={
                    "error": "schema_unavailable",
                    "detail": f"the ProfileConfig model cannot be introspected yet: {exc}",
                },
            )
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = "ProfileConfig"
        return schema

    app.include_router(service)


def create_app() -> FastAPI:
    """Build and return the NOVA control-plane application."""
    app = FastAPI(
        title="NOVA Profile Engine Control Plane",
        version=__version__,
        description=(
            "Control plane for the NOVA Profile Engine: manage Android browser "
            "fingerprint profiles, presets, batch operations and runtime probes."
        ),
    )

    origins = cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(profiles_router, prefix="/api/profiles", tags=["profiles"])
    app.include_router(presets_router, prefix="/api/presets", tags=["presets"])
    app.include_router(batch_router, prefix="/api/batch", tags=["batch"])
    app.include_router(probe_router, prefix="/api/probe", tags=["probe"])

    _register_service_routes(app)
    _register_exception_handlers(app)
    _register_auth_middleware(app)
    return app


app = create_app()
