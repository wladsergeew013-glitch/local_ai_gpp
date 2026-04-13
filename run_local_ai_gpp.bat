@echo off
setlocal enabledelayedexpansion

REM Re-open script in persistent cmd window so output is never lost on close.
REM Accept --persist in any argument position to avoid relaunch loops.
set "PERSIST_MODE=0"
for %%A in (%*) do (
    if /i "%%~A"=="--persist" set "PERSIST_MODE=1"
)
if "%PERSIST_MODE%"=="0" (
    start "Local AI GPP" cmd /k "call \"%~f0\" --persist %*"
    exit /b
)

REM Always run from the repository root where this script is located.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "IMAGE_NAME=local-ai-gpp"
set "CONTAINER_NAME=local-ai-gpp"
set "HOST_PORT=8000"
set "CONTAINER_PORT=8000"
set "MODELS_DIR=%SCRIPT_DIR%models_storage"
set "RUN_EXIT_CODE=0"

if not exist "%MODELS_DIR%" (
    mkdir "%MODELS_DIR%"
)

echo ============================================
echo Local AI GPP launcher
echo Repo: %SCRIPT_DIR%
echo Logs: this window + docker logs %CONTAINER_NAME%
echo ============================================
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo Docker CLI not found in PATH.
    echo Install Docker Desktop and ensure "docker" works in CMD.
    set "RUN_EXIT_CODE=1"
    goto :finalize
)

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker daemon is not running or not reachable.
    echo Start Docker Desktop, wait until it is fully started, then rerun this script.
    set "RUN_EXIT_CODE=1"
    goto :finalize
)

echo [1/4] Building frontend (TypeScript)...
where npm >nul 2>&1
if errorlevel 1 (
    echo npm not found. Skipping frontend build.
    echo If you changed frontend code, install Node.js and run: cd frontend ^&^& npm install ^&^& npm run build
) else (
    pushd frontend
    call npm install
    if errorlevel 1 (
        echo Frontend dependency install failed.
        popd
        set "RUN_EXIT_CODE=1"
        goto :finalize
    )

    call npm run build
    if errorlevel 1 (
        echo Frontend build failed.
        popd
        set "RUN_EXIT_CODE=1"
        goto :finalize
    )
    popd
)

echo [2/4] Building Docker image...
docker build -t %IMAGE_NAME% "%SCRIPT_DIR%"
if errorlevel 1 (
    echo Docker build failed.
    set "RUN_EXIT_CODE=1"
    goto :finalize
)

echo [3/4] Removing old container (if exists)...
docker rm -f %CONTAINER_NAME% >nul 2>&1

echo [4/4] Starting container...
echo ------------------------------------------------------------
echo Starting %CONTAINER_NAME% on http://127.0.0.1:%HOST_PORT%
echo If the container exits, exit code and hints will be shown below.
echo ------------------------------------------------------------
docker run --name %CONTAINER_NAME% --rm -p %HOST_PORT%:%CONTAINER_PORT% -v "%MODELS_DIR%:/app/models_storage" %IMAGE_NAME%
set "RUN_EXIT_CODE=%ERRORLEVEL%"

:finalize
echo.
echo Container finished with exit code: %RUN_EXIT_CODE%
if not "%RUN_EXIT_CODE%"=="0" (
    echo Last logs (docker logs %CONTAINER_NAME%):
    docker logs %CONTAINER_NAME%
)
echo.
echo Script finished. This window will stay open for diagnostics.
echo You can rerun logs manually with: docker logs %CONTAINER_NAME%

endlocal & exit /b %RUN_EXIT_CODE%
