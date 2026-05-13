@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
set "DIST=%ROOT%\dist"
set "WORKER=%DIST%\worker_runtime\python.exe"
set "LOG=%ROOT%\tools\out\smoke_test_exe.log"
if not exist "%ROOT%\tools\out" mkdir "%ROOT%\tools\out" >nul 2>nul

>"%LOG%" echo Local AI GPP EXE smoke test v66
>>"%LOG%" echo Root: %ROOT%
>>"%LOG%" echo Started: %DATE% %TIME%

echo ============================================================
echo Local AI GPP - EXE smoke test v66
echo ============================================================
echo Log: %LOG%
echo.

call :must_exist "%DIST%\LocalAIGPP.exe" || goto fail
call :must_exist "%WORKER%" || goto fail
call :must_exist "%DIST%\worker_runtime\python312._pth" || goto fail
call :must_exist "%DIST%\backend\app\core.py" || goto fail
call :must_exist "%DIST%\backend\app\llama_worker.py" || goto fail
call :must_exist "%DIST%\models_storage" || goto fail
call :must_exist "%ROOT%\models_storage\branding\icons\local_ai_gpp.ico" || goto fail
call :must_exist "%DIST%\LocalAIGPP.ico" || goto fail
call :must_exist "%ROOT%\models_storage\branding\icons\local_ai_gpp.ico" || goto fail
call :must_exist "%ROOT%\frontend\public\assistant\frames\ready_0.png" || goto fail
call :must_exist "%ROOT%\frontend\public\assistant\frames\thinking_0.png" || goto fail
call :must_exist "%ROOT%\frontend\public\assistant\frames\answered_0.png" || goto fail

if exist "%LOCALAPPDATA%\LocalAIGPP\launcher.log" (
  echo [INFO] Launcher log: %LOCALAPPDATA%\LocalAIGPP\launcher.log
  >>"%LOG%" echo [INFO] Launcher log exists: %LOCALAPPDATA%\LocalAIGPP\launcher.log
) else (
  echo [INFO] Launcher log will appear after EXE start: %LOCALAPPDATA%\LocalAIGPP\launcher.log
  >>"%LOG%" echo [INFO] Launcher log not yet created.
)
if exist "%DIST%\logs\launcher.log" (
  echo [INFO] Dist launcher log: %DIST%\logs\launcher.log
  >>"%LOG%" echo [INFO] Dist launcher log exists: %DIST%\logs\launcher.log
)

findstr /C:".." "%DIST%\worker_runtime\python312._pth" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] worker_runtime\python312._pth does not contain .. backend path.
  >>"%LOG%" echo [ERROR] python312._pth does not contain ..
  goto fail
)
echo [OK] python312._pth contains backend parent path.
>>"%LOG%" echo [OK] python312._pth contains ..

findstr /C:"V66_CANONICAL_SHARED_CHAT_REMOTE_REPLACE" "%ROOT%\tools\exe_launcher.py" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] tools\exe_launcher.py is not v66 native assistant launcher.
  >>"%LOG%" echo [ERROR] launcher version marker missing.
  goto fail
)
echo [OK] launcher version marker: v66.
>>"%LOG%" echo [OK] launcher version marker v66.

findstr /C:"V66_CANONICAL_SHARED_CHAT_REMOTE_REPLACE" "%ROOT%\frontend\src\App.tsx" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] frontend\src\App.tsx is not v66 canonical shared chat UI.
  >>"%LOG%" echo [ERROR] frontend v66 marker missing.
  goto fail
)
echo [OK] frontend canonical shared-chat marker: v66.
>>"%LOG%" echo [OK] frontend canonical shared-chat marker v66.

pushd "%DIST%" >nul
"%WORKER%" -c "import sys,json; import backend.app.llama_worker, llama_cpp; print(json.dumps({'python': sys.executable, 'llama_cpp': getattr(llama_cpp,'__version__','unknown'), 'path': sys.path[:8]}, ensure_ascii=False, indent=2))" >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
popd >nul
if not "%RC%"=="0" goto fail_with_log

echo.
echo [OK] Smoke test completed.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%LOG%' -Tail 90"
echo.
echo Press any key to close smoke test.
pause >nul
exit /b 0

:must_exist
if not exist "%~1" (
  echo [ERROR] Missing: %~1
  >>"%LOG%" echo [ERROR] Missing: %~1
  exit /b 1
)
echo [OK] Found: %~1
>>"%LOG%" echo [OK] Found: %~1
exit /b 0

:fail_with_log
echo [ERROR] Smoke test failed. Last log lines:
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath '%LOG%') { Get-Content -LiteralPath '%LOG%' -Tail 160 }"

:fail
echo.
echo ============================================================
echo FAILED
echo ============================================================
echo Log: %LOG%
echo.
echo Press any key to close smoke test.
pause >nul
exit /b 1