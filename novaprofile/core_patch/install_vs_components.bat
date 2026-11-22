@echo off
REM ---------------------------------------------------------------------------
REM Add the ATL and the MFC to the Visual Studio Build Tools install.
REM
REM The Chromium build needs the ATL headers: the DirectX shader compiler
REM that Dawn builds includes <atlbase.h>.  A build that stops with
REM "atlbase.h file not found" is what a missing ATL looks like.
REM
REM The installer needs elevation, so it is started with runas.  Windows
REM asks the operator to confirm the prompt.
REM ---------------------------------------------------------------------------

setlocal

set "VSPATH=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"
set "VSINSTALLER=C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe"
set "LOG=C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\.vs_atl_install.log"

echo === started %DATE% %TIME% ===
echo === install path: %VSPATH% ===

"%VSINSTALLER%" modify ^
  --installPath "%VSPATH%" ^
  --add Microsoft.VisualStudio.Component.VC.ATL ^
  --add Microsoft.VisualStudio.Component.VC.ATLMFC ^
  --quiet ^
  --norestart ^
  --nocache ^
  --wait

echo === finished with %ERRORLEVEL% at %DATE% %TIME% ===
exit /b %ERRORLEVEL%
