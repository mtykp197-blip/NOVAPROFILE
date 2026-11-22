@echo off
REM ---------------------------------------------------------------------------
REM Generate the Chromium build files for the NOVA native core.
REM
REM The build is for Android arm64: the product describes an Android
REM browser, so the core it patches has to be an Android browser.  A
REM desktop build would compile faster and would prove less.
REM
REM The arguments come from the version matrix in build_config.json.
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion

set "DE=C:\depot_tools"
set "SRC=C:\chromium\src"
set "OUT=C:\chromium\out\nova-159"

set "PATH=%DE%;%DE%\external_bin;%PATH%"
set "DEPOT_TOOLS_UPDATE=0"
set "DEPOT_TOOLS_WIN_TOOLCHAIN=0"
set "CHROME_HEADLESS=0"

cd /d "%SRC%"

if not exist "%OUT%" mkdir "%OUT%"

echo === gn gen %OUT% ===
echo === target: android arm64 ===
echo === started %DATE% %TIME%

call "%DE%\gn.bat" gen "%OUT%" ^
  --args="target_os=\"android\" target_cpu=\"arm64\" is_debug=false is_official_build=false treat_warnings_as_errors=false use_remoteexec=false chrome_public_apk=false blink_symbol_level=1 android_sdk_root=\"%SRC%\third_party\android_sdk\""
set "RC=%ERRORLEVEL%"

echo === gn gen finished with %RC% at %DATE% %TIME% ===
exit /b %RC%
