"""Concurrency limiting and process supervision for the NOVA launcher.

Running a dozen Chromium instances on a laptop exhausts both RAM and the CPU
scheduler, and a crashed browser leaves orphaned renderer/GPU processes behind
that keep the profile's ``user_data_dir`` locked.  This module solves both
problems:

* :class:`ConcurrencyLimiter` derives a safe number of simultaneously running
  profiles from the host's RAM and CPU count;
* :class:`ProcessSupervisor` tracks the live profiles, notices crashed
  browsers, reaps the orphaned children and removes the stale singleton lock
  files so the profile can be started again.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .isolation import ProfileIsolation

#: Rough memory footprint of one Chromium profile in megabytes.
MEMORY_PER_PROFILE_MB: int = 800

#: Lower / upper bound of the CPU-derived concurrency estimate.
MIN_CONCURRENT: int = 2
MAX_CONCURRENT: int = 16

#: Fraction of the CPU count that may be used by the browsers.
CPU_SHARE: float = 0.5


def _read_host_metrics() -> tuple[int, int, int]:
    """Return ``(cpu_count, total_ram_bytes, available_ram_bytes)``.

    Falls back to conservative defaults when ``psutil`` cannot read the
    platform metrics.
    """

    try:
        import psutil

        memory = psutil.virtual_memory()
        return int(psutil.cpu_count() or 1), int(memory.total), int(memory.available)
    except Exception:  # pragma: no cover - platform dependent
        import os

        return (os.cpu_count() or 2), 4 * 1024**3, 2 * 1024**3


def compute_max_concurrent(
    *,
    cpu_count: int | None = None,
    total_ram_bytes: int | None = None,
    available_ram_bytes: int | None = None,
    memory_per_profile_mb: int = MEMORY_PER_PROFILE_MB,
) -> int:
    """Compute how many profiles may run simultaneously on this host.

    The result is the minimum of the CPU-derived and the RAM-derived bound,
    clamped to ``[MIN_CONCURRENT, MAX_CONCURRENT]``.
    """

    host_cpu, host_total, host_available = _read_host_metrics()
    effective_cpu = host_cpu if cpu_count is None else int(cpu_count)
    effective_total = host_total if total_ram_bytes is None else int(total_ram_bytes)
    effective_available = (
        host_available if available_ram_bytes is None else int(available_ram_bytes)
    )

    cpu_bound = max(1, int(effective_cpu * CPU_SHARE))
    per_profile_bytes = max(1, memory_per_profile_mb) * 1024 * 1024
    ram_bound = max(1, effective_available // per_profile_bytes)
    total_ram_bound = max(1, effective_total // per_profile_bytes)

    limit = min(cpu_bound, ram_bound, total_ram_bound)
    return max(MIN_CONCURRENT, min(MAX_CONCURRENT, limit))


class ConcurrencyLimiter:
    """Limit the number of simultaneously running profiles."""

    def __init__(self, max_concurrent: int | None = None) -> None:
        self._max_concurrent = (
            compute_max_concurrent() if max_concurrent is None else max(1, int(max_concurrent))
        )
        self._active = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    # -- properties --------------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        """The maximum number of simultaneously running profiles."""

        return self._max_concurrent

    @property
    def active(self) -> int:
        """How many slots are currently held."""

        with self._lock:
            return self._active

    @property
    def available(self) -> int:
        """How many slots are currently free."""

        with self._lock:
            return max(0, self._max_concurrent - self._active)

    # -- acquisition -------------------------------------------------------

    def try_acquire(self, timeout: float = 0.0) -> bool:
        """Try to acquire a slot without blocking.

        When ``timeout`` is greater than zero the call waits up to that many
        seconds for a slot to become free.
        """

        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._active >= self._max_concurrent:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.25))
            self._active += 1
            return True

    def acquire(self, timeout: float | None = None) -> bool:
        """Acquire a slot, blocking until one is free.

        ``timeout`` of ``None`` waits forever; a timeout of ``0`` behaves like
        :meth:`try_acquire`.
        """

        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._active >= self._max_concurrent:
                if deadline is None:
                    self._condition.wait(0.25)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, 0.25))
            self._active += 1
            return True

    def release(self) -> None:
        """Release a previously acquired slot."""

        with self._condition:
            if self._active > 0:
                self._active -= 1
            self._condition.notify_all()

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> ConcurrencyLimiter:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    @contextlib.contextmanager
    def slot(self, timeout: float | None = None):
        """Context manager that holds a slot for the duration of the block."""

        acquired = self.acquire(timeout)
        if not acquired:
            raise TimeoutError(f"could not acquire a launcher slot within {timeout} seconds")
        try:
            yield self
        finally:
            self.release()


@dataclass
class SupervisedProfile:
    """The supervisor's view of one running profile."""

    profile_id: str
    pid: int
    user_data_dir: Path
    started_at: float
    session_id: str = ""
    alive: bool = True
    crashed: bool = False
    reaped_pids: list[int] = field(default_factory=list)


class ProcessSupervisor:
    """Track the live profiles and reap the dead ones."""

    def __init__(
        self,
        isolation: ProfileIsolation | None = None,
        *,
        limiter: ConcurrencyLimiter | None = None,
    ) -> None:
        self._isolation = isolation or ProfileIsolation()
        self._limiter = limiter
        self._profiles: dict[str, SupervisedProfile] = {}
        self._lock = threading.RLock()

    # -- properties --------------------------------------------------------

    @property
    def limiter(self) -> ConcurrencyLimiter | None:
        """The concurrency limiter this supervisor coordinates with."""

        return self._limiter

    @property
    def profiles(self) -> dict[str, SupervisedProfile]:
        """The profiles currently tracked."""

        with self._lock:
            return dict(self._profiles)

    def live_profiles(self) -> list[str]:
        """The ids of the profiles whose browser is still alive."""

        with self._lock:
            return [pid for pid, p in self._profiles.items() if p.alive]

    # -- tracking ----------------------------------------------------------

    def register(
        self,
        profile_id: str,
        pid: int,
        user_data_dir: Path | str,
        *,
        session_id: str = "",
    ) -> SupervisedProfile:
        """Register a freshly launched browser."""

        entry = SupervisedProfile(
            profile_id=profile_id,
            pid=pid,
            user_data_dir=Path(user_data_dir),
            started_at=time.time(),
            session_id=session_id,
        )
        with self._lock:
            self._profiles[profile_id] = entry
        return entry

    def unregister(self, profile_id: str) -> None:
        """Stop tracking a profile."""

        with self._lock:
            self._profiles.pop(profile_id, None)

    # -- inspection --------------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            import psutil

            return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
        except Exception:  # pragma: no cover - platform dependent
            import os

            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True

    def check_crashes(self) -> list[SupervisedProfile]:
        """Detect the profiles whose browser died.

        The crashed profiles are marked and their stale lock files are
        removed so the profile can be launched again.
        """

        crashed: list[SupervisedProfile] = []
        with self._lock:
            entries = list(self._profiles.values())
        for entry in entries:
            if not entry.alive:
                continue
            if self._pid_alive(entry.pid):
                continue
            entry.alive = False
            entry.crashed = True
            with contextlib.suppress(Exception):
                ProfileIsolation.cleanup_lock_files(entry.user_data_dir)
            crashed.append(entry)
        return crashed

    def reap_orphans(self, profile_id: str) -> list[int]:
        """Kill the orphaned renderer/GPU children of a profile."""

        with self._lock:
            entry = self._profiles.get(profile_id)
        if entry is None:
            return []
        killed: list[int] = []
        try:
            import psutil

            proc = psutil.Process(entry.pid)
            for child in proc.children(recursive=True):
                with contextlib.suppress(Exception):
                    child.kill()
                    killed.append(child.pid)
        except Exception:
            # The parent is already gone -- the children are orphans and must
            # be found another way: through the user_data_dir they hold open.
            killed = self._reap_by_user_data_dir(entry.user_data_dir)
        entry.reaped_pids.extend(killed)
        return killed

    @staticmethod
    def _reap_by_user_data_dir(user_data_dir: Path) -> list[int]:
        """Find and kill the processes that still hold the profile directory."""

        killed: list[int] = []
        try:
            import psutil
        except Exception:  # pragma: no cover - psutil optional
            return killed
        target = str(user_data_dir.resolve())
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                joined = " ".join(cmdline)
                if target in joined and "--type=" not in joined:
                    proc.kill()
                    killed.append(int(proc.info["pid"]))
            except Exception:
                continue
        return killed

    def sweep(self) -> list[SupervisedProfile]:
        """Run a full sweep: detect crashes, reap orphans, clean the locks."""

        crashed = self.check_crashes()
        for entry in crashed:
            self.reap_orphans(entry.profile_id)
        return crashed

    def shutdown_all(self, terminate: Callable[[str], bool] | None = None) -> None:
        """Stop every tracked profile."""

        with self._lock:
            profile_ids = list(self._profiles.keys())
        for profile_id in profile_ids:
            if terminate is not None:
                with contextlib.suppress(Exception):
                    terminate(profile_id)
            self.reap_orphans(profile_id)
            self.unregister(profile_id)


__all__ = [
    "CPU_SHARE",
    "MAX_CONCURRENT",
    "MEMORY_PER_PROFILE_MB",
    "MIN_CONCURRENT",
    "ConcurrencyLimiter",
    "ProcessSupervisor",
    "SupervisedProfile",
    "compute_max_concurrent",
]
