"""Per-profile storage isolation for the NOVA browser launcher.

Every profile owns a private Chromium ``user_data_dir`` underneath
``%LOCALAPPDATA%\\NovaProfile\\profiles\\<profile_id>``.  Keeping the
directory per profile means cookies, local storage, IndexedDB, the GPU cache
and the service-worker database never leak from one profile into another.

The module also knows how to *clean* a profile:

* :func:`ProfileIsolation.cleanup_lock_files` removes the singleton lock
  artefacts Chromium leaves behind when it is killed (they otherwise block the
  next start with "profile is already in use");
* :func:`ProfileIsolation.wipe_storage` removes the persisted state so a
  profile can be cloned into a pristine one while keeping its configuration.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

#: Environment variable that overrides the root of the NOVA storage tree.
HOME_ENV_VAR: str = "NOVA_PROFILE_HOME"

#: Name of the directory holding one ``user_data_dir`` per profile.
PROFILES_DIRNAME: str = "profiles"

#: Name of the directory holding the per-profile session logs.
LOGS_DIRNAME: str = "logs"

#: Singleton artefacts Chromium creates next to the profile directory.
LOCK_FILENAMES: tuple[str, ...] = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
)

#: Suffixes of stale scratch files that must never survive a restart.
STALE_SUFFIXES: tuple[str, ...] = (".tmp", ".old")

#: Storage directories/files that :func:`wipe_storage` removes.
#: Relative to the ``user_data_dir``.
STORAGE_ENTRIES: tuple[str, ...] = (
    "Cookies",
    "Cookies-journal",
    "Cookies-journal",
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "CacheStorage",
    "Code Cache",
    "GPUCache",
    "Service Worker",
    "File System",
    "Extensions",
    "WebStorage",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "MediaDevices",
    "Network",
    "blob_storage",
    "Trust Tokens",
    "Reporting",
    "Shared Dictionary",
    "OptimizationHints",
    "File System Access",
)

#: Directories inside ``user_data_dir`` that must survive a storage wipe.
PRESERVED_ENTRIES: tuple[str, ...] = (
    "Default",
    "Local State",
    "Preferences",
    "Secure Preferences",
    "nova_core.json",
    "Bookmarks",
    "Favicons",
    "History",
    "Login Data",
)


def default_home() -> Path:
    """Return the root of the NOVA storage tree.

    ``%LOCALAPPDATA%\\NovaProfile`` on Windows, ``~/.NovaProfile`` elsewhere.
    The ``NOVA_PROFILE_HOME`` environment variable always wins, which makes
    the whole layout trivially testable.
    """

    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "NovaProfile"
    return Path.home() / ".NovaProfile"


class ProfileIsolation:
    """Manage the on-disk footprint of the browser profiles."""

    def __init__(self, home: Path | str | None = None) -> None:
        self._home = Path(home).expanduser() if home is not None else default_home()

    # -- layout ------------------------------------------------------------

    @property
    def home(self) -> Path:
        """The root of the NOVA storage tree."""

        return self._home

    @property
    def profiles_root(self) -> Path:
        """The directory holding one sub-directory per profile."""

        return self._home / PROFILES_DIRNAME

    @property
    def logs_root(self) -> Path:
        """The directory holding the per-profile session logs."""

        return self._home / LOGS_DIRNAME

    def user_data_dir(self, profile_id: str) -> Path:
        """Return the ``user_data_dir`` of ``profile_id`` (not created)."""

        return self.profiles_root / profile_id

    def profile_dir(self, profile_id: str) -> Path:
        """Return the per-profile working directory.

        The ``user_data_dir`` lives inside it so that the launcher can keep
        auxiliary files (the core config, session logs) next to the browser
        state without polluting the Chromium directory.
        """

        return self.profiles_root / profile_id

    def log_dir(self, profile_id: str) -> Path:
        """Return the directory holding the session logs of ``profile_id``."""

        return self.logs_root / profile_id

    # -- creation ----------------------------------------------------------

    def create(self, profile_id: str) -> Path:
        """Create the profile directory tree and return the ``user_data_dir``."""

        target = self.user_data_dir(profile_id)
        target.mkdir(parents=True, exist_ok=True)
        # Chromium expects these sub-directories to exist for a first run.
        for sub in ("Default",):
            (target / sub).mkdir(parents=True, exist_ok=True)
        return target

    def ensure_logs(self, profile_id: str) -> Path:
        """Create and return the log directory of ``profile_id``."""

        target = self.log_dir(profile_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    # -- cleaning ----------------------------------------------------------

    @staticmethod
    def cleanup_lock_files(user_data_dir: Path | str) -> list[Path]:
        """Remove the Chromium singleton lock artefacts.

        Returns the list of files that were actually removed.  Files that do
        not exist are silently skipped; unrelated files are never touched.
        """

        removed: list[Path] = []
        root = Path(user_data_dir)
        if not root.exists():
            return removed
        for name in LOCK_FILENAMES:
            candidate = root / name
            if candidate.exists() and candidate.is_file():
                try:
                    candidate.unlink()
                except OSError:
                    continue
                removed.append(candidate)
        for candidate in sorted(root.glob("*")):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() in STALE_SUFFIXES:
                try:
                    candidate.unlink()
                except OSError:
                    continue
                removed.append(candidate)
        return removed

    @staticmethod
    def wipe_storage(user_data_dir: Path | str) -> None:
        """Remove cookies, storage and caches from a ``user_data_dir``.

        The profile *configuration* (the Chromium ``Preferences`` files and the
        NOVA core config) is preserved -- only the runtime state is dropped,
        which is exactly what "clone into a clean profile" means.
        """

        root = Path(user_data_dir)
        if not root.exists():
            return
        for name in STORAGE_ENTRIES:
            target = root / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                with contextlib.suppress(OSError):
                    target.unlink()
        # The per-profile "Default" profile also carries storage.
        default_profile = root / "Default"
        if default_profile.is_dir():
            for name in STORAGE_ENTRIES:
                target = default_profile / name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    with contextlib.suppress(OSError):
                        target.unlink()

    # -- introspection -----------------------------------------------------

    @staticmethod
    def list_profiles(home: Path | str | None = None) -> list[Path]:
        """List the profile directories under the NOVA home."""

        root = Path(home).expanduser() if home is not None else default_home()
        profiles_root = root / PROFILES_DIRNAME
        if not profiles_root.is_dir():
            return []
        return sorted(p for p in profiles_root.iterdir() if p.is_dir())

    def remove_profile(self, profile_id: str) -> bool:
        """Delete the whole profile directory tree.  Returns ``True`` on success."""

        target = self.user_data_dir(profile_id)
        if not target.exists():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return not target.exists()


__all__ = [
    "HOME_ENV_VAR",
    "LOCK_FILENAMES",
    "LOGS_DIRNAME",
    "PRESERVED_ENTRIES",
    "PROFILES_DIRNAME",
    "STALE_SUFFIXES",
    "STORAGE_ENTRIES",
    "ProfileIsolation",
    "default_home",
]
