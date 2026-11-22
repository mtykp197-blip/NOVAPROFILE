@echo off
REM ---------------------------------------------------------------------------
REM Build the NOVA native Chromium core.
REM
REM The build is the slow part: a Chromium compile takes hours on eighteen
REM cores, and the first one is slower still because everything is built from
REM scratch.  This script does the whole thing:
REM
REM   1. checks that depot_tools and a checkout are there,
REM   2. applies the patch series (or verifies that it is applied),
REM   3. copies the NOVA sources into the tree,
REM   4. runs gn gen with the arguments from the version matrix,
REM   5. runs autoninja.
REM
REM The output is a Chromium for Android arm64, which is the platform the
REM profiles describe.  A desktop build would be faster to compile but it
REM would not be the browser the product claims to be.
REM
REM Usage:  build_core.bat <major> [out-dir]
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion

set "DE=C:\depot_tools"
set "SRC=C:\chromium\src"
set "CORE_DIR=%~dp0"
set "MAJOR=%~1"
set "OUTDIR=%~2"

if "%MAJOR%"=="" (
  echo usage: build_core.bat ^<major^> [out-dir]
  exit /b 2
)

if "%OUTDIR%"=="" set "OUTDIR=C:\chromium\out\nova-%MAJOR%"

if not exist "%DE%\gn.bat" (
  echo === depot_tools is missing at %DE% ===
  exit /b 3
)

if not exist "%SRC%\.git" (
  echo === there is no Chromium checkout at %SRC% ===
  echo === run fetch_chromium.bat first ===
  exit /b 3
)

REM -- 1. the environment -------------------------------------------------
REM depot_tools does not ship a "set_depot_tools_env" script any more: it
REM puts its own tools on the PATH from within Python.  The bundled
REM interpreter lives in a versioned directory, so the script finds it
REM rather than assuming a name -- the directory is renamed whenever
REM Python is upgraded.
set "PATH=%DE%;%DE%\external_bin;%PATH%"
set "DEPOT_TOOLS_UPDATE=0"
set "DEPOT_TOOLS_WIN_TOOLCHAIN=0"

set "BOOTSTRAP_PY="
for /d %%D in ("%DE%\bootstrap-*") do (
  if exist "%%D\python3\bin\python3.exe" set "BOOTSTRAP_PY=%%D\python3\bin\python3.exe"
)

if "%BOOTSTRAP_PY%"=="" (
  echo === bootstrapping depot_tools ===
  call "%DE%\bootstrap\python3.bat" || (
    echo === the depot_tools bootstrap failed ===
    exit /b 4
  )
  for /d %%D in ("%DE%\bootstrap-*") do (
    if exist "%%D\python3\bin\python3.exe" set "BOOTSTRAP_PY=%%D\python3\bin\python3.exe"
  )
)

if "%BOOTSTRAP_PY%"=="" (
  echo === the depot_tools Python is still missing ===
  exit /b 4
)

echo === depot_tools python: %BOOTSTRAP_PY% ===

cd /d "%SRC%"

REM -- 2. the patch series ------------------------------------------------
set "PATCH_FILES="
for %%P in ("%CORE_DIR%patches\*.patch") do set "PATCH_FILES=!PATCH_FILES! %%~nxP"

echo === applying the patch series ===
git apply --check %PATCH_FILES% 2>nul
if errorlevel 1 (
  echo === the patch series does not apply to this checkout ===
  echo === the tree is probably already patched; checking that instead ===
  git apply --check --reverse %PATCH_FILES% 2>nul
  if errorlevel 1 (
    echo === the tree is neither clean nor patched ===
    git status --short
    exit /b 5
  )
  echo === the tree is already patched ===
) else (
  git apply %PATCH_FILES%
  if errorlevel 1 (
    echo === the patch failed to apply ===
    exit /b 5
  )
  echo === the patch series applied ===
)

REM -- 3. the NOVA sources -------------------------------------------------
REM The sources live outside the Chromium tree, so they are copied in.  The
REM build files that pull them in are part of the patch series.
echo === copying the NOVA sources ===
if not exist "%SRC%\nova" mkdir "%SRC%\nova"
copy /y "%CORE_DIR%src\*.h" "%SRC%\nova\" >nul
copy /y "%CORE_DIR%src\*.cc" "%SRC%\nova\" >nul
copy /y "%CORE_DIR%src\*.h" "%SRC%\third_party\blink\renderer\core\nova\" >nul 2>nul

REM -- 4. gn gen -----------------------------------------------------------
echo === generating the build files ===
echo === target: android arm64, output: %OUTDIR% ===

if not exist "%SRC%\build\config" (
  mkdir "%SRC%\build\config"
)

call "%DE%\gn.bat" gen "%OUTDIR%" ^
  --args="target_os=\"android\" target_cpu=\"arm64\" is_debug=false is_official_build=false treat_warnings_as_errors=false use_remoteexec=false chrome_public_apk=false blink_symbol_level=1"
if errorlevel 1 (
  echo === gn gen failed ===
  exit /b 6
)

REM -- 5. the compile ------------------------------------------------------
echo === compiling; this takes hours ===
echo === started %DATE% %TIME%

call "%DE%\autoninja.bat" -C "%OUTDIR%" chrome
if errorlevel 1 (
  echo === the compile failed ===
  exit /b 7
)

echo === done %DATE% %TIME%
echo === the core is in %OUTDIR% ===
exit /b 0
