@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
set "LOG=%ROOT%\tools\out\dist_portable_check.log"
if not exist "%ROOT%\tools\out" mkdir "%ROOT%\tools\out" >nul 2>nul

echo ============================================================
echo Local AI GPP - dist portability check v67.4
echo ============================================================
echo Log: %LOG%
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.12"
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
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERROR] Python not found.
  exit /b 1
)

%PYTHON_CMD% "%ROOT%\tools\28_check_dist_portable.py" >"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
type "%LOG%"
echo.
if not "%RC%"=="0" (
  echo ============================================================
  echo FAILED
  echo ============================================================
  exit /b %RC%
)
echo ============================================================
echo OK
echo ============================================================
exit /b 0
