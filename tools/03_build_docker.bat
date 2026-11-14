@echo off
setlocal EnableExtensions
chcp 65001 >nul

REM ============================================================
REM Local AI GPP - Docker build and run
REM Location: project_root\tools\03_build_docker.bat
REM Run from double-click or cmd.
REM ============================================================

cd /d "%~dp0.."
set "ROOT=%CD%"
set "LOG_DIR=%ROOT%\tools\out"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
set "LOG_FILE=%LOG_DIR%\docker_build_run.log"

>"%LOG_FILE%" echo Local AI GPP Docker launcher
>>"%LOG_FILE%" echo Root: %ROOT%
>>"%LOG_FILE%" echo Started: %DATE% %TIME%
>>"%LOG_FILE%" echo.

echo ============================================================
echo Local AI GPP - Docker build and run
echo ============================================================
echo Root: %ROOT%
echo Log:  %LOG_FILE%
echo.

if not exist "%ROOT%\docker-compose.yml" (
    echo [ERROR] docker-compose.yml not found in project root.
    >>"%LOG_FILE%" echo [ERROR] docker-compose.yml not found.
    goto fail
)

where docker >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker is not found in PATH.
    echo Install Docker Desktop and start it first.
    >>"%LOG_FILE%" echo [ERROR] Docker is not found.
    goto fail
)

docker info >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker daemon is not running.
    echo Start Docker Desktop and run this file again.
    >>"%LOG_FILE%" echo [ERROR] Docker daemon is not running.
    goto fail
)

set "COMPOSE=docker compose"
docker compose version >nul 2>nul
if errorlevel 1 (
    where docker-compose >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Neither "docker compose" nor "docker-compose" is available.
        >>"%LOG_FILE%" echo [ERROR] Compose command not available.
        goto fail
    )
    set "COMPOSE=docker-compose"
)

echo [INFO] Compose command: %COMPOSE%
>>"%LOG_FILE%" echo [INFO] Compose command: %COMPOSE%

echo [INFO] Validating compose file...
>>"%LOG_FILE%" echo [INFO] Running: %COMPOSE% config
%COMPOSE% config >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log

echo.
echo [INFO] Building and starting containers...
echo [INFO] This can take time on first run.
echo.
>>"%LOG_FILE%" echo [INFO] Running: %COMPOSE% up -d --build
%COMPOSE% up -d --build >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto fail_with_log

echo.
echo [OK] Containers are running.
for /f "delims=" %%P in ('%COMPOSE% port frontend 80') do set "WEB_ADDRESS=%%P"
for /f "delims=" %%P in ('%COMPOSE% port backend 8000') do set "API_ADDRESS=%%P"
if not defined WEB_ADDRESS goto fail
if not defined API_ADDRESS goto fail
echo Frontend: http://%WEB_ADDRESS%
echo Backend:  http://%API_ADDRESS%/api/health
echo.
echo [INFO] Opening browser...
start "" "http://%WEB_ADDRESS%"

echo [INFO] Following logs. Press Ctrl+C to stop viewing logs.
echo [INFO] Containers will keep running in background.
echo.
>>"%LOG_FILE%" echo [OK] Containers started.
%COMPOSE% logs -f --tail=120

goto finish

:fail_with_log
echo [ERROR] Docker command failed. Last log lines:
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

:finish
echo.
echo ============================================================
echo DONE
echo ============================================================
echo To stop containers later, run:
echo   docker compose down
echo.
pause
exit /b 0
