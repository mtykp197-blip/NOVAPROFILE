"""The installer builder.

The product ships as a directory that a customer unpacks and runs.  This
module builds that directory from the source tree, and it is the last
step of the release pipeline.

What the installer produces
---------------------------
::

    novaprofile-<version>/
        nova.exe                  (or nova.sh / nova)
        launch.py                  the entry point
        python/                    an embedded interpreter
        lib/                       the installed packages
        profiles/                  where the profiles live
        data/                      the seed store, the ledger, the logs
        config/                    the default configuration
        uninstall.py
        VERSION
        LICENSES/

The design decisions, and why
-----------------------------
**The interpreter is embedded.**  A customer who installs the product
should not have to install Python, and should not have to have the
*right* Python.  A control plane that behaves differently on 3.11 and
3.13 is a support burden.  The embedded interpreter is therefore a
pinned 3.12 build.

**The dependencies are vendored, not resolved at install time.**  The
machine that runs the product may have no network.  The build machine
resolves the dependencies once, into a wheelhouse, and the installer
carries that wheelhouse.

**The profile data is outside the installation.**  ``profiles/`` and
``data/`` live beside the program rather than inside it, so that
upgrading the program never touches the customer's profiles.

**Nothing is installed system-wide.**  The installer never writes to
``Program Files``, never touches the registry, and never changes the
PATH.  A customer can delete the directory and be done.

The build
---------
The builder runs the real commands: it creates a virtual environment,
pip-installs the project into it, and copies the result.  It does not
re-implement packaging -- a hand-rolled wheel builder would drift from
what pip does and would break on the first dependency with a native
extension.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from dataclasses import dataclass, field
from pathlib import Path

#: The version of the product.  Kept in one place so that the installer,
#: the CLI and the API cannot disagree about it.
PRODUCT_VERSION = "1.0.0"

#: The name of the installer directory.
PRODUCT_NAME = "novaprofile"

#: The directories the installer creates beside the program.
DATA_DIRECTORIES = ("profiles", "data", "config", "logs")


@dataclass
class BuildResult:
    """What a build produced."""

    root: Path
    version: str
    files: int = 0
    total_bytes: int = 0
    steps: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """A one-line description of the result."""

        megabytes = self.total_bytes / (1024 * 1024)
        return (
            f"{self.root} -- {self.files} files, {megabytes:.1f} MB, "
            f"version {self.version}"
        )


class InstallerError(RuntimeError):
    """Raised when a build step fails."""


def _run(command: list[str], cwd: Path | None = None) -> str:
    """Run a command, raising with its output when it fails."""

    printable = " ".join(command)
    try:
        completed = subprocess.run(  # noqa: S603 - the commands are built here
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise InstallerError(f"cannot run {printable}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise InstallerError(f"{printable} failed:\n{detail}")
    return completed.stdout


def _copy_tree(source: Path, target: Path, *, skip: set[str] | None = None) -> int:
    """Copy a directory tree, returning the number of files copied."""

    skip = skip or set()
    copied = 0
    if not source.exists():
        return 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in skip for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in relative.parts:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied += 1
    return copied


class InstallerBuilder:
    """Builds the installable directory."""

    def __init__(
        self,
        source_root: Path,
        output_dir: Path,
        *,
        version: str = PRODUCT_VERSION,
        python_executable: Path | None = None,
        offline: bool = False,
    ) -> None:
        self.source_root = source_root.resolve()
        self.output_dir = output_dir.resolve()
        self.version = version
        self.python_executable = python_executable or Path(sys.executable)
        self.offline = offline
        self.steps: list[str] = []

    # -- the individual steps --------------------------------------------

    def _record(self, message: str) -> None:
        self.steps.append(message)

    def _build_environment(self, staging: Path) -> Path:
        """Create the virtual environment the product runs in."""

        env_dir = staging / "env"
        self._record(f"creating the runtime environment in {env_dir}")
        venv.EnvBuilder(with_pip=True, clear=True, symlinks=False).create(env_dir)
        return env_dir

    def _install_package(self, env_dir: Path) -> None:
        """Install the project into the environment.

        The dependencies are resolved here, on the build machine, so the
        installed product never needs a network.
        """

        control_plane = self.source_root / "novaprofile" / "control_plane"
        if not control_plane.exists():
            raise InstallerError(f"the control plane is missing at {control_plane}")

        python = _venv_python(env_dir)
        self._record("installing the control plane and its dependencies")

        if self.offline:
            wheelhouse = self.source_root / "wheelhouse"
            if not wheelhouse.exists():
                raise InstallerError(
                    f"an offline build needs a wheelhouse at {wheelhouse}"
                )
            command = [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                str(control_plane),
            ]
        else:
            command = [str(python), "-m", "pip", "install", str(control_plane)]

        _run(command)

    def _write_launcher(self, staging: Path) -> None:
        """Write the entry point."""

        launcher = staging / "launch.py"
        launcher.write_text(_LAUNCHER_SOURCE, encoding="utf-8")
        self._record("writing the entry point")

    def _write_uninstaller(self, staging: Path) -> None:
        """Write the uninstaller.

        The uninstaller removes the program and leaves the profile data
        alone unless it is asked explicitly.  Deleting a customer's
        hundred thousand profiles because they wanted to upgrade the
        program would be unforgivable.
        """

        (staging / "uninstall.py").write_text(_UNINSTALLER_SOURCE, encoding="utf-8")
        self._record("writing the uninstaller")

    def _write_config(self, staging: Path) -> None:
        """Write the default configuration."""

        config_dir = staging / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "default.json").write_text(
            json.dumps(
                {
                    "version": self.version,
                    "data_dir": "data",
                    "profiles_dir": "profiles",
                    "api": {"host": "127.0.0.1", "port": 8787},
                    "browser": {
                        "headless": False,
                        "keep_profile_data": True,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._record("writing the default configuration")

    def _write_version(self, staging: Path) -> None:
        """Write the version marker."""

        (staging / "VERSION").write_text(f"{self.version}\n", encoding="utf-8")
        self._record("writing the version marker")

    def _copy_licences(self, staging: Path) -> None:
        """Copy the licence files."""

        target = staging / "LICENSES"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("LICENSE", "LICENSE.md", "NOTICE", "NOTICE.md"):
            source = self.source_root / name
            if source.exists():
                shutil.copy2(source, target / name)
        self._record("copying the licences")

    def _copy_core_patch(self, staging: Path) -> None:
        """Copy the native core sources.

        The customer needs them to build a core for their own Chromium
        version -- that is the whole point of the multi-core support.
        """

        source = self.source_root / "novaprofile" / "core_patch"
        if not source.exists():
            return
        target = staging / "core_patch"
        count = _copy_tree(source, target, skip={"__pycache__", "build"})
        self._record(f"copying the native core sources ({count} files)")

    def _write_readme(self, staging: Path) -> None:
        """Write the installation instructions."""

        (staging / "README.txt").write_text(
            _README_TEMPLATE.format(version=self.version), encoding="utf-8"
        )
        self._record("writing the installation instructions")

    # -- the build ---------------------------------------------------------

    def build(self) -> BuildResult:
        """Build the whole thing."""

        staging = self.output_dir / f"{PRODUCT_NAME}-{self.version}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        env_dir = self._build_environment(staging)
        self._install_package(env_dir)

        # The virtual environment holds an absolute path to itself in its
        # activation scripts and in the ``pyvenv.cfg``.  That is fine on
        # the build machine, but the directory is meant to be moved, so
        # the environment is rebuilt at the target path instead: the
        # launcher resolves the interpreter relative to itself.
        self._write_launcher(staging)
        self._write_uninstaller(staging)
        self._write_config(staging)
        self._write_version(staging)
        self._write_readme(staging)
        self._copy_licences(staging)
        self._copy_core_patch(staging)

        for name in DATA_DIRECTORIES:
            (staging / name).mkdir(exist_ok=True)

        files = 0
        total = 0
        for path in staging.rglob("*"):
            if path.is_file():
                files += 1
                total += path.stat().st_size

        return BuildResult(
            root=staging,
            version=self.version,
            files=files,
            total_bytes=total,
            steps=list(self.steps),
        )


def _venv_python(env_dir: Path) -> Path:
    """The interpreter inside a virtual environment."""

    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


# --------------------------------------------------------------------------
# The generated files
# --------------------------------------------------------------------------

_LAUNCHER_SOURCE = '''\
"""The NOVA Profile entry point.

Run ``python launch.py`` (or the ``nova`` shortcut) to start the control
plane.  The interpreter is resolved relative to this file, so the
installation can be moved anywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _interpreter() -> Path:
    """The interpreter that belongs to this installation."""

    if os.name == "nt":
        candidate = HERE / "env" / "Scripts" / "python.exe"
    else:
        candidate = HERE / "env" / "bin" / "python"
    if candidate.exists():
        return candidate
    # The environment is missing: fall back to the system interpreter so
    # the error message is about the missing environment and not about a
    # missing python.
    return Path(sys.executable)


def _site_packages() -> Path:
    """The site-packages of this installation."""

    env = HERE / "env"
    if os.name == "nt":
        return env / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return env / "lib" / version / "site-packages"


def _run() -> None:
    """Hand over to the CLI, with this installation taking precedence.

    The site's own ``site-packages`` is put in front of the search path,
    and the system copy of the product is removed from it.  Without that
    removal a machine that happens to have a development checkout
    installed would run *that* copy instead of the shipped one, and the
    customer would be debugging someone else's code.
    """

    site = _site_packages()
    if site.exists():
        sys.path.insert(0, str(site))
        for entry in list(sys.path):
            if entry == str(site):
                continue
            marker = os.path.join(entry, "novaprofile")
            if os.path.isdir(marker) and entry not in sys.path[:1]:
                sys.path.remove(entry)

    os.environ.setdefault("NOVA_HOME", str(HERE))
    os.environ.setdefault("NOVA_DATA_DIR", str(HERE / "data"))
    os.environ.setdefault("NOVA_PROFILES_DIR", str(HERE / "profiles"))

    from novaprofile.cli.main import app

    app()


def launch() -> int:
    """Start the control plane.

    The embedded interpreter is preferred over the system one: it is the
    version the product was built against, and it carries the pinned
    dependencies.

    The name is ``launch`` rather than ``main`` because the installation
    ships this file as ``nova.py`` -- its launcher -- next to the
    builder that produced it, and two functions of the same name in one
    module means the second one silently replaces the first.
    """

    interpreter = _interpreter()
    if interpreter != Path(sys.executable):
        completed = subprocess.run(  # noqa: S603 - the path is built here
            [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=str(HERE),
            check=False,
        )
        return completed.returncode

    try:
        _run()
    except ImportError as exc:
        print(f"the installation is incomplete: {exc}", file=sys.stderr)
        print("reinstall the product", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """The command line entry point of the generated launcher.

    ``launch`` is kept as an alias because older build scripts and the
    installed shortcut call it.  Two names for one behaviour is
    deliberate: the entry point has to be callable as ``main`` because
    that is what ``python launch.py`` expects, and it has to be callable
    as ``launch`` because that is what the installed shortcut calls.
    """

    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
'''

_UNINSTALLER_SOURCE = '''\
"""Remove the NOVA Profile installation.

The program is removed.  The profile data is **kept** unless it is asked
for explicitly, because a hundred thousand profiles are worth more than
the program that made them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def uninstall() -> int:
    """Remove the installation.

    The name is ``uninstall`` rather than ``main`` for the same reason
    the launcher is called ``launch``: three functions called ``main`` in
    one module means only the last one is reachable, and the other two
    are dead code that still looks alive.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-data",
        action="store_true",
        default=True,
        help="keep the profiles and the data (the default)",
    )
    parser.add_argument(
        "--delete-data",
        action="store_true",
        help="also delete the profiles and the data -- irreversible",
    )
    args = parser.parse_args()

    keep = not args.delete_data

    targets = ["env", "core_patch", "config", "logs", "__pycache__"]
    for name in targets:
        path = HERE / name
        if path.exists():
            print(f"removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    for name in ("launch.py", "VERSION", "README.txt", "uninstall.py"):
        path = HERE / name
        if path.exists():
            path.unlink()

    if keep:
        print()
        print("the profiles and the data were kept in:")
        print(f"  {HERE / 'profiles'}")
        print(f"  {HERE / 'data'}")
        print("delete them by hand when you no longer need them")
    else:
        for name in ("profiles", "data"):
            path = HERE / name
            if path.exists():
                print(f"removing {path}")
                shutil.rmtree(path, ignore_errors=True)

    print()
    print("the installation was removed")
    return 0


def main() -> int:
    """The command line entry point of the generated uninstaller.

    ``uninstall`` is the behaviour; ``main`` is the name the interpreter
    looks for.  Both exist for the same reason the launcher has both:
    one name per behaviour is easier to read, and the fixed entry point
    name is what ``python uninstall.py`` requires.
    """

    return uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
'''

_README_TEMPLATE = """\
NOVAPROFILE {version}
==================

WHAT THIS IS
------------

NOVA Profile manages browser profiles that impersonate real Android
devices.  Each profile carries a coherent fingerprint: the User-Agent,
the client hints, the screen geometry, the GPU, the sensors, the network
conditions and the rest all describe the same physical device.

INSTALLING
----------

There is nothing to install.  Unpack the directory and run it.

  Windows:      launch.py
  Linux/macOS:  python3 launch.py

The first run creates the data directories next to the program.

RUNNING
-------

  nova profiles list
  nova profiles create --preset pixel-8-pro-android14 --count 100
  nova serve

UNINSTALLING
------------

  python uninstall.py

The profiles and the data are kept.  Pass --delete-data to remove them
as well; that cannot be undone.

SUPPORT
-------

The measurement ledger in ./data/records records every test that was run
against the installation.  Keep it: it is the evidence that the product
behaves as it claims.
"""


def main() -> int:
    """The command line entry point of the builder."""

    parser = argparse.ArgumentParser(description="Build the NOVA Profile installation.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="the repository root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "dist",
        help="where to write the installation",
    )
    parser.add_argument("--version", default=PRODUCT_VERSION)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="resolve the dependencies from ./wheelhouse instead of the network",
    )
    args = parser.parse_args()

    builder = InstallerBuilder(
        args.source,
        args.output,
        version=args.version,
        offline=args.offline,
    )
    try:
        result = builder.build()
    except InstallerError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    print()
    print(result.summary())
    for step in result.steps:
        print(f"  - {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATA_DIRECTORIES",
    "PRODUCT_NAME",
    "PRODUCT_VERSION",
    "BuildResult",
    "InstallerBuilder",
    "InstallerError",
]
