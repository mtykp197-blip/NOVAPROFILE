"""Tests for the ``nova`` command line interface.

The CLI tests are tolerant of the engine still being under construction: the
commands must never crash with an ``ImportError`` traceback and must return a
sensible exit code.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from novaprofile.api import deps
from novaprofile.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point every path helper at a temporary directory."""
    monkeypatch.setenv("NOVA_PROFILE_HOME", str(tmp_path / "home"))
    return tmp_path


def test_doctor_exits_zero_and_prints_sections() -> None:
    """``nova doctor`` succeeds and prints its diagnostic sections."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "NOVA Profile Engine" in result.output
    assert "Python" in result.output
    assert "Browser binary" in result.output
    assert "State DB" in result.output
    assert "Engine modules" in result.output


def test_profile_list_json_is_valid() -> None:
    """``nova profile list --json`` prints a valid JSON array."""
    result = runner.invoke(app, ["profile", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)


def test_profile_list_without_json() -> None:
    """``nova profile list`` succeeds even when the store is empty."""
    result = runner.invoke(app, ["profile", "list"])
    assert result.exit_code == 0, result.output


def test_unknown_command_exits_non_zero() -> None:
    """An unknown command returns a non-zero exit code."""
    result = runner.invoke(app, ["definitely-not-a-command"])
    assert result.exit_code != 0


def test_unknown_profile_show_exits_non_zero() -> None:
    """Showing an unknown profile fails with a non-zero exit code."""
    result = runner.invoke(app, ["profile", "show", "does-not-exist"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_help_is_available() -> None:
    """The root help screen lists the command groups."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for expected in ("profile", "preset", "batch", "probe", "doctor"):
        assert expected in result.output


def test_version_command() -> None:
    """``nova version`` prints the package version."""
    from novaprofile import __version__

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_preset_list_is_resilient() -> None:
    """``nova preset list`` never raises an import error."""
    result = runner.invoke(app, ["preset", "list"])
    assert result.exit_code == 0, result.output


def test_preset_list_json_is_valid() -> None:
    """``nova preset list --json`` prints a valid JSON array."""
    result = runner.invoke(app, ["preset", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)


def test_store_unavailable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing store produces a clear message, not a traceback."""

    def _boom() -> Any:
        raise deps.ModuleUnavailableError("novaprofile.registry.store", "not available")

    monkeypatch.setattr(deps, "get_store", _boom)
    result = runner.invoke(app, ["profile", "list"])
    assert result.exit_code != 0
    assert "not available" in result.output
    assert "Traceback" not in result.output
