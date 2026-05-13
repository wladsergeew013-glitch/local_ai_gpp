@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

REM ================================================================
REM MINI_ASSISTANT_WINDOW_V38: browser mode still uses /assistant fallback; EXE uses /api/desktop/show-assistant.
REM Local AI GPP - fast local browser launch
REM Location: project_root\tools\01_run_local_browser.bat
REM Full reinstall: tools\01_run_local_browser.bat --setup
REM Free local ports: tools\01_run_local_browser.bat --reset-ports
REM ================================================================

set "TOOLS_DIR=%~dp0"
for %%I in ("%TOOLS_DIR%..") do set "ROOT=%%~fI"
set "OUT_DIR=%TOOLS_DIR%out"
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%" >nul 2>nul
set "TMP_DIR=%OUT_DIR%\tmp"
set "PIP_CACHE_DIR=%OUT_DIR%\pip-cache"
if not exist "%TMP_DIR%" mkdir "%TMP_DIR%" >nul 2>nul
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%" >nul 2>nul
set "TMP=%TMP_DIR%"
set "TEMP=%TMP_DIR%"
set "NO_PROXY=localhost,127.0.0.1,::1,[::1],*.localhost"
set "no_proxy=localhost,127.0.0.1,::1,[::1],*.localhost"
set "LOCAL_AI_GPP_PROXY_BYPASS=1"
set "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--no-proxy-server --proxy-bypass-list=<-loopback>;localhost;127.0.0.1;::1;[::1]"

set "LOG=%OUT_DIR%\run_local_browser.log"
set "BACKEND_LAUNCHER=%OUT_DIR%\_start_backend.bat"
set "FRONTEND_LAUNCHER=%OUT_DIR%\_start_frontend.bat"
set "BACKEND_HOST=127.0.0.1"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "FORCE_SETUP=0"
set "RESET_PORTS=0"
set "CHECK_ONLY=0"
set "ASSISTANT_MODE=0"
if /I "%~1"=="--setup" set "FORCE_SETUP=1"
if /I "%~2"=="--setup" set "FORCE_SETUP=1"
if /I "%~1"=="--reset-ports" set "RESET_PORTS=1"
if /I "%~2"=="--reset-ports" set "RESET_PORTS=1"
if /I "%~1"=="--check" set "CHECK_ONLY=1"
if /I "%~2"=="--check" set "CHECK_ONLY=1"
if /I "%~1"=="--assistant" set "ASSISTANT_MODE=1"
if /I "%~2"=="--assistant" set "ASSISTANT_MODE=1"
if /I "%~3"=="--assistant" set "ASSISTANT_MODE=1"
if /I "%~4"=="--assistant" set "ASSISTANT_MODE=1"

>"%LOG%" echo ============================================================
>>"%LOG%" echo LOCAL AI GPP - LOCAL BROWSER RUN
>>"%LOG%" echo ============================================================
>>"%LOG%" echo Started: %DATE% %TIME%
>>"%LOG%" echo Tools: %TOOLS_DIR%
>>"%LOG%" echo Root: %ROOT%
>>"%LOG%" echo Force setup: %FORCE_SETUP%
>>"%LOG%" echo Reset ports: %RESET_PORTS%
>>"%LOG%" echo Check only: %CHECK_ONLY%
>>"%LOG%" echo.

echo ============================================================
echo Local AI GPP - local browser run
echo ============================================================
echo Root: %ROOT%
echo Log:  %LOG%
echo.

call :check_project || goto fail
call :setup_backend || goto fail
call :setup_frontend || goto fail
call :prepare_ports || goto fail
call :write_launchers || goto fail

echo.
echo [OK] Setup complete.
>>"%LOG%" echo [OK] Setup complete.

if "%CHECK_ONLY%"=="1" (
  echo [OK] Check completed. Nothing was started because --check was passed.
  >>"%LOG%" echo [OK] Check-only mode completed.
  echo.
  pause
  exit /b 0
)

if "%BACKEND_ALREADY_RUNNING%"=="1" (
  echo [OK] Backend is already running on port %BACKEND_PORT%. Reusing it.
  >>"%LOG%" echo [OK] Reusing existing backend.
) else (
  echo [INFO] Starting backend window...
  >>"%LOG%" echo [INFO] Starting backend window.
  start "Local AI GPP Backend" "%BACKEND_LAUNCHER%"
)

if "%FRONTEND_ALREADY_RUNNING%"=="1" (
  echo [OK] Frontend is already running on port %FRONTEND_PORT%. Reusing it.
  >>"%LOG%" echo [OK] Reusing existing frontend.
) else (
  echo [INFO] Starting frontend window...
  >>"%LOG%" echo [INFO] Starting frontend window.
  start "Local AI GPP Frontend" "%FRONTEND_LAUNCHER%"
)

timeout /t 4 /nobreak >nul
if "%ASSISTANT_MODE%"=="1" (
  start "" "http://127.0.0.1:%FRONTEND_PORT%/assistant"
) else (
  start "" "http://127.0.0.1:%FRONTEND_PORT%"
)

echo.
echo ============================================================
echo STARTED
echo ============================================================
echo Frontend: http://127.0.0.1:%FRONTEND_PORT%
echo Backend:  http://%BACKEND_HOST%:%BACKEND_PORT%/api/health
echo.
echo Backend/frontend are running in separate cmd windows when needed.
echo Close those windows to stop the app.
echo.
pause
exit /b 0


:check_project
echo [INFO] Checking project files...
>>"%LOG%" echo [INFO] Checking project files.

if not exist "%ROOT%\run_server.py" (
  echo [ERROR] Not found: %ROOT%\run_server.py
  >>"%LOG%" echo [ERROR] run_server.py not found.
  exit /b 1
)
if not exist "%ROOT%\backend\app\main.py" (
  echo [ERROR] Not found: %ROOT%\backend\app\main.py
  >>"%LOG%" echo [ERROR] backend\app\main.py not found.
  exit /b 1
)
if not exist "%ROOT%\backend\requirements.txt" (
  echo [ERROR] Not found: %ROOT%\backend\requirements.txt
  >>"%LOG%" echo [ERROR] backend\requirements.txt not found.
  exit /b 1
)
if not exist "%ROOT%\frontend\package.json" (
  echo [ERROR] Not found: %ROOT%\frontend\package.json
  >>"%LOG%" echo [ERROR] frontend\package.json not found.
  exit /b 1
)
exit /b 0


:find_python
echo [INFO] Searching Python...
>>"%LOG%" echo [INFO] Searching Python.
set "PY_CMD="

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 --version >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3.12"

  if not defined PY_CMD (
    py -3.13 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.13"
  )

  if not defined PY_CMD (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3"
  )
)

if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(1 if 'WindowsApps' in sys.executable else 0)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
  )
)

if not defined PY_CMD (
  echo [ERROR] Python not found. Install Python 3.12/3.13 from python.org or add python to PATH.
  >>"%LOG%" echo [ERROR] Python not found.
  exit /b 1
)

echo [OK] Python command: %PY_CMD%
>>"%LOG%" echo [OK] Python command: %PY_CMD%
%PY_CMD% --version >>"%LOG%" 2>&1
exit /b 0


:setup_backend
set "VENV_DIR=%ROOT%\backend\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "PYDEPS_DIR=%ROOT%\backend\.pydeps"
set "BACKEND_RUN_MODE="
set "BACKEND_PY_CMD="

if "%FORCE_SETUP%"=="1" (
  if exist "%VENV_DIR%" (
    echo [INFO] Removing old backend venv...
    >>"%LOG%" echo [INFO] Removing old backend venv.
    rmdir /s /q "%VENV_DIR%" >>"%LOG%" 2>&1
  )
  if exist "%PYDEPS_DIR%" (
    echo [INFO] Removing old backend .pydeps...
    >>"%LOG%" echo [INFO] Removing old backend .pydeps.
    rmdir /s /q "%PYDEPS_DIR%" >>"%LOG%" 2>&1
  )
)

if exist "%VENV_DIR%\pyvenv.cfg" (
  findstr /I /C:"WindowsApps" "%VENV_DIR%\pyvenv.cfg" >nul 2>nul
  if not errorlevel 1 (
    echo [WARN] Existing backend venv was created from Windows Store Python. Recreating it...
    >>"%LOG%" echo [WARN] Existing backend venv uses WindowsApps Python. Recreating it.
    rmdir /s /q "%VENV_DIR%" >>"%LOG%" 2>&1
  )
)

if exist "%VENV_PY%" (
  "%VENV_PY%" --version >nul 2>nul
  if errorlevel 1 (
    echo [WARN] Existing backend venv is broken. Recreating it...
    >>"%LOG%" echo [WARN] Existing backend venv is broken. Recreating it.
    rmdir /s /q "%VENV_DIR%" >>"%LOG%" 2>&1
  ) else (
    "%VENV_PY%" -m pip --version >nul 2>nul
    if errorlevel 1 (
      echo [WARN] Existing backend venv has no pip. Recreating it...
      >>"%LOG%" echo [WARN] Existing backend venv has no pip. Recreating it.
      rmdir /s /q "%VENV_DIR%" >>"%LOG%" 2>&1
    )
  )
)

if not exist "%VENV_PY%" (
  call :find_python || exit /b 1
  echo [INFO] Creating backend virtual environment...
  >>"%LOG%" echo [INFO] Creating backend virtual environment.
  call %%PY_CMD%% -m venv "%VENV_DIR%" >>"%LOG%" 2>&1
  if errorlevel 1 (
    echo [WARN] Failed to create backend venv. Falling back to backend\.pydeps.
    >>"%LOG%" echo [WARN] Failed to create backend venv. Falling back to backend\.pydeps.
  )
)

if exist "%VENV_PY%" (
  "%VENV_PY%" -m pip --version >nul 2>nul
  if errorlevel 1 (
    echo [WARN] Backend venv was created without pip. Falling back to backend\.pydeps.
    >>"%LOG%" echo [WARN] Backend venv was created without pip. Falling back to backend\.pydeps.
    rmdir /s /q "%VENV_DIR%" >>"%LOG%" 2>&1
  )
)

if exist "%VENV_PY%" (
  echo [OK] Backend venv: %VENV_PY%
  >>"%LOG%" echo [OK] Backend venv: %VENV_PY%
  set "BACKEND_RUN_MODE=venv"
  set "BACKEND_PY_CMD=%VENV_PY%"

  echo [INFO] Installing backend requirements...
  >>"%LOG%" echo [INFO] Installing backend requirements.
  "%VENV_PY%" -m pip install --upgrade pip >>"%LOG%" 2>&1
  if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    exit /b 1
  )
  "%VENV_PY%" -m pip install -r "%ROOT%\backend\requirements.txt" >>"%LOG%" 2>&1
  if errorlevel 1 (
    echo [ERROR] Failed to install backend requirements.
    exit /b 1
  )
  "%VENV_PY%" -c "import llama_cpp" >nul 2>nul
  if errorlevel 1 (
    echo [INFO] Installing default CPU llama.cpp runtime for development...
    >>"%LOG%" echo [INFO] Installing default CPU llama.cpp runtime for development.
    "%VENV_PY%" -m pip install --force-reinstall --no-cache-dir "llama-cpp-python==0.3.19" --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu" >>"%LOG%" 2>&1
    if errorlevel 1 (
      echo [ERROR] Failed to install CPU llama.cpp runtime.
      exit /b 1
    )
  )
  if exist "%PYDEPS_DIR%" (
    echo [INFO] Removing unused backend\.pydeps...
    >>"%LOG%" echo [INFO] Removing unused backend\.pydeps.
    rmdir /s /q "%PYDEPS_DIR%" >>"%LOG%" 2>&1
  )
  exit /b 0
)

if not defined PY_CMD (
  call :find_python || exit /b 1
)

if not exist "%PYDEPS_DIR%" mkdir "%PYDEPS_DIR%" >nul 2>nul
echo [INFO] Installing backend requirements into backend\.pydeps...
>>"%LOG%" echo [INFO] Installing backend requirements into backend\.pydeps.
call %%PY_CMD%% -m pip install --upgrade --target "%PYDEPS_DIR%" -r "%ROOT%\backend\requirements.txt" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to install backend requirements into backend\.pydeps.
  exit /b 1
)
call %%PY_CMD%% -m pip install --upgrade --target "%PYDEPS_DIR%" "llama-cpp-python==0.3.19" --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to install CPU llama.cpp runtime into backend\.pydeps.
  exit /b 1
)
set "BACKEND_RUN_MODE=pydeps"
set "BACKEND_PY_CMD=%PY_CMD%"
echo [OK] Backend Python deps: %PYDEPS_DIR%
>>"%LOG%" echo [OK] Backend Python deps: %PYDEPS_DIR%
exit /b 0


:setup_frontend
echo [INFO] Checking Node.js and npm...
>>"%LOG%" echo [INFO] Checking Node.js and npm.
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] node not found. Install Node.js LTS.
  >>"%LOG%" echo [ERROR] node not found.
  exit /b 1
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm.cmd not found. Install Node.js LTS.
  >>"%LOG%" echo [ERROR] npm.cmd not found.
  exit /b 1
)
node --version >>"%LOG%" 2>&1
call npm.cmd --version >>"%LOG%" 2>&1

if "%FORCE_SETUP%"=="1" (
  if exist "%ROOT%\frontend\node_modules" (
    echo [INFO] Removing old frontend node_modules...
    >>"%LOG%" echo [INFO] Removing old frontend node_modules.
    rmdir /s /q "%ROOT%\frontend\node_modules" >>"%LOG%" 2>&1
  )
)

if not exist "%ROOT%\frontend\node_modules" (
  echo [INFO] Installing frontend npm dependencies...
  >>"%LOG%" echo [INFO] Installing frontend npm dependencies.
  pushd "%ROOT%\frontend" >nul
  call npm.cmd install >>"%LOG%" 2>&1
  if errorlevel 1 (
    popd >nul
    echo [ERROR] npm install failed.
    exit /b 1
  )
  popd >nul
) else (
  echo [OK] frontend\node_modules already exists.
  >>"%LOG%" echo [OK] frontend\node_modules already exists.
)
exit /b 0


:prepare_ports
set "BACKEND_ALREADY_RUNNING=0"
set "FRONTEND_ALREADY_RUNNING=0"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"

if "%RESET_PORTS%"=="1" (
  echo [INFO] Resetting ports 8000 and 5173...
  >>"%LOG%" echo [INFO] Resetting ports 8000 and 5173.
  call :kill_port 8000
  call :kill_port 5173
  timeout /t 2 /nobreak >nul
)

call :check_url "http://127.0.0.1:8000/api/health"
if not errorlevel 1 (
  set "BACKEND_ALREADY_RUNNING=1"
  set "BACKEND_PORT=8000"
) else (
  call :find_free_port 8000 8099 BACKEND_PORT
  if errorlevel 1 (
    echo [ERROR] No free backend ports in range 8000-8099.
    >>"%LOG%" echo [ERROR] No free backend ports in range 8000-8099.
    exit /b 1
  )
)
if not "%BACKEND_PORT%"=="8000" (
  echo [WARN] Port 8000 is busy or unhealthy. Using backend port %BACKEND_PORT%.
  >>"%LOG%" echo [WARN] Port 8000 is busy or unhealthy. Using backend port %BACKEND_PORT%.
)

call :find_free_port 5173 5299 FRONTEND_PORT
if errorlevel 1 (
  echo [ERROR] No free frontend ports in range 5173-5299.
  >>"%LOG%" echo [ERROR] No free frontend ports in range 5173-5299.
  exit /b 1
)
if not "%FRONTEND_PORT%"=="5173" (
  echo [WARN] Port 5173 is busy. Using frontend port %FRONTEND_PORT%.
  >>"%LOG%" echo [WARN] Port 5173 is busy. Using frontend port %FRONTEND_PORT%.
)

echo [OK] Backend port: %BACKEND_PORT%
echo [OK] Frontend port: %FRONTEND_PORT%
>>"%LOG%" echo [OK] Backend port: %BACKEND_PORT%
>>"%LOG%" echo [OK] Frontend port: %FRONTEND_PORT%
exit /b 0


:check_url
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri '%~1'; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) { exit 0 } } catch {}; exit 1" >nul 2>nul
exit /b %ERRORLEVEL%


:is_port_busy
netstat -ano | findstr /r /c:":%1 .*LISTENING" >nul 2>nul
exit /b %ERRORLEVEL%


:find_free_port
set "PORT_RESULT="
for /L %%P in (%~1,1,%~2) do (
  if not defined PORT_RESULT (
    netstat -ano | findstr /r /c:":%%P .*LISTENING" >nul 2>nul
    if errorlevel 1 set "PORT_RESULT=%%P"
  )
)
if not defined PORT_RESULT exit /b 1
set "%~3=%PORT_RESULT%"
exit /b 0


:print_port_pids
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do (
  echo   PID %%P
)
exit /b 0


:kill_port
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do (
  echo [INFO] Killing PID %%P on port %1
  >>"%LOG%" echo [INFO] Killing PID %%P on port %1
  taskkill /PID %%P /F >>"%LOG%" 2>&1
)
exit /b 0


:write_launchers
echo [INFO] Writing helper launchers...
>>"%LOG%" echo [INFO] Writing helper launchers.

>"%BACKEND_LAUNCHER%" echo @echo off
>>"%BACKEND_LAUNCHER%" echo chcp 65001 ^>nul
>>"%BACKEND_LAUNCHER%" echo cd /d "%%~dp0..\.."
>>"%BACKEND_LAUNCHER%" echo set "LOCAL_AI_GPP_HOST=%BACKEND_HOST%"
>>"%BACKEND_LAUNCHER%" echo set "LOCAL_AI_GPP_PORT=%BACKEND_PORT%"
>>"%BACKEND_LAUNCHER%" echo set "LOCAL_AI_GPP_CORS_ORIGINS=http://127.0.0.1:%FRONTEND_PORT%,http://localhost:%FRONTEND_PORT%"
>>"%BACKEND_LAUNCHER%" echo set "NO_PROXY=localhost,127.0.0.1,::1,[::1],*.localhost"
>>"%BACKEND_LAUNCHER%" echo set "no_proxy=localhost,127.0.0.1,::1,[::1],*.localhost"
>>"%BACKEND_LAUNCHER%" echo set "LOCAL_AI_GPP_PROXY_BYPASS=1"
>>"%BACKEND_LAUNCHER%" echo echo Backend: http://%BACKEND_HOST%:%BACKEND_PORT%/api/health
>>"%BACKEND_LAUNCHER%" echo echo.
if "%BACKEND_RUN_MODE%"=="pydeps" (
  >>"%BACKEND_LAUNCHER%" echo set "PYTHONPATH=%%CD%%;%%CD%%\backend\.pydeps"
  >>"%BACKEND_LAUNCHER%" echo call %BACKEND_PY_CMD% "%%CD%%\run_server.py"
) else (
  >>"%BACKEND_LAUNCHER%" echo set "PYTHONPATH=%%CD%%"
  >>"%BACKEND_LAUNCHER%" echo "%%CD%%\backend\.venv\Scripts\python.exe" "%%CD%%\run_server.py"
)
>>"%BACKEND_LAUNCHER%" echo echo.
>>"%BACKEND_LAUNCHER%" echo echo Backend stopped or failed.
>>"%BACKEND_LAUNCHER%" echo pause

>"%FRONTEND_LAUNCHER%" echo @echo off
>>"%FRONTEND_LAUNCHER%" echo chcp 65001 ^>nul
>>"%FRONTEND_LAUNCHER%" echo cd /d "%%~dp0..\..\frontend"
>>"%FRONTEND_LAUNCHER%" echo set VITE_API_BASE=http://%BACKEND_HOST%:%BACKEND_PORT%
>>"%FRONTEND_LAUNCHER%" echo set "NO_PROXY=localhost,127.0.0.1,::1,[::1],*.localhost"
>>"%FRONTEND_LAUNCHER%" echo set "no_proxy=localhost,127.0.0.1,::1,[::1],*.localhost"
>>"%FRONTEND_LAUNCHER%" echo set "LOCAL_AI_GPP_PROXY_BYPASS=1"
>>"%FRONTEND_LAUNCHER%" echo echo Frontend: http://127.0.0.1:%FRONTEND_PORT%
>>"%FRONTEND_LAUNCHER%" echo echo.
>>"%FRONTEND_LAUNCHER%" echo call npm.cmd run dev -- --host 127.0.0.1 --port %FRONTEND_PORT% --strictPort
>>"%FRONTEND_LAUNCHER%" echo echo.
>>"%FRONTEND_LAUNCHER%" echo echo Frontend stopped or failed.
>>"%FRONTEND_LAUNCHER%" echo pause
exit /b 0


:fail
echo.
echo ============================================================
echo FAILED
echo ============================================================
echo Log: %LOG%
echo.
if exist "%LOG%" (
  echo Last log lines:
  echo ------------------------------------------------------------
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%LOG%' -Tail 120"
  echo ------------------------------------------------------------
) else (
  echo Log file was not created.
)
echo.
pause
exit /b 1
