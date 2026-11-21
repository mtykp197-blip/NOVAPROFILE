"""Browser process management for the NOVA profile engine.

Modules
-------
``isolation``
    Per-profile ``user_data_dir`` management, lock-file cleanup and storage
    wiping.
``launcher``
    The Chromium command line builder and the process lifecycle.
``supervisor``
    The concurrency limiter and the process/crash supervisor.
"""

from __future__ import annotations

from .isolation import (
    HOME_ENV_VAR,
    LOCK_FILENAMES,
    LOGS_DIRNAME,
    PROFILES_DIRNAME,
    STALE_SUFFIXES,
    STORAGE_ENTRIES,
    ProfileIsolation,
    default_home,
)
from .launcher import (
    BASE_DISABLE_FEATURES,
    CORE_CONFIG_FILENAME,
    DEFAULT_CHROME_PATHS,
    BrowserLauncher,
    LaunchedBrowser,
    chrome_version,
    default_launcher,
)
from .supervisor import (
    MAX_CONCURRENT,
    MEMORY_PER_PROFILE_MB,
    MIN_CONCURRENT,
    ConcurrencyLimiter,
    ProcessSupervisor,
    SupervisedProfile,
    compute_max_concurrent,
)

__all__ = [
    "BASE_DISABLE_FEATURES",
    "CORE_CONFIG_FILENAME",
    "ConcurrencyLimiter",
    "DEFAULT_CHROME_PATHS",
    "HOME_ENV_VAR",
    "LOCK_FILENAMES",
    "LOGS_DIRNAME",
    "MAX_CONCURRENT",
    "MEMORY_PER_PROFILE_MB",
    "MIN_CONCURRENT",
    "PROFILES_DIRNAME",
    "ProcessSupervisor",
    "ProfileIsolation",
    "STALE_SUFFIXES",
    "STORAGE_ENTRIES",
    "SupervisedProfile",
    "BrowserLauncher",
    "LaunchedBrowser",
    "chrome_version",
    "compute_max_concurrent",
    "default_home",
    "default_launcher",
]
