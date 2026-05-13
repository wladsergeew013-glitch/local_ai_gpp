@echo off
setlocal EnableExtensions
chcp 65001 >nul

REM ============================================================
REM Local AI GPP - restore safe CPU llama.cpp runtime
REM Usage:
REM   tools\07_install_cpu_runtime.bat
REM ============================================================

cd /d "%~dp0.."
set "ROOT=%CD%"
set "LOG_DIR=%ROOT%\tools\out"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "LOG_FILE=%LOG_DIR%\install_cpu_runtime.log"
set "VENV_PY=%ROOT%\backend\.venv\Scripts\python.exe"

>"%LOG_FILE%" echo Local AI GPP CPU runtime installer
>>"%LOG_FILE%" echo Root: %ROOT%
>>"%LOG_FILE%" echo Started: %DATE% %TIME%
>>"%LOG_FILE%" echo.

echo ============================================================
echo Local AI GPP - install CPU runtime
echo ============================================================
echo Root: %ROOT%
echo Log:  %LOG_FILE%
echo.

if not exist "%VENV_PY%" (
  echo [ERROR] Backend venv not found: %VENV_PY%
  echo Run tools\01_run_local_browser.bat --check first.
  >>"%LOG_FILE%" echo [ERROR] backend venv not found.
  goto fail
)

echo [INFO] Installing CPU llama-cpp-python...
>>"%LOG_FILE%" echo [INFO] Installing CPU llama-cpp-python.
"%VENV_PY%" -m pip uninstall -y llama-cpp-python >>"%LOG_FILE%" 2>&1
"%VENV_PY%" -m pip install --force-reinstall --no-cache-dir "llama-cpp-python==0.3.19" --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log

echo [INFO] Runtime verification:
"%VENV_PY%" -c "import llama_cpp; print('version=', getattr(llama_cpp, '__version__', '')); from llama_cpp import llama_cpp as l; print('supports_gpu_offload=', getattr(l, 'llama_supports_gpu_offload', lambda: None)()); info=getattr(l, 'llama_print_system_info', lambda: b'')(); print(info.decode('utf-8', 'replace') if isinstance(info, bytes) else info)" > "%LOG_DIR%\install_cpu_runtime_verify.txt" 2>&1
type "%LOG_DIR%\install_cpu_runtime_verify.txt"
type "%LOG_DIR%\install_cpu_runtime_verify.txt" >> "%LOG_FILE%"

echo.
echo [OK] CPU runtime installed.
echo Restart LocalAIGPP.exe or backend.
pause
exit /b 0

:fail_with_log
echo ------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath '%LOG_FILE%') { Get-Content -LiteralPath '%LOG_FILE%' -Tail 80 }"
echo ------------------------------------------------------------

:fail
echo.
echo ============================================================
echo FAILED
echo ============================================================
echo Log: %LOG_FILE%
pause
exit /b 1
