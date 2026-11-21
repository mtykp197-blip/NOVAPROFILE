"""Restricting access to the files that hold secrets.

The master key protects every proxy credential and every encrypted secret in
the installation.  On Windows the POSIX permission bits are ignored, so a
file created with mode ``0o600`` still inherits the ACL of its directory and
may be readable by other accounts on the machine.

This module applies the Windows equivalent: an explicit access control list
that grants the current user full control and nobody else anything.  On
other platforms it falls back to the POSIX mode, which is honoured there.

The functions are best-effort by design.  A file that cannot be tightened
is reported, but it never raises: refusing to write a key because the
filesystem does not support ACLs would be worse than writing it with the
permissions the platform does support.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: The owner-only mode used on POSIX.
OWNER_ONLY_MODE: int = stat.S_IRUSR | stat.S_IWUSR


@dataclass(frozen=True)
class HardeningResult:
    """What happened when a file was tightened."""

    path: Path
    hardened: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.hardened


def _current_user() -> str:
    """The account that owns the current process.

    On Windows the name is resolved from the token; on POSIX the login name
    is enough.
    """

    if sys.platform == "win32":
        import getpass

        return getpass.getuser()
    import pwd

    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError):  # pragma: no cover - unusual environments
        return os.environ.get("USER", "")


def harden_file(path: Path | str) -> HardeningResult:
    """Restrict a file to the current user.

    On Windows the ACL is rewritten through ``icacls``: the file is reset
    to inherit nothing, the current user is granted full control, and
    system accounts keep the access they need to keep the machine working.
    On POSIX the mode is set to ``0600``.
    """

    target = Path(path)
    if not target.exists():
        return HardeningResult(path=target, hardened=False, detail="the file does not exist")

    if sys.platform == "win32":
        return _harden_windows(target)

    try:
        os.chmod(target, OWNER_ONLY_MODE)
    except OSError as exc:
        return HardeningResult(path=target, hardened=False, detail=str(exc))
    return HardeningResult(path=target, hardened=True, detail="the mode was set to 0600")


def _harden_windows(target: Path) -> HardeningResult:
    """Rewrite the ACL of a file so that only the current user may read it."""

    user = _current_user()
    if not user:
        return HardeningResult(
            path=target, hardened=False, detail="the current user could not be resolved"
        )

    # ``icacls`` is always present on a Windows installation.  The sequence
    # removes the inheritance, grants the user full control, and leaves the
    # system accounts with the access Windows itself needs.
    commands = (
        ["icacls", str(target), "/inheritance:r"],
        ["icacls", str(target), "/grant:r", f"{user}:(F)"],
        ["icacls", str(target), "/grant:r", "SYSTEM:(F)"],
        ["icacls", str(target), "/grant:r", "Administrators:(F)"],
    )

    details: list[str] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return HardeningResult(path=target, hardened=False, detail=str(exc))
        if completed.returncode != 0:
            details.append((completed.stderr or completed.stdout or "").strip())
            return HardeningResult(
                path=target,
                hardened=False,
                detail="; ".join(details) or "icacls reported an error",
            )

    return HardeningResult(path=target, hardened=True, detail=f"the ACL grants only {user}")


def harden_directory(path: Path | str) -> HardeningResult:
    """Restrict a directory to the current user.

    A directory that holds secrets must not be traversable by other
    accounts either: a readable directory reveals the file names.
    """

    target = Path(path)
    if not target.is_dir():
        return HardeningResult(path=target, hardened=False, detail="the directory does not exist")

    if sys.platform == "win32":
        user = _current_user()
        if not user:
            return HardeningResult(
                path=target, hardened=False, detail="the current user could not be resolved"
            )
        for command in (
            ["icacls", str(target), "/inheritance:r"],
            ["icacls", str(target), "/grant:r", f"{user}:(OI)(CI)(F)"],
            ["icacls", str(target), "/grant:r", "SYSTEM:(OI)(CI)(F)"],
            ["icacls", str(target), "/grant:r", "Administrators:(OI)(CI)(F)"],
        ):
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return HardeningResult(path=target, hardened=False, detail=str(exc))
            if completed.returncode != 0:
                return HardeningResult(
                    path=target,
                    hardened=False,
                    detail=(completed.stderr or completed.stdout or "").strip(),
                )
        return HardeningResult(path=target, hardened=True, detail=f"the ACL grants only {user}")

    try:
        os.chmod(target, stat.S_IRWXU)
    except OSError as exc:
        return HardeningResult(path=target, hardened=False, detail=str(exc))
    return HardeningResult(path=target, hardened=True, detail="the mode was set to 0700")


__all__ = [
    "OWNER_ONLY_MODE",
    "HardeningResult",
    "harden_directory",
    "harden_file",
]
