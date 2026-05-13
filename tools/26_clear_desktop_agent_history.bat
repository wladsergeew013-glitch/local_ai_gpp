@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "LOCAL_STATE=%LOCALAPPDATA%\LocalAIGPP"
set "EXE_STATE=%ROOT%\assistant_state"
set "DIST_STATE=%ROOT%\dist\assistant_state"

echo ============================================================
echo Local AI GPP - clear desktop assistant state v66
echo ============================================================
echo Root: %ROOT%
echo.

if exist "%LOCAL_STATE%" (
  echo [INFO] Cleaning %LOCAL_STATE%
  del /q "%LOCAL_STATE%\shared_chat_v*.json" >nul 2>nul
  del /q "%LOCAL_STATE%\assistant_position_v*.json" >nul 2>nul
  del /q "%LOCAL_STATE%\assistant_settings_v*.json" >nul 2>nul
)

if exist "%EXE_STATE%" (
  echo [INFO] Cleaning %EXE_STATE%
  del /q "%EXE_STATE%\*.json" >nul 2>nul
)

if exist "%DIST_STATE%" (
  echo [INFO] Cleaning %DIST_STATE%
  del /q "%DIST_STATE%\*.json" >nul 2>nul
)

echo.
echo Done. Restart LocalAIGPP.exe.
pause