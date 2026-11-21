"""``nova batch`` command group."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from novaprofile.api import deps

__all__ = ["app", "register"]

app = typer.Typer(help="Batch operations over many profiles.", no_args_is_help=True)
console = Console()


def _fail(message: str, code: int = 1) -> None:
    console.print(Panel(f"[red]{message}[/red]", title="Error", border_style="red"))
    raise typer.Exit(code=code)


def _store() -> Any:
    try:
        return deps.get_store()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}")
        raise  # pragma: no cover - unreachable


def _jsonable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@app.command("create")
def batch_create(
    count: int = typer.Option(..., "--count", min=1, help="How many profiles to create."),  # noqa: B008
    preset: str = typer.Option(..., "--preset", help="Device preset id."),  # noqa: B008
    group: str | None = typer.Option(None, "--group", help="Group id."),  # noqa: B008
    tags: list[str] = typer.Option([], "--tags", help="Tags (repeatable)."),  # noqa: B008
    name_prefix: str = typer.Option("profile", "--name-prefix", help="Prefix for generated names."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Create many profiles from one preset."""
    store = _store()
    try:
        guard = deps.get_guard()
    except deps.EngineUnavailableError:
        guard = None

    results = _run_batch(store, guard, count, preset, group, list(tags), name_prefix)
    if as_json:
        console.print_json(json.dumps(results, default=str))
    else:
        table = Table(title="Batch create")
        table.add_column("Index", justify="right")
        table.add_column("Profile ID", style="cyan")
        table.add_column("Name")
        table.add_column("Result")
        for item in results["created"]:
            table.add_row(
                str(item["index"]),
                item["profile_id"],
                item["name"],
                "[green]created[/green]",
            )
        for item in results["failed"]:
            table.add_row(
                str(item["index"]),
                "-",
                "-",
                f"[red]{item['reason']}[/red]",
            )
        console.print(table)
    if results["failed_count"]:
        raise typer.Exit(code=1)


def _run_batch(
    store: Any,
    guard: Any,
    count: int,
    preset: str,
    group: str | None,
    tags: list[str],
    name_prefix: str,
) -> dict[str, Any]:
    """Create *count* profiles, tolerating individual failures."""
    from novaprofile.api.routes_profiles import _allocate, _build_config, _mutate_identity

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for index in range(count):
        try:
            config = _build_config(preset, {}, None, "random", f"{name_prefix}-{index + 1:04d}")
            config = _mutate_identity(
                config,
                name=f"{name_prefix}-{index + 1:04d}",
                group_id=group,
                tags=list(tags),
            )
            allocated = _allocate(store, guard, config)
            store.create_profile(allocated)
            # The signatures are committed after the profile row exists.
            if guard is not None:
                commit = getattr(guard, "commit", None)
                if callable(commit):
                    commit(allocated, str(allocated.identity.id))
            identity = getattr(allocated, "identity", None)
            profile_id = str(getattr(identity, "id", "") or "")
            created.append(
                {
                    "index": index,
                    "profile_id": profile_id,
                    "name": f"{name_prefix}-{index + 1:04d}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - per-item failures are reported
            failed.append({"index": index, "reason": str(exc)})
    return {
        "requested": count,
        "created": created,
        "failed": failed,
        "created_count": len(created),
        "failed_count": len(failed),
    }


@app.command("group")
def batch_group(
    profile_ids: list[str] = typer.Option(..., "--profile-id", help="Profile id (repeatable)."),  # noqa: B008
    group: str | None = typer.Option(None, "--group", help="Group id to assign."),  # noqa: B008
    tags: list[str] = typer.Option([], "--tags", help="Tags to add."),  # noqa: B008
    folder: str | None = typer.Option(None, "--folder", help="Folder id to assign."),  # noqa: B008
) -> None:
    """Assign a group / tags / folder to many profiles."""
    store = _store()
    updater = getattr(store, "update_profile", None)
    if updater is None:
        _fail("profile store does not implement update_profile", code=3)
        return

    from novaprofile.api.routes_profiles import _mutate_identity, _touch

    updated: list[str] = []
    failed: list[str] = []
    for profile_id in profile_ids:
        try:
            config = store.get_profile(profile_id)
            if config is None:
                failed.append(f"{profile_id}: not found")
                continue
            changes: dict[str, Any] = {}
            if group is not None:
                changes["group_id"] = group
            if folder is not None:
                changes["folder_id"] = folder
            if tags:
                identity = getattr(config, "identity", None)
                current = list(getattr(identity, "tags", []) or [])
                for tag in tags:
                    if tag not in current:
                        current.append(tag)
                changes["tags"] = current
            new_config = _mutate_identity(config, **changes) if changes else config
            updater(_touch(new_config))
            updated.append(profile_id)
        except Exception as exc:  # noqa: BLE001 - per-item failures are reported
            failed.append(f"{profile_id}: {exc}")

    console.print(f"[green]Updated[/green] {len(updated)} profile(s)")
    for reason in failed:
        console.print(f"[red]{reason}[/red]")
    if failed:
        raise typer.Exit(code=1)


@app.command("search")
def batch_search(
    query: str = typer.Option("", "--query", help="Substring to search for."),  # noqa: B008
    group: str | None = typer.Option(None, "--group", help="Restrict to a group."),  # noqa: B008
    tag: str | None = typer.Option(None, "--tag", help="Restrict to a tag."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Search across every stored profile."""
    store = _store()
    lister = getattr(store, "list_profiles", None)
    if lister is None:
        _fail("profile store does not implement list_profiles", code=3)
        return
    try:
        configs = list(lister(group_id=group, tag=tag, status=None, search=query or None) or [])
    except TypeError:
        configs = list(lister() or [])

    needle = query.strip().lower()
    matches: list[Any] = []
    for config in configs:
        identity = getattr(config, "identity", None)
        haystack = " ".join(
            [
                str(getattr(identity, "name", "") or ""),
                str(getattr(identity, "notes", "") or ""),
                " ".join(str(tag) for tag in (getattr(identity, "tags", []) or [])),
            ]
        ).lower()
        if needle and needle not in haystack:
            continue
        matches.append(config)

    if as_json:
        console.print_json(json.dumps([_jsonable(item) for item in matches], default=str))
        return
    table = Table(title=f"Search results ({len(matches)})")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Group")
    table.add_column("Status")
    for config in matches:
        identity = config.identity
        table.add_row(
            str(identity.id or ""),
            str(identity.name or ""),
            str(identity.group_id or "-"),
            str(identity.status or ""),
        )
    console.print(table)


def register(root: typer.Typer) -> None:
    """Attach the ``batch`` command group to *root*."""
    root.add_typer(app, name="batch")
