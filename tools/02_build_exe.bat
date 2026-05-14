@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

REM ============================================================
REM Local AI GPP - canonical portable desktop EXE builder v67.1
REM Location: project_root\tools\02_build_exe.bat
REM Produces:
REM   dist\LocalAIGPP.exe
REM   dist\worker_runtime\python.exe
REM   dist\backend\app\*.py
REM   dist\models_storage\*.json
REM Usage:
REM   tools\02_build_exe.bat
REM   tools\02_build_exe.bat --cpu
REM   tools\02_build_exe.bat --cuda auto
REM   tools\02_build_exe.bat --cuda cu124
REM ============================================================

cd /d "%~dp0.."
set "ROOT=%CD%"
set "TOOLS_DIR=%ROOT%\tools"
set "LOG_DIR=%TOOLS_DIR%\out"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "LOG_FILE=%LOG_DIR%\build_exe.log"
set "TMP_DIR=%LOG_DIR%\tmp"
set "PIP_CACHE_DIR=%LOG_DIR%\pip-cache"
if not exist "%TMP_DIR%" mkdir "%TMP_DIR%" >nul 2>nul
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%" >nul 2>nul
set "TMP=%TMP_DIR%"
set "TEMP=%TMP_DIR%"
set "NO_PROXY=localhost,127.0.0.1,::1,[::1],*.localhost"
set "no_proxy=localhost,127.0.0.1,::1,[::1],*.localhost"
set "LOCAL_AI_GPP_PROXY_BYPASS=1"

>"%LOG_FILE%" echo Local AI GPP desktop exe canonical builder v67.1
>>"%LOG_FILE%" echo Root: %ROOT%
>>"%LOG_FILE%" echo Started: %DATE% %TIME%
>>"%LOG_FILE%" echo Args: %*
>>"%LOG_FILE%" echo.

echo ============================================================
echo Local AI GPP - build desktop EXE v67.1
echo ============================================================
echo Root: %ROOT%
echo Log:  %LOG_FILE%
echo.

if not exist "%ROOT%\tools\02_build_exe.py" (
  echo [ERROR] Missing: %ROOT%\tools\02_build_exe.py
  >>"%LOG_FILE%" echo [ERROR] Missing tools\02_build_exe.py
  goto fail
)
if not exist "%ROOT%\tools\exe_launcher.py" (
  echo [ERROR] Missing: %ROOT%\tools\exe_launcher.py
  >>"%LOG_FILE%" echo [ERROR] Missing tools\exe_launcher.py
  goto fail
)
if not exist "%ROOT%\frontend\package.json" (
  echo [ERROR] Missing: %ROOT%\frontend\package.json
  >>"%LOG_FILE%" echo [ERROR] Missing frontend\package.json
  goto fail
)
if not exist "%ROOT%\backend\app\main.py" (
  echo [ERROR] Missing: %ROOT%\backend\app\main.py
  >>"%LOG_FILE%" echo [ERROR] Missing backend\app\main.py
  goto fail
)

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.12"
)
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.13 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.13"
  )
)
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.11 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.11"
  )
)
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
  )
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(1 if 'WindowsApps' in sys.executable else 0)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
  )
)
if not defined PYTHON_CMD (
  echo [ERROR] Normal Python 3.11/3.12/3.13 was not found.
  echo Install Python from python.org or run: py install 3.12
  >>"%LOG_FILE%" echo [ERROR] Python not found.
  goto fail
)

echo [INFO] Python command: %PYTHON_CMD%
>>"%LOG_FILE%" echo [INFO] Python command: %PYTHON_CMD%

REM v67: the .bat no longer runs a second/legacy PyInstaller pipeline.
REM It delegates to tools\02_build_exe.py, which creates the portable
REM dist bundle and the embedded worker_runtime used by the EXE.
set "LOCAL_AI_GPP_BUILD_LOG_REDIRECTED=1"
%PYTHON_CMD% "%ROOT%\tools\02_build_exe.py" %* >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log

if not exist "%ROOT%\dist\LocalAIGPP.exe" (
  echo [ERROR] dist\LocalAIGPP.exe was not created.
  >>"%LOG_FILE%" echo [ERROR] Missing dist\LocalAIGPP.exe
  goto fail_with_log
)
if not exist "%ROOT%\dist\worker_runtime\python.exe" (
  echo [ERROR] dist\worker_runtime\python.exe was not created.
  >>"%LOG_FILE%" echo [ERROR] Missing dist\worker_runtime\python.exe
  goto fail_with_log
)

echo.
echo ============================================================
echo DESKTOP EXE BUILD COMPLETE
echo ============================================================
echo File:   %ROOT%\dist\LocalAIGPP.exe
echo Worker: %ROOT%\dist\worker_runtime\python.exe
echo.
echo Run smoke test:
echo   tools\09_smoke_test_exe.bat
echo.
pause
exit /b 0

:fail_with_log
echo [ERROR] Build failed. Last log lines:
echo ------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath '%LOG_FILE%') { Get-Content -LiteralPath '%LOG_FILE%' -Tail 160 }"
echo ------------------------------------------------------------
goto fail

:fail
echo.
echo ============================================================
echo FAILED
echo ============================================================
echo Log: %LOG_FILE%
echo.
pause
exit /b 1
