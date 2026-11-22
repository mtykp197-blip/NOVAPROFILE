@echo off
REM ---------------------------------------------------------------------------
REM Compile the NOVA native Chromium core.
REM
REM The build takes hours on eighteen cores.  It runs in the background and
REM writes its log where the operator can read it.
REM
REM Three details are not obvious, and each of them cost a build:
REM
REM   * ``autoninja`` is not used.  It reads ``/proc/self/fd/1`` to work out
REM     whether the build runs on a terminal, which is a Linux path and does
REM     not exist on Windows, so it crashes before it starts.  Ninja itself
REM     does the same job.
REM
REM   * The output directory is ``src/out/...`` and not ``chromium/out/...``.
REM     GN resolves ``//out/...`` against the source root, which is where
REM     the checkout lives.
REM
REM   * Chromium's own Python has to come first on the PATH.  The build
REM     calls ``python3`` from a batch file, and on a Windows machine that
REM     has the Microsoft Store's Python installed, ``python3`` resolves to
REM     the Store's stub, which prints "Python was not found" and fails the
REM     step that asked for it.  The checkout carries a working Python of
REM     its own, and putting it first is what makes the build use it.
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion

set "DE=C:\depot_tools"
set "SRC=C:\chromium\src"
set "OUT=%SRC%\out\nova-win"

REM The toolchain of depot_tools is fetched from a bucket that needs a
REM login; the local Visual Studio is used instead.  Chromium's own
REM documentation says the same thing.
set "DEPOT_TOOLS_WIN_TOOLCHAIN=0"
set "DEPOT_TOOLS_UPDATE=0"

REM The build tools look for Visual Studio 2022 under Program Files, but
REM the Build Tools installer puts it under Program Files (x86).
set "vs2022_install=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"

REM The checkout's Python comes first, then depot_tools, whose binaries
REM the build fetches the NDK and the Android build tools through.
set "PATH=%SRC%\third_party\cpython3\host\bin;%DE%;%DE%\external_bin;%PATH%"

cd /d "%SRC%"

echo === compiling the NOVA core ===
echo === target: windows x64 ===
echo === output: %OUT% ===
echo === started %DATE% %TIME%

"%SRC%\third_party\ninja\ninja.exe" -C "%OUT%" chrome
set "RC=%ERRORLEVEL%"

echo === finished with %RC% at %DATE% %TIME% ===
exit /b %RC%
