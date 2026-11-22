@echo off
REM ---------------------------------------------------------------------------
REM Sync the Chromium checkout with the hooks.
REM
REM The hooks install the toolchain that Chromium's own build uses.  It is
REM not the Visual Studio toolchain: Chromium builds against a pinned
REM version of the compiler, and the build fails without it.
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion

set "DE=C:\depot_tools"
set "PATH=%DE%;%DE%\external_bin;%PATH%"
set "DEPOT_TOOLS_UPDATE=0"
set "DEPOT_TOOLS_WIN_TOOLCHAIN=0"

cd /d C:\chromium

echo === syncing ===
call "%DE%\gclient.bat" sync --nohooks --no-history chromium
if errorlevel 1 (
  echo === the sync failed ===
  exit /b %errorlevel%
)

echo === running the hooks ===
REM The toolchain hook is the one that matters here; it downloads the
REM pinned compiler and the Windows SDK into the checkout.
call "%DE%\gclient.bat" runhooks
if errorlevel 1 (
  echo === the hooks failed ===
  exit /b %errorlevel%
)

echo === done %DATE% %TIME%
exit /b 0
