@echo off
setlocal enabledelayedexpansion

REM Always run from the repository root where this script is located.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "IMAGE_NAME=local-ai-gpp"
set "CONTAINER_NAME=local-ai-gpp"
set "HOST_PORT=8000"
set "CONTAINER_PORT=8000"
set "MODELS_DIR=%SCRIPT_DIR%models_storage"

if not exist "%MODELS_DIR%" (
    mkdir "%MODELS_DIR%"
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
        exit /b 1
    )

    call npm run build
    if errorlevel 1 (
        echo Frontend build failed.
        exit /b 1
    )
    popd
)

echo [2/4] Building Docker image...
docker build -t %IMAGE_NAME% "%SCRIPT_DIR%"
if errorlevel 1 (
    echo Docker build failed.
    exit /b 1
)

echo [3/4] Removing old container (if exists)...
docker rm -f %CONTAINER_NAME% >nul 2>&1

echo [4/4] Starting container...
docker run --name %CONTAINER_NAME% --rm -p %HOST_PORT%:%CONTAINER_PORT% -v "%MODELS_DIR%:/app/models_storage" %IMAGE_NAME%

endlocal
