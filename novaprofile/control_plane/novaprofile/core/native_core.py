"""The state of the native core.

The native core is the Chromium build that carries the NOVA patch series.
It is what serves the surfaces the control plane cannot: the user agent
that the browser itself reports, the screen geometry, the WebGL strings,
the canvas readback.

The control plane generates a profile either way -- the JavaScript
runtime in the page can carry the same values -- but the two are not
interchangeable.  A page that compares what the *accessor* returns with
what a *shim* returns sees the difference, so the operator has to know
which one is in use before a fleet goes out.

The check is deliberately read-only.  It looks at what is installed and
reports what it finds; it never builds, downloads or modifies anything.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "core_status",
    "installed_core_summary",
]


def _cores_root() -> Path:
    """The directory the installed cores live in.

    The location comes from the same place the rest of the control plane
    uses, so that the doctor and the launcher cannot disagree about
    where a core is.
    """

    from ..launcher.isolation import default_home

    return default_home() / "cores"


def installed_core_summary() -> list[tuple[str, str]]:
    """The installed cores as ``(version, path)`` pairs.

    A core that cannot be read is skipped rather than reported as an
    error: the doctor reports the state of the installation, and a
    directory without a manifest is simply not a core.
    """

    from .cores import CoreManager

    try:
        manager = CoreManager()
        records = manager.list_cores()
    except Exception:
        # The core tree is optional.  A control plane that cannot read it
        # still generates profiles.
        return []

    return [(record.version, str(record.path)) for record in records]


def core_status() -> tuple[bool, str]:
    """Whether a native core is installed, and a description of the state.

    Returns ``(ok, detail)``.  ``ok`` is ``True`` when at least one core
    is installed and its executable is present.

    The detail always says what was found, including when the answer is
    "none" -- an operator reading the doctor needs to know whether the
    machine has no core at all or has one that is broken.
    """

    cores = installed_core_summary()
    if not cores:
        return False, (
            "no native core is installed -- the profiles run on a stock "
            "browser. Build one with 'build.py' and install it, or accept "
            "that the JavaScript runtime carries the surfaces."
        )

    from .cores import CoreManager

    try:
        manager = CoreManager()
        records = manager.list_cores()
    except Exception:
        records = []

    usable: list[str] = []
    for record in records:
        executable = record.manifest.executable
        if not executable:
            continue
        candidate = (
            Path(executable)
            if Path(executable).is_absolute()
            else record.path / executable
        )
        if candidate.is_file():
            usable.append(record.version)

    if not usable:
        listed = ", ".join(version for version, _ in cores)
        return False, (
            f"{len(cores)} core(s) are installed ({listed}) but none of "
            "them carries a browser executable -- the installation is "
            "incomplete."
        )

    newest = usable[-1]
    return True, f"core {newest} is installed ({len(cores)} in total)"
