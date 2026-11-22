"""Tests for the installer builder.

The builder is tested by building.  A mock would not catch the thing
that matters, which is whether the produced directory actually contains
a working installation.

The full build is slow -- it creates a virtual environment and pip
installs the project -- so the slow test is marked.  Everything else
runs on every invocation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from novaprofile.installer import (
    DATA_DIRECTORIES,
    PRODUCT_NAME,
    BuildResult,
    InstallerBuilder,
    InstallerError,
)


@pytest.fixture()
def source_root(tmp_path: Path) -> Path:
    """A source tree that is just complete enough to package.

    The real repository is not used: the test must not depend on the
    working copy, and it must not take an hour.
    """

    root = tmp_path / "src"
    control_plane = root / "novaprofile" / "control_plane"
    package = control_plane / "novaprofile"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text('"""A stub package."""\n', encoding="utf-8")
    (package / "cli").mkdir()
    (package / "cli" / "__init__.py").write_text("", encoding="utf-8")
    # The stub carries two commands, like the real CLI does.  Typer
    # collapses an application with a single command into that command,
    # so a one-command stub would not accept a command name at all.
    (package / "cli" / "main.py").write_text(
        "import typer\n"
        "app = typer.Typer(name='nova', no_args_is_help=True)\n"
        "\n"
        "@app.command('doctor')\n"
        "def doctor() -> None:\n"
        "    print('hello')\n"
        "\n"
        "@app.command('version')\n"
        "def version() -> None:\n"
        "    print('nova 1.2.3')\n",
        encoding="utf-8",
    )
    (control_plane / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=68", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        'name = "novaprofile-stub"\n'
        'version = "0.0.1"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = ["typer>=0.12"]\n'
        "\n"
        "[project.scripts]\n"
        'nova-stub = "novaprofile.cli.main:app"\n'
        "\n"
        "[tool.setuptools.packages.find]\n"
        'where = ["."]\n'
        'include = ["novaprofile*"]\n',
        encoding="utf-8",
    )

    core_patch = root / "novaprofile" / "core_patch" / "src"
    core_patch.mkdir(parents=True)
    (core_patch / "nova_core_config.h").write_text("// header\n", encoding="utf-8")
    (core_patch / "stale.pyc").write_bytes(b"\x00")

    (root / "LICENSE").write_text("All rights reserved.\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# The layout
# --------------------------------------------------------------------------


def test_the_builder_knows_the_product_name() -> None:
    """The installation is named after the product."""

    assert PRODUCT_NAME == "novaprofile"


def test_the_data_directories_are_outside_the_program() -> None:
    """The profile data never lives inside the program.

    Upgrading the program must never touch a customer's profiles, so
    the data directories are siblings of the environment rather than
    inside it.
    """

    assert "profiles" in DATA_DIRECTORIES
    assert "data" in DATA_DIRECTORIES
    assert "env" not in DATA_DIRECTORIES


def test_the_uninstaller_keeps_the_data_by_default() -> None:
    """The uninstaller does not delete the profiles unless asked.

    A hundred thousand profiles are worth more than the program that
    made them, and an upgrade must never destroy them.
    """

    from novaprofile.installer.builder import _UNINSTALLER_SOURCE

    assert "--keep-data" in _UNINSTALLER_SOURCE
    assert "default=True" in _UNINSTALLER_SOURCE
    assert "--delete-data" in _UNINSTALLER_SOURCE


def test_the_uninstaller_is_explicit_about_irreversibility() -> None:
    """The destructive flag says so in its own help text."""

    from novaprofile.installer.builder import _UNINSTALLER_SOURCE

    assert "irreversible" in _UNINSTALLER_SOURCE


# --------------------------------------------------------------------------
# The generated files
# --------------------------------------------------------------------------


def test_the_launcher_resolves_the_interpreter_relatively() -> None:
    """The installation can be moved.

    The interpreter is resolved relative to the launcher, so the
    directory works from any path.  An absolute path baked in at build
    time would break the moment the customer moved the folder.
    """

    from novaprofile.installer.builder import _LAUNCHER_SOURCE

    assert 'HERE / "env"' in _LAUNCHER_SOURCE
    assert str(Path.home()) not in _LAUNCHER_SOURCE


def test_the_launcher_reports_an_incomplete_installation() -> None:
    """A broken installation says so instead of crashing."""

    from novaprofile.installer.builder import _LAUNCHER_SOURCE

    assert "the installation is incomplete" in _LAUNCHER_SOURCE


def test_the_readme_explains_the_uninstall() -> None:
    """The customer can find out how to remove it."""

    from novaprofile.installer.builder import _README_TEMPLATE

    rendered = _README_TEMPLATE.format(version="1.2.3")
    assert "1.2.3" in rendered
    assert "uninstall.py" in rendered
    assert "--delete-data" in rendered


# --------------------------------------------------------------------------
# The build
# --------------------------------------------------------------------------


def test_a_missing_control_plane_is_an_error(tmp_path: Path) -> None:
    """A source tree without the control plane fails clearly."""

    empty = tmp_path / "empty"
    empty.mkdir()
    builder = InstallerBuilder(empty, tmp_path / "out")
    with pytest.raises(InstallerError) as excinfo:
        builder._install_package(tmp_path / "env")
    assert "control plane is missing" in str(excinfo.value)


def test_an_offline_build_without_a_wheelhouse_is_an_error(
    source_root: Path, tmp_path: Path
) -> None:
    """An offline build without the wheels fails before it starts.

    The failure is at the check, not halfway through a long build.
    """

    builder = InstallerBuilder(source_root, tmp_path / "out", offline=True)
    with pytest.raises(InstallerError) as excinfo:
        builder._install_package(tmp_path / "env")
    assert "wheelhouse" in str(excinfo.value)


def test_the_stale_bytecode_is_not_copied(source_root: Path, tmp_path: Path) -> None:
    """Compiled leftovers do not travel with the installation."""

    builder = InstallerBuilder(source_root, tmp_path / "out")
    staging = tmp_path / "staging"
    staging.mkdir()
    builder._copy_core_patch(staging)
    copied = list((staging / "core_patch").rglob("*.pyc"))
    assert not copied
    assert (staging / "core_patch" / "src" / "nova_core_config.h").exists()


def test_the_build_result_summarises_itself(tmp_path: Path) -> None:
    """The summary is one line and carries the size."""

    result = BuildResult(
        root=tmp_path / "novaprofile-1.0.0",
        version="1.0.0",
        files=12,
        total_bytes=3 * 1024 * 1024,
    )
    summary = result.summary()
    assert "novaprofile-1.0.0" in summary
    assert "12 files" in summary
    assert "3.0 MB" in summary


@pytest.mark.slow
def test_the_build_produces_a_runnable_installation(
    source_root: Path, tmp_path: Path
) -> None:
    """The produced directory actually runs.

    This is the test that matters: it builds the installation and then
    runs the entry point in it.  A builder that produces a directory
    which cannot start is worthless, and no amount of structural
    assertion would have caught it.
    """

    builder = InstallerBuilder(source_root, tmp_path / "out", version="1.2.3")
    result = builder.build()

    assert result.root == tmp_path / "out" / "novaprofile-1.2.3"
    assert result.root.exists()
    assert (result.root / "launch.py").exists()
    assert (result.root / "uninstall.py").exists()
    assert (result.root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.3"
    assert (result.root / "config" / "default.json").exists()
    for name in DATA_DIRECTORIES:
        assert (result.root / name).is_dir()

    config = json.loads((result.root / "config" / "default.json").read_text())
    assert config["version"] == "1.2.3"

    # The entry point runs and the CLI is reachable.  The stub package
    # carries a ``doctor`` command, so running it proves the whole chain
    # works: the interpreter was found, the site-packages were added,
    # the package was imported and the command was dispatched.
    completed = subprocess.run(  # noqa: S603 - the command is built here
        [sys.executable, str(result.root / "launch.py"), "doctor"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "hello" in completed.stdout

    shutil.rmtree(result.root, ignore_errors=True)
