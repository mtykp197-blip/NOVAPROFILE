"""Command line interface for the NOVA Profile Engine.

The CLI is intentionally decoupled from the API layer so that it remains
usable while the engine modules are still being assembled: every command
degrades with a clear "module not available yet" message instead of an
``ImportError`` traceback.
"""

from __future__ import annotations

__all__ = ["app", "main"]


def __getattr__(name: str) -> object:
    """Lazily expose the Typer application.

    Importing :mod:`novaprofile.cli` therefore does not require Typer to be
    installed, which keeps ``import novaprofile.cli`` cheap.
    """
    if name in {"app", "main"}:
        from novaprofile.cli.main import app

        return app
    raise AttributeError(name)


def main() -> None:
    """Console-script entry point."""
    from novaprofile.cli.main import app

    app()
