"""Multi-core manager: install, verify and select browser cores.

A NOVA installation may hold *several* Chromium cores side by side -- one
per major version, or one per release channel.  Each core owns its own
directory, its own configuration and its own manifest.

This module provides:

* :class:`CoreManager` -- the directory layout, the manifest read/write, the
  integrity seal and the verification of an installed core;
* :class:`CoreSelector` -- the resolution of a *requested* major version onto
  an *installed* core, with an explicit fallback policy;
* :func:`seal_directory` / :func:`verify_directory` -- the integrity
  mechanism, which hashes the core's files while ignoring the volatile
  scratch files a running browser produces.

The integrity rule
------------------
A core is sealed by hashing every regular file in its directory except the
volatile ones.  The excluded patterns are the ones a *running* browser
creates or rewrites constantly -- log files, temporary files, crash dumps
and editor backups.  Sealing them would make every verification fail, which
would make the mechanism useless.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .versions import (
    CoreFallbackPolicy,
    CoreInstallError,
    CoreManifest,
    CoreSelection,
    ReleaseChannel,
    VersionRegistry,
)

#: The name of the manifest file inside a core directory.
MANIFEST_FILENAME: str = "core.json"

#: The name of the integrity seal file inside a core directory.
SEAL_FILENAME: str = "core.seal.json"

#: The sub-directory of the NOVA home that holds the installed cores.
CORES_DIRNAME: str = "cores"

#: File suffixes that a running browser rewrites constantly.  They are
#: excluded from the integrity seal.
VOLATILE_SUFFIXES: tuple[str, ...] = (
    ".log",
    ".tmp",
    ".old",
    ".swp",
    ".dmp",
    ".crash",
)

#: File names that are excluded from the integrity seal.
VOLATILE_NAMES: tuple[str, ...] = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
    "chrome_debug.log",
)

#: Directory names that never contain sealable content.
VOLATILE_DIRS: tuple[str, ...] = (
    "logs",
    "log",
    "tmp",
    "temp",
    "crashpad",
    "Crashpad",
)


def _is_volatile(path: Path, root: Path) -> bool:
    """Whether a path must be excluded from the integrity seal."""

    name = path.name
    if name in VOLATILE_NAMES:
        return True
    if path.suffix.lower() in VOLATILE_SUFFIXES:
        return True
    try:
        relative = path.relative_to(root)
    except ValueError:  # pragma: no cover - defensive
        return False
    return any(part in VOLATILE_DIRS for part in relative.parts)


def _hash_file(path: Path) -> str:
    """SHA-256 of a single file, streamed so that large binaries are fine."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal_directory(root: Path | str) -> dict[str, str]:
    """Hash every non-volatile file in ``root``.

    Returns a mapping of the relative path to its SHA-256.  The result is
    stable: the same directory content always produces the same mapping,
    regardless of the order the filesystem reports the entries in.
    """

    base = Path(root).expanduser()
    if not base.is_dir():
        raise CoreInstallError(f"core directory does not exist: {base}")

    digests: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name == SEAL_FILENAME:
            continue
        if _is_volatile(path, base):
            continue
        relative = path.relative_to(base).as_posix()
        digests[relative] = _hash_file(path)
    return digests


def verify_directory(root: Path | str, seal: dict[str, str]) -> tuple[bool, list[str], list[str]]:
    """Verify a sealed directory against its recorded digests.

    Returns ``(ok, missing, modified)``:

    * ``missing`` -- files that the seal lists but that no longer exist;
    * ``modified`` -- files whose content changed, plus files that exist but
      were not in the seal.
    """

    base = Path(root).expanduser()
    current = seal_directory(base)

    missing = sorted(set(seal) - set(current))
    modified = sorted(path for path in set(seal) & set(current) if seal[path] != current[path])
    unexpected = sorted(set(current) - set(seal))
    return (
        (not missing and not modified and not unexpected),
        missing,
        [
            *modified,
            *unexpected,
        ],
    )


@dataclass
class CoreRecord:
    """An installed core together with its resolved manifest."""

    manifest: CoreManifest
    path: Path
    sealed: bool = False
    seal_digests: dict[str, str] = field(default_factory=dict)
    last_verified_at: datetime | None = None

    @property
    def core_id(self) -> str:
        return self.manifest.core_id

    @property
    def major(self) -> int:
        return self.manifest.major

    @property
    def version(self) -> str:
        return self.manifest.version


class CoreManager:
    """Owns the installed cores and their manifests.

    The layout under the NOVA home is::

        cores/
          120.0.6099.144/
            core.json          the manifest
            core.seal.json     the integrity seal
            chrome/...         the browser files
          155.0.8095.0/
            ...
    """

    def __init__(
        self,
        home: Path | str | None = None,
        registry: VersionRegistry | None = None,
    ) -> None:
        from ..launcher.isolation import default_home

        self._home = Path(home).expanduser() if home is not None else default_home()
        self._registry = registry or VersionRegistry()

    # -- layout ------------------------------------------------------------

    @property
    def home(self) -> Path:
        return self._home

    @property
    def cores_root(self) -> Path:
        """The directory holding the installed cores."""

        return self._home / CORES_DIRNAME

    def core_dir(self, version: str) -> Path:
        """The directory a core of the given version lives in."""

        return self.cores_root / version

    # -- manifests ---------------------------------------------------------

    def manifest_path(self, core_dir: Path | str) -> Path:
        return Path(core_dir) / MANIFEST_FILENAME

    def read_manifest(self, core_dir: Path | str) -> CoreManifest:
        """Read the manifest of a core directory.

        Raises :class:`CoreInstallError` when the manifest is missing or
        unreadable.
        """

        path = self.manifest_path(core_dir)
        if not path.is_file():
            raise CoreInstallError(f"no core manifest at {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CoreInstallError(f"cannot read the core manifest at {path}: {exc}") from exc
        try:
            return CoreManifest.model_validate(payload)
        except Exception as exc:
            raise CoreInstallError(f"the core manifest at {path} is invalid: {exc}") from exc

    def write_manifest(self, core_dir: Path | str, manifest: CoreManifest) -> Path:
        """Write a core manifest into its directory."""

        target = Path(core_dir)
        target.mkdir(parents=True, exist_ok=True)
        path = self.manifest_path(target)
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    # -- listing -----------------------------------------------------------

    def list_cores(self) -> list[CoreRecord]:
        """Every installed core, ordered by major version."""

        root = self.cores_root
        if not root.is_dir():
            return []

        records: list[CoreRecord] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                manifest = self.read_manifest(entry)
            except CoreInstallError:
                # A directory without a readable manifest is not a core.
                continue
            record = CoreRecord(manifest=manifest, path=entry)
            seal = self.read_seal(entry)
            if seal is not None:
                record.sealed = True
                record.seal_digests = seal
            records.append(record)

        records.sort(key=lambda item: (item.major, item.version))
        return records

    def installed_majors(self) -> list[int]:
        """The major versions that have an installed core."""

        return sorted({record.major for record in self.list_cores()})

    def find_core(self, major: int) -> CoreRecord | None:
        """The installed core for a major version, if any."""

        for record in self.list_cores():
            if record.major == major:
                return record
        return None

    # -- sealing -----------------------------------------------------------

    def seal(self, core_dir: Path | str) -> dict[str, str]:
        """Seal a core directory and write the seal file."""

        base = Path(core_dir)
        digests = seal_directory(base)
        seal_path = base / SEAL_FILENAME
        seal_path.write_text(
            json.dumps(
                {
                    "sealed_at": datetime.now(UTC).isoformat(),
                    "file_count": len(digests),
                    "files": digests,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return digests

    def read_seal(self, core_dir: Path | str) -> dict[str, str] | None:
        """Read the seal of a core directory, if it has one."""

        path = Path(core_dir) / SEAL_FILENAME
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        files = payload.get("files")
        if not isinstance(files, dict):
            return None
        return {str(k): str(v) for k, v in files.items()}

    def verify(self, core_dir: Path | str) -> tuple[bool, list[str], list[str]]:
        """Verify a core directory against its seal.

        Raises :class:`CoreInstallError` when the core has never been sealed.
        """

        base = Path(core_dir)
        seal = self.read_seal(base)
        if seal is None:
            raise CoreInstallError(f"the core at {base} has never been sealed")
        ok, missing, modified = verify_directory(base, seal)
        return ok, missing, modified

    # -- installation ------------------------------------------------------

    def register_core(
        self,
        *,
        version: str,
        major: int,
        source_dir: Path | str,
        core_id: str | None = None,
        channel: ReleaseChannel = ReleaseChannel.CUSTOM,
        executable: str = "",
        platform: str = "windows",
        build_revision: str = "",
        seal: bool = True,
    ) -> CoreRecord:
        """Install a core by copying ``source_dir`` into the cores tree.

        The copy is what gives every core its own directory: two cores of
        different versions never share a file, so one can be replaced or
        removed without disturbing the other.
        """

        source = Path(source_dir).expanduser()
        if not source.is_dir():
            raise CoreInstallError(f"core source directory does not exist: {source}")

        target = self.core_dir(version)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)

        manifest = CoreManifest(
            core_id=core_id or version,
            version=version,
            major=major,
            channel=channel,
            build_revision=build_revision,
            platform=platform,
            executable=executable,
            install_path=str(target),
        )
        self.write_manifest(target, manifest)

        record = CoreRecord(manifest=manifest, path=target)
        if seal:
            record.seal_digests = self.seal(target)
            record.sealed = True
        return record

    def remove_core(self, version: str) -> bool:
        """Remove an installed core.  Returns ``True`` when it existed."""

        target = self.core_dir(version)
        if not target.is_dir():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return not target.exists()


class CoreSelector:
    """Resolves a requested major version onto an installed core.

    The fallback policy is explicit and configurable, because the two
    legitimate answers to "the requested core is missing" are different:

    * ``NEAREST`` -- run the closest installed core and record the
      difference.  The profile's declared version is then adjusted so that
      the UA, the client hints and the running core still agree.
    * ``ERROR`` -- refuse to launch.  The caller gets a clear error naming
      the missing version and the installed ones.
    """

    def __init__(
        self,
        manager: CoreManager,
        registry: VersionRegistry | None = None,
        policy: CoreFallbackPolicy = CoreFallbackPolicy.NEAREST,
    ) -> None:
        self._manager = manager
        self._registry = registry or VersionRegistry()
        self._policy = policy

    @property
    def policy(self) -> CoreFallbackPolicy:
        return self._policy

    def select(
        self,
        requested_major: int,
        *,
        channel: ReleaseChannel | None = None,
        policy: CoreFallbackPolicy | None = None,
    ) -> CoreSelection:
        """Select the core that will serve ``requested_major``.

        Raises :class:`CoreInstallError` when no core can be selected.
        """

        effective_policy = policy or self._policy
        records = self._manager.list_cores()
        if channel is not None:
            records = [r for r in records if r.manifest.channel == channel]
        if not records:
            raise CoreInstallError(
                "no browser core is installed; register one before launching a profile"
            )

        for record in records:
            if record.major == requested_major:
                return CoreSelection(
                    requested_major=requested_major,
                    core=record.manifest,
                    exact=True,
                    fallback_used=False,
                )

        installed = sorted({r.major for r in records})
        if effective_policy is CoreFallbackPolicy.ERROR:
            raise CoreInstallError(
                f"no core is installed for Chromium {requested_major}; "
                f"installed majors: {installed}"
            )

        if effective_policy is CoreFallbackPolicy.OLDEST:
            chosen = min(installed)
            reason = "selected the oldest installed core"
        elif effective_policy is CoreFallbackPolicy.NEWEST:
            chosen = max(installed)
            reason = "selected the newest installed core"
        else:
            chosen = min(installed, key=lambda major: (abs(major - requested_major), major))
            reason = (
                f"no core for Chromium {requested_major}; selected the nearest "
                f"installed core ({chosen})"
            )

        for record in records:
            if record.major == chosen:
                return CoreSelection(
                    requested_major=requested_major,
                    core=record.manifest,
                    exact=False,
                    fallback_used=True,
                    reason=reason,
                )

        raise CoreInstallError(  # pragma: no cover - unreachable
            f"no core is installed for Chromium {chosen}"
        )

    def available_majors(self) -> list[int]:
        """The major versions that can currently be served."""

        return self._manager.installed_majors()


__all__ = [
    "CORES_DIRNAME",
    "MANIFEST_FILENAME",
    "SEAL_FILENAME",
    "VOLATILE_DIRS",
    "VOLATILE_NAMES",
    "VOLATILE_SUFFIXES",
    "CoreManager",
    "CoreRecord",
    "CoreSelector",
    "CoreSelection",
    "seal_directory",
    "verify_directory",
]
