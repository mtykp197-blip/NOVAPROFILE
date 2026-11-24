"""``nova profile`` command group."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from novaprofile.api import deps

__all__ = ["app", "register"]

app = typer.Typer(help="Manage browser fingerprint profiles.", no_args_is_help=True)
console = Console()


def _fail(message: str, code: int = 1) -> None:
    """Print an error panel and exit with a non-zero status."""
    console.print(Panel(f"[red]{message}[/red]", title="Error", border_style="red"))
    raise typer.Exit(code=code)


def _store() -> Any:
    try:
        return deps.get_store()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}")
        raise  # pragma: no cover - unreachable, _fail always exits


def _jsonable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name, None))
            for field in dataclasses.fields(value)
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _profile_id_of(config: Any) -> str:
    identity = getattr(config, "identity", None)
    if identity is None and isinstance(config, dict):
        identity = config.get("identity", {})
    value = getattr(identity, "id", None)
    if value is None and isinstance(identity, dict):
        value = identity.get("id")
    return str(value or "")


def _identity_of(config: Any) -> dict[str, Any]:
    identity = getattr(config, "identity", None)
    if identity is None and isinstance(config, dict):
        identity = config.get("identity", {})
    dump = getattr(identity, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(identity, dict):
        return dict(identity)
    return {}


def _profiles_table(configs: list[Any]) -> Table:
    table = Table(title="Profiles", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Group")
    table.add_column("Status")
    table.add_column("Tags")
    for config in configs:
        identity = _identity_of(config)
        table.add_row(
            _profile_id_of(config),
            str(identity.get("name", "")),
            str(identity.get("group_id") or "-"),
            str(identity.get("status", "")),
            ",".join(identity.get("tags") or []),
        )
    return table


@app.command("create")
def profile_create(
    preset: str = typer.Option(..., "--preset", help="Device preset id."),  # noqa: B008
    name: str | None = typer.Option(None, "--name", help="Profile name."),  # noqa: B008
    group: str | None = typer.Option(None, "--group", help="Group id."),  # noqa: B008
    tags: list[str] = typer.Option([], "--tags", help="Tags (repeatable)."),  # noqa: B008
    proxy: str | None = typer.Option(None, "--proxy", help="Proxy URL."),  # noqa: B008
    count: int = typer.Option(1, "--count", min=1, help="How many profiles to create."),  # noqa: B008
    notes: str = typer.Option("", "--notes", help="Free-form notes."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Create one or more profiles from a preset."""
    from novaprofile.api.routes_profiles import _allocate, _build_config, _mutate_identity

    store = _store()
    try:
        guard = deps.get_guard()
    except deps.EngineUnavailableError:
        guard = None

    created: list[dict[str, Any]] = []
    failures: list[str] = []
    for index in range(count):
        try:
            config = _build_config(preset, {}, None, "random", name or f"{preset}-{index + 1:04d}")
            config = _mutate_identity(
                config,
                name=name or f"{preset}-{index + 1:04d}",
                group_id=group,
                tags=list(tags),
                notes=notes,
            )
            if proxy:
                config = _apply_proxy(config, proxy)
            allocated = _allocate(store, guard, config)
            store.create_profile(allocated)
            # The signatures are committed after the profile row exists.
            if guard is not None:
                commit = getattr(guard, "commit", None)
                if callable(commit):
                    commit(allocated, allocated.identity.id)
            created.append(_jsonable(allocated))
        except deps.EngineUnavailableError as exc:
            _fail(f"{exc}")
        except Exception as exc:  # noqa: BLE001 - reported per item
            failures.append(str(exc))

    if as_json:
        console.print_json(json.dumps(created, default=str))
    else:
        console.print(_profiles_table(created))
    if failures:
        for reason in failures:
            console.print(Panel(f"[red]{reason}[/red]", border_style="red"))
        raise typer.Exit(code=1)


def _apply_proxy(config: Any, proxy: str) -> Any:
    """Attach a proxy URL to a freshly built config."""
    fields = set(getattr(config, "model_fields", {}) or {})
    if "proxy" in fields and hasattr(config, "model_copy"):
        return config.model_copy(update={"proxy": {"url": proxy}})
    if isinstance(config, dict) and "proxy" in config:
        updated = dict(config)
        updated["proxy"] = {"url": proxy}
        return updated
    return config


@app.command("list")
def profile_list(
    group: str | None = typer.Option(None, "--group", help="Filter by group id."),  # noqa: B008
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag."),  # noqa: B008
    status_filter: str | None = typer.Option(None, "--status", help="Filter by status."),  # noqa: B008
    search: str | None = typer.Option(None, "--search", help="Substring search."),  # noqa: B008
    limit: int = typer.Option(100, "--limit", min=0, help="Maximum number of rows."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """List the stored profiles."""
    store = _store()
    lister = getattr(store, "list_profiles", None)
    if lister is None:
        _fail("profile store does not implement list_profiles", code=3)
        return
    try:
        configs = list(
            lister(
                group_id=group,
                tag=tag,
                status=status_filter,
                search=search,
            )
            or []
        )
    except TypeError:
        configs = list(lister() or [])
    configs = configs[:limit]

    if as_json:
        console.print_json(json.dumps([_jsonable(item) for item in configs], default=str))
        return
    if not configs:
        console.print("[yellow]No profiles found.[/yellow]")
        return
    console.print(_profiles_table(configs))


@app.command("show")
def profile_show(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Show the full configuration of one profile."""
    store = _store()
    config = store.get_profile(profile_id)
    if config is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    if as_json:
        console.print_json(json.dumps(_jsonable(config), default=str))
        return
    payload = _jsonable(config)
    console.print(Panel(json.dumps(payload, indent=2, default=str), title=profile_id))


@app.command("delete")
def profile_delete(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
) -> None:
    """Delete a profile."""
    store = _store()
    if store.get_profile(profile_id) is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    store.delete_profile(profile_id)
    console.print(f"[green]Deleted profile[/green] {profile_id}")


@app.command("clone")
def profile_clone(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
    new_seed: bool = typer.Option(True, "--new-seed/--same-seed", help="Roll a fresh master seed."),  # noqa: B008
    name: str | None = typer.Option(None, "--name", help="Name of the clone."),  # noqa: B008
) -> None:
    """Clone an existing profile."""
    from novaprofile.api.routes_profiles import _allocate, _mutate_identity, _override_seed

    store = _store()
    source = store.get_profile(profile_id)
    if source is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    try:
        guard = deps.get_guard()
    except deps.EngineUnavailableError:
        guard = None

    from datetime import UTC, datetime

    from ..core.seed import new_profile_id

    identity = _identity_of(source)
    clone = _mutate_identity(
        source,
        id=new_profile_id(),
        name=name or f"{identity.get('name', 'profile')} (copy)",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        status="created",
        launch_count=0,
        total_runtime_ms=0,
    )
    if new_seed:
        clone = _override_seed(clone, deps.new_master_seed())
    allocated = _allocate(store, guard, clone)
    store.create_profile(allocated)
    if guard is not None:
        commit = getattr(guard, "commit", None)
        if callable(commit):
            commit(allocated, allocated.identity.id)
    console.print(f"[green]Cloned[/green] {profile_id} -> {_profile_id_of(allocated)}")


@app.command("launch")
def profile_launch(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
    headful: bool = typer.Option(False, "--headful", help="Show the browser window."),  # noqa: B008
) -> None:
    """Launch the browser for a profile."""
    store = _store()
    config = store.get_profile(profile_id)
    if config is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    try:
        launcher = deps.get_launcher()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}", code=3)
        return
    launch = getattr(launcher, "launch", None)
    if not callable(launch):
        _fail("browser launcher does not implement launch", code=3)
        return
    try:
        result = launch(config, headful=headful)
    except TypeError:
        result = launch(config)
    except Exception as exc:  # noqa: BLE001 - user-facing failure
        _fail(f"launch failed: {exc}", code=5)
        return
    console.print(f"[green]Launched[/green] {profile_id}: {result}")


@app.command("validate")
def profile_validate(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
) -> None:
    """Run the consistency resolver and show the report."""
    store = _store()
    config = store.get_profile(profile_id)
    if config is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    try:
        resolver = deps.get_resolver()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}", code=3)
        return
    report = resolver.check(config)
    payload = _jsonable(report)
    ok = bool(payload.get("ok", False)) if isinstance(payload, dict) else bool(payload)
    console.print(Panel(json.dumps(payload, indent=2, default=str), title="Consistency report"))
    if not ok:
        raise typer.Exit(code=6)


@app.command("probe")
def profile_probe(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Run the runtime probe against a profile."""
    store = _store()
    config = store.get_profile(profile_id)
    if config is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    try:
        probe = deps.get_probe()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}", code=3)
        return
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        _fail("runtime probe does not implement collect", code=3)
        return
    report = collector(config)
    payload = _jsonable(report)
    if as_json:
        console.print_json(json.dumps(payload, default=str))
        return
    console.print(Panel(json.dumps(payload, indent=2, default=str), title="Probe report"))


def register(root: typer.Typer) -> None:
    """Attach the ``profile`` command group to *root*."""
    root.add_typer(app, name="profile")
