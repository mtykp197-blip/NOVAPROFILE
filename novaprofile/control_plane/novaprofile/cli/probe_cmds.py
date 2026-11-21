"""``nova probe`` command group."""

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

app = typer.Typer(help="Runtime probing and QA suites.", no_args_is_help=True)
console = Console()


def _fail(message: str, code: int = 1) -> None:
    console.print(Panel(f"[red]{message}[/red]", title="Error", border_style="red"))
    raise typer.Exit(code=code)


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


@app.command("run")
def probe_run(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Probe a single profile."""
    store = _store_or_fail()
    config = store.get_profile(profile_id)
    if config is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    probe = _probe_or_fail()
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        _fail("runtime probe does not implement collect", code=3)
        return
    report = collector(config)
    payload = _jsonable(report)
    if as_json:
        console.print_json(json.dumps(payload, default=str))
        return
    console.print(Panel(json.dumps(payload, indent=2, default=str), title=f"Probe {profile_id}"))


@app.command("suite")
def probe_suite(
    count: int = typer.Option(5, "--count", min=1, help="How many profiles to sample."),  # noqa: B008
    group: str | None = typer.Option(None, "--group", help="Restrict to a group."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),  # noqa: B008
) -> None:
    """Run the full QA suite over a sample of profiles."""
    store = _store_or_fail()
    probe = _probe_or_fail()
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        _fail("runtime probe does not implement collect", code=3)
        return

    lister = getattr(store, "list_profiles", None)
    configs: list[Any] = []
    if lister is not None:
        try:
            configs = list(lister(group_id=group) or [])
        except TypeError:
            configs = list(lister() or [])
    configs = configs[:count]

    reports: list[Any] = []
    for config in configs:
        reports.append(_jsonable(collector(config)))

    metrics = _aggregate(reports)
    if as_json:
        console.print_json(
            json.dumps({"sample_size": len(configs), "metrics": metrics}, default=str)
        )
        return

    table = Table(title=f"QA suite ({len(configs)} profile(s))")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key, value in metrics.items():
        table.add_row(key, str(value))
    console.print(table)


def _aggregate(reports: list[Any]) -> dict[str, Any]:
    """Aggregate the QA metrics of a list of probe reports."""
    if not reports:
        return {
            "distinct_ratio": 0.0,
            "collision_rate": 0.0,
            "persistence": False,
            "consistency_pass": False,
        }

    def _ratio(key: str) -> float:
        values = [
            float(report.get(key, 0.0) or 0.0) for report in reports if isinstance(report, dict)
        ]
        return sum(values) / len(values) if values else 0.0

    dict_reports = [report for report in reports if isinstance(report, dict)]
    return {
        "distinct_ratio": _ratio("distinct_ratio"),
        "collision_rate": _ratio("collision_rate"),
        "persistence": all(bool(report.get("persistence_ok", False)) for report in dict_reports),
        "consistency_pass": all(
            bool(report.get("consistency_pass", False)) for report in dict_reports
        ),
    }


@app.command("persistence")
def probe_persistence(
    profile_id: str = typer.Argument(..., help="Profile id."),  # noqa: B008
    restarts: int = typer.Option(5, "--restarts", min=2, help="How many restarts to perform."),  # noqa: B008
) -> None:
    """Restart a profile's browser repeatedly and verify the fingerprint holds."""
    store = _store_or_fail()
    config = store.get_profile(profile_id)
    if config is None:
        _fail(f"profile {profile_id!r} not found", code=4)
        return
    probe = _probe_or_fail()
    collector = getattr(probe, "collect", None)
    if not callable(collector):
        _fail("runtime probe does not implement collect", code=3)
        return

    try:
        launcher = deps.get_launcher()
    except deps.EngineUnavailableError:
        launcher = None

    launch = getattr(launcher, "launch", None) if launcher is not None else None
    stop = None
    if launcher is not None:
        for attr in ("stop", "stop_profile", "terminate"):
            candidate = getattr(launcher, attr, None)
            if callable(candidate):
                stop = candidate
                break

    fingerprints: list[str] = []
    errors: list[str] = []
    for attempt in range(restarts):
        if callable(launch):
            try:
                launch(config)
            except TypeError:
                try:
                    launch(config, headless=True)
                except Exception as exc:  # noqa: BLE001 - reported per attempt
                    errors.append(f"attempt {attempt + 1}: {exc}")
                    break
            except Exception as exc:  # noqa: BLE001 - reported per attempt
                errors.append(f"attempt {attempt + 1}: {exc}")
                break
        if stop is not None:
            try:
                stop(profile_id)
            except Exception as exc:  # noqa: BLE001 - reported per attempt
                errors.append(f"attempt {attempt + 1}: stop failed: {exc}")
        report = collector(config)
        fingerprints.append(_fingerprint_of(report))

    unique = len(set(fingerprints))
    stable = bool(fingerprints) and unique == 1
    console.print(
        Panel(
            json.dumps(
                {
                    "profile_id": profile_id,
                    "restarts": restarts,
                    "attempted": len(fingerprints),
                    "stable": stable,
                    "distinct_fingerprints": unique,
                    "errors": errors,
                },
                indent=2,
            ),
            title="Persistence test",
        )
    )
    if not stable:
        raise typer.Exit(code=6)


def _fingerprint_of(report: Any) -> str:
    if isinstance(report, dict):
        results = report.get("results")
    else:
        results = getattr(report, "results", None)
    if not results:
        return ""
    parts: list[str] = []
    for item in results:
        if isinstance(item, dict):
            parts.append(f"{item.get('axis')}={item.get('value')}")
        else:
            parts.append(f"{getattr(item, 'axis', '')}={getattr(item, 'value', '')}")
    return "|".join(sorted(parts))


def _store_or_fail() -> Any:
    try:
        return deps.get_store()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}")
        raise  # pragma: no cover - unreachable


def _probe_or_fail() -> Any:
    try:
        return deps.get_probe()
    except deps.EngineUnavailableError as exc:
        _fail(f"{exc}", code=3)
        raise  # pragma: no cover - unreachable


def register(root: typer.Typer) -> None:
    """Attach the ``probe`` command group to *root*."""
    root.add_typer(app, name="probe")
