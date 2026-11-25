# -*- mode: python ; coding: utf-8 -*-
"""The PyInstaller specification of the NOVA control plane.

The control plane is frozen into one executable that the desktop shell
starts.  The reasons it is frozen rather than shipped as a virtual
environment are the ones that matter to a customer: it starts in a
second instead of a dozen, it does not need a Python on the machine,
and it cannot be broken by a ``pip install`` that happened on the same
machine for an unrelated reason.

What is *not* in the executable
-------------------------------
The Chromium core.  The core is a browser: it is hundreds of
megabytes, it is patched separately, and it is replaced when the core
is rebuilt.  Freezing it into the executable would mean rebuilding the
executable for every core, and the shell would have to know where to
find it.  It travels beside the executable instead.

The hidden imports
------------------
The engine imports its modules through a loader that reports a missing
module rather than raising, so the analysis cannot see every import
that matters.  They are listed here.
"""

from pathlib import Path

#: The package that is frozen.
PACKAGE = "novaprofile"

#: The modules the loader reaches for by name.
#
# The engine's own loader resolves a module by name and reports a
# diagnostic when the module is absent, which means the static analysis
# does not see the import at all.
HIDDEN_IMPORTS = [
    # The device emulation talks to the browser's developer protocol
    # over a websocket.  The websocket library is reached by name --
    # ``websockets.asyncio.client`` -- so the static analysis does not
    # see the import and the frozen binary would be without it.
    "websockets",
    "websockets.asyncio.client",
    "websockets.asyncio.connection",
    "websockets.client",
    "websockets.exceptions",
    "websockets.frames",
    "websockets.legacy.client",
    "websockets.protocol",
    "novaprofile.api.app",
    "novaprofile.api.auth",
    "novaprofile.api.deps",
    "novaprofile.api.routes_batch",
    "novaprofile.api.routes_presets",
    "novaprofile.api.routes_probe",
    "novaprofile.api.routes_profiles",
    "novaprofile.cli.main",
    "novaprofile.core.chipset_gpu",
    "novaprofile.core.collision",
    "novaprofile.core.consistency",
    "novaprofile.core.cores",
    "novaprofile.core.devices",
    "novaprofile.core.device_catalog_extended",
    "novaprofile.core.device_catalog_fleet",
    "novaprofile.core.device_catalog_market",
    "novaprofile.core.device_spec_source",
    "novaprofile.core.fleet",
    "novaprofile.core.fleet_validator",
    "novaprofile.core.font_catalog",
    "novaprofile.core.generated_presets",
    "novaprofile.core.generator",
    "novaprofile.core.geo_catalog",
    "novaprofile.core.geo_coordinates",
    "novaprofile.core.geo_vietnam",
    "novaprofile.core.gpu_params",
    "novaprofile.core.hardware_approximation",
    "novaprofile.core.identity",
    "novaprofile.core.machine",
    "novaprofile.core.native_core",
    "novaprofile.core.probe_surfaces",
    "novaprofile.core.seed",
    "novaprofile.core.soc_cores",
    "novaprofile.core.surfaces",
    "novaprofile.core.surface_compare",
    "novaprofile.core.tls_profiles",
    "novaprofile.core.versions",
    "novaprofile.installer.chromium_bundle",
    "novaprofile.js_runtime",
    "novaprofile.launcher.core_document",
    "novaprofile.launcher.launcher",
    "novaprofile.probe",
    "novaprofile.registry.store",
    "novaprofile.schema.hardware",
    "novaprofile.schema.identity",
    "novaprofile.security.crypto",
]

#: The third-party packages the engine needs at runtime.
#
# ``uvicorn``'s protocol implementations are loaded by name when the
# server starts, and PyInstaller's analysis does not see them.
HIDDEN_IMPORTS += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.server",
    "anyio._backends._asyncio",
    "fastapi.middleware.cors",
    "pydantic",
    "pydantic_core",
]

#: The data files the engine reads.
#
# The device catalogue is a Python module rather than a data file, so
# the only data is the ICU and the time zone database, both of which
# are found at runtime.
datas = []

#: The market dataset the catalogue is built from.
#
# The dataset is the scraped specification sheet of the handsets the
# Vietnamese market buys.  It is a data file rather than a module, so
# it has to travel inside the bundle: without it the frozen control
# plane would answer with the two thousand reference handsets and
# none of the nineteen thousand of the market.
DATA_ROOT = Path("..") / "core_patch" / "data"
for _data_file in ("vn_devices.jsonl", "devices.jsonl"):
    _data_path = DATA_ROOT / _data_file
    if _data_path.exists():
        datas.append((str(_data_path), "core_patch/data"))

del _data_file, _data_path

#: The binaries that have to travel with the executable.
binaries = []

#: The folders that are collected whole.
#
# The catalogue of the generated presets is four megabytes of Python
# source; freezing it keeps the import machinery from having to find
# it on disk.
hiddenimports = list(HIDDEN_IMPORTS)

analysis = Analysis(
    [str(Path(PACKAGE) / "cli" / "main.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The test tooling has no business in a shipped executable.
        "pytest",
        "pytest_asyncio",
        "_pytest",
        "tkinter",
        "matplotlib",
        "notebook",
        "IPython",
        # The console scripts of the development environment.
        "pip",
        "setuptools",
        "wheel",
        # The scientific stack the engine does not use.
        "numpy",
        "scipy",
        "pandas",
        "PIL",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="nova-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path("..") / ".." / "asset" / "nova-appicon.png"),
)
