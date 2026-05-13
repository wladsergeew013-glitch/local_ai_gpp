@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0.."
set "ROOT=%CD%"
set "LOG_DIR=%ROOT%\tools\out"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "LOG_FILE=%LOG_DIR%\check_runtime_gpu.log"
set "RUNTIME_PY="
set "RUNTIME_ROOT=%ROOT%"

if exist "%ROOT%\dist\worker_runtime\Scripts\python.exe" (
  set "RUNTIME_PY=%ROOT%\dist\worker_runtime\Scripts\python.exe"
  set "RUNTIME_ROOT=%ROOT%\dist"
) else if exist "%ROOT%\backend\.venv\Scripts\python.exe" (
  set "RUNTIME_PY=%ROOT%\backend\.venv\Scripts\python.exe"
) else (
  echo [ERROR] No worker/backend runtime python found.
  echo Expected dist\worker_runtime\Scripts\python.exe or backend\.venv\Scripts\python.exe.
  exit /b 1
)

>"%LOG_FILE%" echo Local AI GPP runtime GPU check
>>"%LOG_FILE%" echo Root: %ROOT%
>>"%LOG_FILE%" echo Runtime root: %RUNTIME_ROOT%
>>"%LOG_FILE%" echo Runtime python: %RUNTIME_PY%
>>"%LOG_FILE%" echo Started: %DATE% %TIME%
>>"%LOG_FILE%" echo.

echo ============================================================
echo Local AI GPP - GPU runtime diagnostics
echo ============================================================
echo Runtime python: %RUNTIME_PY%
echo Log: %LOG_FILE%
echo.

pushd "%RUNTIME_ROOT%" >nul
set "PYTHONPATH=%RUNTIME_ROOT%"
"%RUNTIME_PY%" "%ROOT%\tools\runtime_probe.py" >>"%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"
popd >nul

type "%LOG_FILE%"
echo.
if not "%RC%"=="0" (
  echo [ERROR] Runtime probe failed. See log above.
  exit /b %RC%
)
echo [OK] Runtime probe completed.
exit /b 0
