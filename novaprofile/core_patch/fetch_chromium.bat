@echo off
REM Fetch Chromium for the NOVA native core.
REM The arguments are baked in, because a nested cmd /c with redirected
REM arguments loses them on Windows.

setlocal
set "DE=C:\depot_tools"
set "SRC=C:\chromium\src"
set "VERSION=159"
set "BRANCH=8078"

if not exist "%DE%" (
  echo depot_tools is missing at %DE%
  exit /b 3
)

if not exist "C:\chromium" mkdir "C:\chromium"

echo === fetching Chromium %VERSION%.%BRANCH%.0 ===
echo === checkout: %SRC%
echo === started %DATE% %TIME%

cd /d C:\chromium

call "%DE%\fetch.bat" --nohooks --no-history chromium
if errorlevel 1 (
  echo === fetch failed with %errorlevel% ===
  exit /b %errorlevel%
)

echo === checkout taken ===
cd /d "%SRC%"

REM Pin the checkout to the branch the matrix names.  A branch head moves;
REM a release tag does not, and the product must build the same core twice.
git fetch origin %BRANCH% --depth=1
if errorlevel 1 (
  echo === could not fetch branch %BRANCH% ===
  exit /b %errorlevel%
)

git checkout -B nova-%VERSION% refs/remotes/origin/%BRANCH%
if errorlevel 1 (
  echo === could not check out branch %BRANCH% ===
  exit /b %errorlevel%
)

git log -1 --oneline
echo === done %DATE% %TIME%
exit /b 0
