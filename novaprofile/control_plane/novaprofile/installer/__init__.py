"""The installer builder.

The installer turns the source tree into a directory a customer can
unpack and run.  It does not write to the system, it does not touch the
registry and it does not change the PATH -- the whole installation lives
in one directory, and deleting that directory is the uninstall.

See :mod:`novaprofile.installer.builder` for the design decisions.
"""

from __future__ import annotations

from .builder import (
    DATA_DIRECTORIES,
    PRODUCT_NAME,
    PRODUCT_VERSION,
    BuildResult,
    InstallerBuilder,
    InstallerError,
)

__all__ = [
    "DATA_DIRECTORIES",
    "PRODUCT_NAME",
    "PRODUCT_VERSION",
    "BuildResult",
    "InstallerBuilder",
    "InstallerError",
]
