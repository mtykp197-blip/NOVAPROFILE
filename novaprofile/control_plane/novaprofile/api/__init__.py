"""FastAPI control plane for the NOVA Profile Engine.

The package exposes :func:`novaprofile.api.app.create_app`, a factory that
wires the profile, preset, batch and probe routers onto a configured
:class:`fastapi.FastAPI` instance.

Every module in this package imports the sibling engine modules defensively:
the engine is assembled by several agents in parallel, so the control plane
must remain importable (and testable) even while a particular engine module
has not landed yet.  A missing module surfaces as an HTTP 503 with an
actionable message instead of an ``ImportError`` traceback.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app() -> object:
    """Return a configured control-plane application.

    Imported lazily so that ``import novaprofile.api`` stays cheap and does not
    pull in FastAPI when the CLI is used without a web server.
    """
    from novaprofile.api.app import create_app as _create_app

    return _create_app()
