"""Typer root application for the ``nova`` command line interface.

Usage::

    nova profile create --preset pixel-7
    nova profile list --json
    nova preset list
    nova batch create --count 10 --preset pixel-7
    nova probe suite --count 5
    nova doctor

Every command works without the rest of the engine being present: missing
modules produce a clear diagnostic and a non-zero exit code instead of a
traceback.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from novaprofile import __version__
from novaprofile.api import deps
from novaprofile.cli import batch_cmds, probe_cmds, profile_cmds

__all__ = ["app", "main"]

app = typer.Typer(
    name="nova",
    help="NOVA Profile Engine -- Android browser fingerprint profile manager.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


# --------------------------------------------------------------------------
# preset
# --------------------------------------------------------------------------

preset_app = typer.Typer(help="Inspect the device preset catalogue.", no_args_is_help=True)
app.add_typer(preset_app, name="preset")


@preset_app.command("list")
def preset_list(as_json: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    """List the available device presets."""
    presets = deps.list_presets()
    if as_json:
        console.print_json(json.dumps(presets, default=str))
        return
    if not presets:
        console.print(
            "[yellow]No presets available yet (novaprofile.core.devices is not available).[/yellow]"
        )
        return
    table = Table(title=f"Presets ({len(presets)})")
    table.add_column("ID", style="cyan")
    table.add_column("Manufacturer")
    table.add_column("Model")
    table.add_column("Android")
    for preset in presets:
        table.add_row(
            str(preset.get("id", preset.get("preset_id", ""))),
            str(preset.get("manufacturer", preset.get("brand", "-"))),
            str(preset.get("model", preset.get("device", "-"))),
            str(preset.get("release", preset.get("android_release", "-"))),
        )
    console.print(table)


@preset_app.command("show")
def preset_show(preset_id: str = typer.Argument(..., help="Preset id.")) -> None:
    """Show the full definition of one preset."""
    preset = deps.get_preset(preset_id)
    if preset is None:
        console.print(Panel(f"[red]preset {preset_id!r} not found[/red]", border_style="red"))
        raise typer.Exit(code=4)
    console.print(Panel(json.dumps(preset, indent=2, default=str), title=preset_id))


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

_CHROME_CANDIDATES = (
    "chrome.exe",
    "msedge.exe",
    "chromium.exe",
    "brave.exe",
)


def _find_chrome() -> tuple[str | None, list[str]]:
    """Locate a Chromium-based browser binary.

    Returns the resolved path (or ``None``) plus the list of locations that
    were probed, so ``doctor`` can explain *where* it looked.
    """
    probed: list[str] = []
    override = os.environ.get("NOVA_CHROME_PATH")
    if override:
        probed.append(override)
        if Path(override).exists():
            return override, probed

    on_path = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if on_path:
        probed.append(on_path)
        return on_path, probed

    local = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("PROGRAMFILES")
    if not program_files:
        program_files = os.environ.get("ProgramFiles")  # noqa: SIM112 - Windows spelling
    roots: list[Path] = []
    if local:
        roots.append(Path(local) / "Google" / "Chrome" / "Application")
        roots.append(Path(local) / "Chromium" / "Application")
    if program_files:
        roots.append(Path(program_files) / "Google" / "Chrome" / "Application")
    if platform.system() != "Windows":
        roots.extend([Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/google/chrome")])

    for root in roots:
        for candidate in _CHROME_CANDIDATES:
            path = root / candidate
            probed.append(str(path))
            if path.exists():
                return str(path), probed
    return None, probed


@app.command("doctor")
def doctor() -> None:
    """Run environment diagnostics.

    Reports the Python version, the detected browser binary, the writable
    storage directories and the availability of every engine module.
    """
    home = deps.get_profile_home()
    table = Table(title="NOVA Profile Engine -- environment")
    table.add_column("Check")
    table.add_column("Status")
    # The paths are long and they are the point of the table, so the
    # column wraps instead of truncating: a truncated path is worse than
    # no path, because it looks like one.
    table.add_column("Detail", overflow="fold", no_wrap=False)

    table.add_row("NOVA version", "[green]ok[/green]", __version__)
    table.add_row(
        "Python",
        "[green]ok[/green]",
        f"{platform.python_version()} ({sys.executable})",
    )
    table.add_row("Platform", "[green]ok[/green]", f"{platform.system()} {platform.release()}")

    chrome_path, probed = _find_chrome()
    if chrome_path:
        table.add_row("Browser binary", "[green]ok[/green]", chrome_path)
    else:
        detail = f"not found (probed {len(probed)} locations; set NOVA_CHROME_PATH)"
        table.add_row("Browser binary", "[yellow]missing[/yellow]", detail)

    # The time zone database.  Without it every profile claims UTC with
    # a zero offset, which is one of the loudest signals a site can
    # detect -- so the condition has to be visible here, before a fleet
    # is created, rather than discovered in production.
    from novaprofile.core.consistency import tzdata_status

    tzdata_ok, tzdata_detail = tzdata_status()
    if tzdata_ok:
        table.add_row("Time zone database", "[green]ok[/green]", tzdata_detail)
    else:
        table.add_row("Time zone database", "[red]missing[/red]", tzdata_detail)

    db_path = deps.get_state_db_path()
    table.add_row("State DB", "[green]ok[/green]", str(db_path))

    # The native core.
    #
    # The core is the Chromium build that carries the patch series.  A
    # fleet that runs on a stock browser is still a fleet -- the control
    # plane generates the profiles either way -- but the surfaces the
    # patches own are served by the browser itself rather than by the
    # core, so the operator has to know which of the two is in use.
    try:
        from novaprofile.core.native_core import core_status

        core_ok, core_detail = core_status()
    except Exception as exc:  # pragma: no cover - the import is optional
        core_ok, core_detail = False, f"the check is unavailable: {exc}"

    if core_ok:
        table.add_row("Native core", "[green]ok[/green]", core_detail)
    else:
        table.add_row("Native core", "[yellow]missing[/yellow]", core_detail)

    for label, directory in (
        ("Home dir", home),
        ("Profiles dir", home / "profiles"),
        ("Keys dir", home / "keys"),
        ("Logs dir", home / "logs"),
    ):
        writable = "ok"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".nova-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            writable = f"not writable: {exc}"
        colour = "green" if writable == "ok" else "red"
        table.add_row(label, f"[{colour}]{writable}[/{colour}]", str(directory))

    modules = deps.available_modules()
    module_table = Table(title="Engine modules")
    module_table.add_column("Module")
    module_table.add_column("Status")
    for name, ready in modules.items():
        if ready:
            module_table.add_row(name, "[green]available[/green]")
        else:
            module_table.add_row(name, "[yellow]not available yet[/yellow]")

    console.print(table)
    console.print(module_table)


@app.command("keygen")
def keygen() -> None:
    """Generate a fresh API key for the control plane."""
    from novaprofile.api import auth

    console.print(
        Panel(
            auth.generate_key(),
            title="NOVA API key",
            border_style="green",
        )
    )
    console.print("[dim]Set it as NOVA_API_KEY before starting the server.[/dim]")


# --------------------------------------------------------------------------
# top-level helpers
# --------------------------------------------------------------------------


@app.command("version")
def version() -> None:
    """Print the NOVA version."""
    console.print(f"nova {__version__}")


@app.command("serve")
def serve(
    host: str = typer.Option("", "--host", help="Bind address (default 127.0.0.1)."),  # noqa: B008
    port: int = typer.Option(0, "--port", min=0, max=65535, help="Bind port (default 8756)."),  # noqa: B008
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload."),  # noqa: B008
) -> None:
    """Start the FastAPI control plane with uvicorn.

    The server binds to the loopback interface by default.  When it is bound
    to another interface, an API key is required; the command refuses to
    start without one.
    """
    try:
        import uvicorn
    except ImportError:
        console.print(Panel("[red]uvicorn is not installed[/red]", border_style="red"))
        raise typer.Exit(code=3) from None

    from novaprofile.api import auth
    from novaprofile.api.app import create_app

    bind_host = host or auth.api_host()
    bind_port = port or auth.api_port()

    report = auth.exposure_report()
    if not report.loopback and not auth.require_auth():
        console.print(
            Panel(
                f"[red]Refusing to start[/red]\n\n"
                f"The control plane would be bound to {bind_host}, which is reachable "
                "from the network, but no API key is configured.\n"
                "Every profile, seed and proxy credential on this machine would be "
                "readable by anyone who can reach the port.\n\n"
                "Set NOVA_API_KEY (or pass --host 127.0.0.1) and try again.",
                border_style="red",
            )
        )
        raise typer.Exit(code=2)

    for warning in report.warnings:
        console.print(f"[yellow]![/yellow] {warning}")

    console.print(f"[green]Serving[/green] NOVA control plane on http://{bind_host}:{bind_port}")
    if auth.require_auth():
        console.print("[dim]Authentication is on; send the key in the X-Nova-Key header.[/dim]")
    uvicorn.run(create_app(), host=bind_host, port=bind_port, reload=reload)


# --------------------------------------------------------------------------
# the JavaScript runtime
# --------------------------------------------------------------------------


@app.command("runtime")
def runtime(
    preset_id: str = typer.Option("pixel-8-pro-android14", "--preset", help="Preset id."),
    output: Annotated[  # noqa: B008
        Path | None, typer.Option("--output", "-o", help="Write the script here.")
    ] = None,
    show_digest: Annotated[  # noqa: B008
        bool, typer.Option("--digest/--no-digest", help="Print the digest of the script.")
    ] = True,
) -> None:
    """Build the page-context JavaScript runtime for one profile.

    The runtime is the fallback layer: it carries the fingerprint into the
    page when the native Chromium core is not there.  The native core is
    the better mechanism -- it does not go through JavaScript at all -- but
    the runtime is what makes a stock browser usable in the meantime.

    The digest identifies the script exactly.  Two profiles never produce
    the same digest, and the same profile always produces the same one, so
    the digest can be recorded next to a measurement.
    """

    from novaprofile.core.generator import ProfileGenerator
    from novaprofile.js_runtime import JSRuntimeBuilder

    try:
        profile = ProfileGenerator().generate(preset_id)
    except Exception as exc:  # the preset does not exist
        console.print(Panel(f"[red]{exc}[/red]", border_style="red"))
        raise typer.Exit(code=4) from None

    builder = JSRuntimeBuilder()
    script = builder.build(profile)
    digest = builder.build_hash(profile)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(script, encoding="utf-8")
        console.print(f"[green]wrote[/green] {output} ({len(script)} bytes)")
    if show_digest:
        console.print(f"digest [cyan]{digest}[/cyan]")
    if output is None:
        sys.stdout.write(script)


def main() -> None:
    """Console-script entry point."""
    app()


profile_cmds.register(app)
batch_cmds.register(app)
probe_cmds.register(app)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
