@echo off
setlocal EnableExtensions
chcp 65001 >nul

REM ============================================================
REM Local AI GPP - build desktop EXE with native tray assistant v66
REM Location: project_root\tools\02_build_exe.bat
REM Produces: dist\LocalAIGPP.exe
REM ============================================================

cd /d "%~dp0.."
set "ROOT=%CD%"
set "TOOLS_DIR=%ROOT%\tools"
set "LOG_DIR=%TOOLS_DIR%\out"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "TMP_DIR=%LOG_DIR%\tmp"
set "PIP_CACHE_DIR=%LOG_DIR%\pip-cache"
if not exist "%TMP_DIR%" mkdir "%TMP_DIR%" >nul 2>nul
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%" >nul 2>nul
set "TMP=%TMP_DIR%"
set "TEMP=%TMP_DIR%"
set "LOG_FILE=%LOG_DIR%\build_exe.log"
if not exist "%ROOT%\models_storage\branding\icons" mkdir "%ROOT%\models_storage\branding\icons" >nul 2>nul
if exist "%ROOT%\models_storage\branding\local_ai_gpp.ico" (
    copy /Y "%ROOT%\models_storage\branding\local_ai_gpp.ico" "%ROOT%\models_storage\branding\icons\local_ai_gpp.ico" >nul 2>nul
    del /Q "%ROOT%\models_storage\branding\local_ai_gpp.ico" >nul 2>nul
)
if exist "%ROOT%\models_storage\branding\local_ai_gpp_icon_preview.png" (
    if not exist "%ROOT%\models_storage\branding\icons\local_ai_gpp_icon_preview.png" copy /Y "%ROOT%\models_storage\branding\local_ai_gpp_icon_preview.png" "%ROOT%\models_storage\branding\icons\local_ai_gpp_icon_preview.png" >nul 2>nul
    del /Q "%ROOT%\models_storage\branding\local_ai_gpp_icon_preview.png" >nul 2>nul
)
if not defined LLAMA_CPP_ACCEL set "LLAMA_CPP_ACCEL=cpu"
if not defined LLAMA_CPP_CPU_VERSION set "LLAMA_CPP_CPU_VERSION=0.3.19"
if not defined LLAMA_CPP_CUDA_VERSION set "LLAMA_CPP_CUDA_VERSION=0.3.4"
if /I "%~1"=="--cpu" set "LLAMA_CPP_ACCEL=cpu"
if /I "%~1"=="--cuda" (
    if "%~2"=="" (set "LLAMA_CPP_ACCEL=auto") else (set "LLAMA_CPP_ACCEL=%~2")
)

>"%LOG_FILE%" echo Local AI GPP desktop exe builder v66
>>"%LOG_FILE%" echo Root: %ROOT%
>>"%LOG_FILE%" echo Started: %DATE% %TIME%
>>"%LOG_FILE%" echo llama.cpp acceleration: %LLAMA_CPP_ACCEL%
>>"%LOG_FILE%" echo.

echo ============================================================
echo Local AI GPP - build desktop EXE v66
echo ============================================================
echo Root: %ROOT%
echo Log:  %LOG_FILE%
echo llama.cpp acceleration: %LLAMA_CPP_ACCEL%
echo.

if not exist "%ROOT%\backend\app\main.py" goto missing_project
if not exist "%ROOT%\frontend\package.json" goto missing_project
if not exist "%ROOT%\tools\exe_launcher.py" goto missing_project
findstr /B /C:":install_llama_backend" "%~f0" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Build script is corrupted: label :install_llama_backend not found.
    >>"%LOG_FILE%" echo [ERROR] Build script is corrupted: label :install_llama_backend not found.
    goto fail
)
set "EXE_ICON=%ROOT%\models_storage\branding\icons\local_ai_gpp.ico"
if not exist "%EXE_ICON%" (
    echo [WARN] EXE icon not found: %EXE_ICON%
    >>"%LOG_FILE%" echo [WARN] EXE icon not found. PyInstaller will use default icon.
)
if not exist "%ROOT%\backend\requirements.txt" goto missing_project

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
    echo [ERROR] Python is not found. Install Python 3.12/3.13 and try again.
    >>"%LOG_FILE%" echo [ERROR] Python is not found.
    goto fail
)

echo [INFO] Python command: %PYTHON_CMD%
>>"%LOG_FILE%" echo [INFO] Python command: %PYTHON_CMD%

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js is not found in PATH.
    >>"%LOG_FILE%" echo [ERROR] Node.js is not found.
    goto fail
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm.cmd is not found in PATH.
    >>"%LOG_FILE%" echo [ERROR] npm.cmd is not found.
    goto fail
)

set "VENV_DIR=%ROOT%\backend\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_DIR%\pyvenv.cfg" (
    findstr /I /C:"WindowsApps" "%VENV_DIR%\pyvenv.cfg" >nul 2>nul
    if not errorlevel 1 (
        echo [WARN] Existing backend venv was created from Windows Store Python. Recreating it...
        >>"%LOG_FILE%" echo [WARN] Existing backend venv uses WindowsApps Python. Recreating it.
        rmdir /s /q "%VENV_DIR%" >>"%LOG_FILE%" 2>&1
    )
)

if exist "%VENV_PY%" (
    "%VENV_PY%" --version >nul 2>nul
    if errorlevel 1 rmdir /s /q "%VENV_DIR%" >>"%LOG_FILE%" 2>&1
)

if not exist "%VENV_PY%" (
    echo [INFO] Creating backend virtual environment...
    >>"%LOG_FILE%" echo [INFO] Creating backend virtual environment.
    %PYTHON_CMD% -m venv "%VENV_DIR%" >>"%LOG_FILE%" 2>&1
    if errorlevel 1 goto fail_with_log
)

if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment python not found: %VENV_PY%
    >>"%LOG_FILE%" echo [ERROR] Virtual environment python not found.
    goto fail
)

echo [INFO] Installing Python dependencies...
>>"%LOG_FILE%" echo [INFO] Installing Python dependencies.
"%VENV_PY%" -m pip install --upgrade pip >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log
"%VENV_PY%" -m pip install -r "%ROOT%\backend\requirements.txt" pyinstaller pywebview pystray Pillow >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log

if /I "%LLAMA_CPP_ACCEL%"=="cpu" (
    echo [INFO] Using CPU llama-cpp-python backend.
    >>"%LOG_FILE%" echo [INFO] Using CPU llama-cpp-python backend.
) else (
    call :install_llama_backend || goto fail_with_log
)

echo [INFO] Installing frontend dependencies and building frontend...
>>"%LOG_FILE%" echo [INFO] Building frontend.
pushd "%ROOT%\frontend" >nul
if not exist "node_modules" (
    call npm.cmd install >>"%LOG_FILE%" 2>&1
    if errorlevel 1 (
        popd >nul
        goto fail_with_log
    )
)
set "VITE_API_BASE=."
call npm.cmd run build >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    popd >nul
    goto fail_with_log
)
popd >nul

if not exist "%ROOT%\frontend\dist\index.html" (
    echo [ERROR] frontend\dist\index.html not found after build.
    >>"%LOG_FILE%" echo [ERROR] frontend build output missing.
    goto fail
)

if exist "%ROOT%\dist\LocalAIGPP.exe" (
    tasklist /FI "IMAGENAME eq LocalAIGPP.exe" 2>nul | find /I "LocalAIGPP.exe" >nul
    if not errorlevel 1 (
        echo [ERROR] LocalAIGPP.exe is running. Close it from tray or Task Manager and run build again.
        >>"%LOG_FILE%" echo [ERROR] LocalAIGPP.exe is running.
        goto fail
    )
)

if not exist "%ROOT%\models_storage" mkdir "%ROOT%\models_storage" >nul 2>nul
if not exist "%ROOT%\models_storage\branding" mkdir "%ROOT%\models_storage\branding" >nul 2>nul
if not exist "%ROOT%\models_storage\models.json" echo []>"%ROOT%\models_storage\models.json"

echo [INFO] Building desktop EXE with PyInstaller...
>>"%LOG_FILE%" echo [INFO] Running PyInstaller.
"%VENV_PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "LocalAIGPP" ^
  --icon "%EXE_ICON%" ^
  --paths "%ROOT%" ^
  --add-data "%ROOT%\frontend\dist;frontend_dist" ^
  --collect-all webview ^
  --collect-all pystray ^
  --collect-all PIL ^
  --collect-submodules backend ^
  --collect-submodules uvicorn ^
  --exclude-module llama_cpp ^
  --exclude-module llama_cpp.llama_cpp ^
  --exclude-module llama_cpp.llama ^
  --exclude-module llama_cpp.llava_cpp ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import tkinter ^
  --hidden-import PIL.ImageTk ^
  "%ROOT%\tools\exe_launcher.py" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log

if not exist "%ROOT%\dist\LocalAIGPP.exe" (
    echo [ERROR] dist\LocalAIGPP.exe was not created.
    >>"%LOG_FILE%" echo [ERROR] exe output missing.
    goto fail
)

if not exist "%ROOT%\dist\models_storage" mkdir "%ROOT%\dist\models_storage" >nul 2>nul
if not exist "%ROOT%\dist\models_storage\branding" mkdir "%ROOT%\dist\models_storage\branding" >nul 2>nul
if exist "%ROOT%\models_storage\settings.json" copy "%ROOT%\models_storage\settings.json" "%ROOT%\dist\models_storage\settings.json" >nul 2>nul
if exist "%ROOT%\models_storage\models.json" copy "%ROOT%\models_storage\models.json" "%ROOT%\dist\models_storage\models.json" >nul 2>nul
if exist "%ROOT%\models_storage\branding" xcopy "%ROOT%\models_storage\branding" "%ROOT%\dist\models_storage\branding" /E /I /Y >nul 2>nul
if exist "%EXE_ICON%" copy "%EXE_ICON%" "%ROOT%\dist\LocalAIGPP.ico" >nul 2>nul
if exist "%ROOT%\README.md" copy "%ROOT%\README.md" "%ROOT%\dist\README.md" >nul 2>nul

echo.
echo ============================================================
echo DESKTOP EXE BUILD COMPLETE
echo ============================================================
echo File: %ROOT%\dist\LocalAIGPP.exe
echo Tray assistant: native Tk window, no invisible WebView background.
echo.
>>"%LOG_FILE%" echo [OK] EXE created: %ROOT%\dist\LocalAIGPP.exe
pause
exit /b 0

:install_llama_backend
if /I "%LLAMA_CPP_ACCEL%"=="cpu" (
    echo [INFO] Using CPU llama-cpp-python backend.
    >>"%LOG_FILE%" echo [INFO] Using CPU llama-cpp-python backend.
    exit /b 0
)
set "CUDA_TAG=%LLAMA_CPP_ACCEL%"
if /I "%CUDA_TAG%"=="auto" set "CUDA_TAG="
if not defined CUDA_TAG (
    where nvidia-smi >nul 2>nul
    if errorlevel 1 (
        echo [INFO] NVIDIA GPU was not detected through nvidia-smi. Keeping CPU llama.cpp backend.
        >>"%LOG_FILE%" echo [INFO] nvidia-smi not found. Keeping CPU llama.cpp backend.
        exit /b 0
    )
    set "CUDA_TAG=cu124"
)
echo [INFO] Trying CUDA llama-cpp-python backend: %CUDA_TAG%
>>"%LOG_FILE%" echo [INFO] Trying CUDA llama-cpp-python backend: %CUDA_TAG%
"%VENV_PY%" -m pip uninstall -y llama-cpp-python >>"%LOG_FILE%" 2>&1
set "PREV_CMAKE_ARGS=%CMAKE_ARGS%"
set "PREV_FORCE_CMAKE=%FORCE_CMAKE%"
set "CMAKE_ARGS=-DGGML_CUDA=on"
set "FORCE_CMAKE=1"
"%VENV_PY%" -m pip install --force-reinstall --no-cache-dir --only-binary=:all: "llama-cpp-python==%LLAMA_CPP_CUDA_VERSION%" --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/%CUDA_TAG%" >>"%LOG_FILE%" 2>&1
set "CMAKE_ARGS=%PREV_CMAKE_ARGS%"
set "FORCE_CMAKE=%PREV_FORCE_CMAKE%"
if errorlevel 1 (
    echo [WARN] CUDA wheel install failed. Falling back to CPU backend.
    >>"%LOG_FILE%" echo [WARN] CUDA wheel install failed. Falling back to CPU backend.
    "%VENV_PY%" -m pip install --force-reinstall --no-cache-dir "llama-cpp-python==%LLAMA_CPP_CPU_VERSION%" --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu" >>"%LOG_FILE%" 2>&1
    exit /b 0
)
exit /b 0

:missing_project
echo [ERROR] Run this bat from project root tools folder. Required files are missing.
>>"%LOG_FILE%" echo [ERROR] Required project files are missing.
goto fail

:fail_with_log
echo [ERROR] Build failed. Last log lines:
echo ------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath '%LOG_FILE%') { Get-Content -LiteralPath '%LOG_FILE%' -Tail 120 }"
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