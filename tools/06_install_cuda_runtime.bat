@echo off
setlocal EnableExtensions
chcp 65001 >nul

REM ============================================================
REM Local AI GPP - install NVIDIA CUDA llama.cpp runtime
REM Usage:
REM   tools\06_install_cuda_runtime.bat
REM   tools\06_install_cuda_runtime.bat cu125
REM Default CUDA wheel index: cu124
REM ============================================================

cd /d "%~dp0.."
set "ROOT=%CD%"
set "LOG_DIR=%ROOT%\tools\out"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "LOG_FILE=%LOG_DIR%\install_cuda_runtime.log"
set "VENV_PY=%ROOT%\backend\.venv\Scripts\python.exe"
set "CUDA_TAG=cu124"
set "LLAMA_CPP_CUDA_VERSION=0.3.36"
if not "%~1"=="" set "CUDA_TAG=%~1"
if not "%~2"=="" set "LLAMA_CPP_CUDA_VERSION=%~2"

>"%LOG_FILE%" echo Local AI GPP CUDA runtime installer
>>"%LOG_FILE%" echo Root: %ROOT%
>>"%LOG_FILE%" echo CUDA tag: %CUDA_TAG%
>>"%LOG_FILE%" echo Started: %DATE% %TIME%
>>"%LOG_FILE%" echo.

echo ============================================================
echo Local AI GPP - install CUDA runtime
echo ============================================================
echo Root: %ROOT%
echo CUDA wheel index: %CUDA_TAG%
echo llama-cpp-python CUDA version: %LLAMA_CPP_CUDA_VERSION%
echo Log:  %LOG_FILE%
echo.

if not exist "%VENV_PY%" (
  echo [ERROR] Backend venv not found: %VENV_PY%
  echo Create backend/.venv with Python 3.12 and install backend/requirements-base.txt first.
  >>"%LOG_FILE%" echo [ERROR] backend venv not found.
  goto fail
)

"%VENV_PY%" -c "import sys; raise SystemExit(0 if (sys.version_info.major, sys.version_info.minor) in [(3,10),(3,11),(3,12)] else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] CUDA wheels usually require Python 3.10-3.12.
  "%VENV_PY%" --version
  >>"%LOG_FILE%" echo [ERROR] Unsupported Python for CUDA wheel.
  "%VENV_PY%" --version >>"%LOG_FILE%" 2>&1
  goto fail
)

where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo [WARN] nvidia-smi not found. NVIDIA driver may be missing or not in PATH.
  >>"%LOG_FILE%" echo [WARN] nvidia-smi not found.
) else (
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader >>"%LOG_FILE%" 2>&1
)

echo [INFO] Removing current llama-cpp-python...
>>"%LOG_FILE%" echo [INFO] Uninstalling llama-cpp-python.
"%VENV_PY%" -m pip uninstall -y llama-cpp-python >>"%LOG_FILE%" 2>&1

echo [INFO] Installing prebuilt CUDA llama-cpp-python %LLAMA_CPP_CUDA_VERSION% from %CUDA_TAG%...
>>"%LOG_FILE%" echo [INFO] Installing prebuilt CUDA llama-cpp-python %LLAMA_CPP_CUDA_VERSION% from %CUDA_TAG%.
"%VENV_PY%" -m pip install --force-reinstall --no-cache-dir --only-binary=:all: "llama-cpp-python==%LLAMA_CPP_CUDA_VERSION%" --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/%CUDA_TAG%" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERROR] Prebuilt CUDA wheel install failed for %CUDA_TAG% / %LLAMA_CPP_CUDA_VERSION%.
  echo Try another tag, for example:
  echo   tools\06_install_cuda_runtime.bat cu125 0.3.36
  echo If you want to build from source, install Visual Studio Build Tools with C++ and NVIDIA CUDA Toolkit.
  echo Manual source build:
  echo   set CMAKE_ARGS=-DGGML_CUDA=on
  echo   set FORCE_CMAKE=1
  echo   "%VENV_PY%" -m pip install --force-reinstall --no-cache-dir llama-cpp-python==0.3.19
  goto fail_with_log
)

echo [INFO] Runtime verification:
echo [INFO] Installing NVIDIA CUDA 12.4 runtime and cuBLAS DLLs...
"%VENV_PY%" -m pip install "nvidia-cuda-runtime-cu12==12.4.127" "nvidia-cublas-cu12==12.4.5.8" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log
set "PYTHONPATH=%ROOT%"
"%VENV_PY%" -c "import json; from backend.app.core import get_runtime_diagnostics; d=get_runtime_diagnostics(); print(json.dumps(d, ensure_ascii=False, indent=2)); raise SystemExit(0 if d.get('supports_gpu_offload') else 2)" > "%LOG_DIR%\install_cuda_runtime_verify.txt" 2>&1
set "VERIFY_EXIT=%ERRORLEVEL%"
type "%LOG_DIR%\install_cuda_runtime_verify.txt"
type "%LOG_DIR%\install_cuda_runtime_verify.txt" >> "%LOG_FILE%"
if not "%VERIFY_EXIT%"=="0" (
  echo [ERROR] CUDA runtime was installed, but GPU offload is not available.
  echo [ERROR] If diagnostics show missing CUDA DLLs, install the matching NVIDIA CUDA 12 runtime/toolkit.
  echo [ERROR] Typical DLLs: cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll.
  echo [ERROR] The CUDA install was not converted to CPU automatically. Run tools\07_install_cpu_runtime.bat if you want CPU rollback.
  >>"%LOG_FILE%" echo [ERROR] CUDA runtime verification did not report gpu_runtime_ready=true. No automatic CPU rollback was performed.
  goto fail_with_log
)

echo.
echo [OK] CUDA runtime install command completed.
echo Restart LocalAIGPP.exe or backend, then open Runtime diagnostics.
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
