@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
set "DIST=%ROOT%\dist"
set "WORKER=%DIST%\worker_runtime\python.exe"
set "PTH_FILE=%DIST%\worker_runtime\python312._pth"
set "LOG=%ROOT%\tools\out\smoke_test_exe.log"
if not exist "%ROOT%\tools\out" mkdir "%ROOT%\tools\out" >nul 2>nul

>"%LOG%" echo Local AI GPP EXE smoke test v67.4
>>"%LOG%" echo Root: %ROOT%
>>"%LOG%" echo Started: %DATE% %TIME%

echo ============================================================
echo Local AI GPP - EXE smoke test v67.4
echo ============================================================
echo Log: %LOG%
echo.

call :check_file "%DIST%\LocalAIGPP.exe" "EXE" || goto fail
call :check_file "%WORKER%" "worker python" || goto fail
call :check_file "%PTH_FILE%" "embedded python path file" || goto fail
call :check_file "%DIST%\backend\app\core.py" "portable backend core" || goto fail
call :check_file "%DIST%\backend\app\llama_worker.py" "portable llama worker" || goto fail
call :check_file "%DIST%\models_storage" "portable models storage" || goto fail
call :check_file "%DIST%\models_storage\settings.json" "portable settings" || goto fail
call :check_file "%DIST%\models_storage\models.json" "portable model registry" || goto fail
call :check_file "%DIST%\LocalAIGPP.ico" "portable icon" || goto fail

REM Source-only checks. They are skipped on a copied dist-only machine.
if exist "%ROOT%\tools\exe_launcher.py" (
  findstr /C:"V67_4_DESKTOP_SYNC_MULTI_CONVERSATION" "%ROOT%\tools\exe_launcher.py" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] tools\exe_launcher.py does not contain canonical desktop sync marker.
    >>"%LOG%" echo [ERROR] launcher marker missing.
    goto fail
  )
  echo [OK] launcher canonical sync marker present.
  >>"%LOG%" echo [OK] launcher canonical sync marker present.
)

if exist "%ROOT%\frontend\src\App.tsx" (
  findstr /C:"V67_4_DESKTOP_SYNC_MULTI_CONVERSATION" "%ROOT%\frontend\src\App.tsx" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] frontend\src\App.tsx does not contain canonical desktop sync marker.
    >>"%LOG%" echo [ERROR] frontend marker missing.
    goto fail
  )
  echo [OK] frontend canonical sync marker present.
  >>"%LOG%" echo [OK] frontend canonical sync marker present.
)

if exist "%ROOT%\backend\app\main.py" (
  findstr /R /C:"@app\.get('/api/desktop/chat-sync')" /C:"@app\.get(\"/api/desktop/chat-sync\")" "%ROOT%\backend\app\main.py" >nul 2>nul
  if not errorlevel 1 (
    echo [ERROR] backend\app\main.py still registers /api/desktop/chat-sync.
    >>"%LOG%" echo [ERROR] backend main still registers desktop chat-sync route.
    goto fail
  )
  echo [OK] backend has no desktop chat-sync route owner.
  >>"%LOG%" echo [OK] backend desktop route owner absent.
)

"%WORKER%" -c "from pathlib import Path; p=Path(r'%PTH_FILE%'); lines=[x.strip().lstrip('\ufeff') for x in p.read_text(encoding='utf-8', errors='replace').splitlines()]; raise SystemExit(0 if '..' in lines else 1)" >nul 2>>"%LOG%"
if errorlevel 1 (
  echo [ERROR] worker_runtime\python312._pth does not contain exact ".." line.
  >>"%LOG%" echo [ERROR] python312._pth missing exact .. line.
  goto fail
)
echo [OK] embedded Python ._pth contains backend parent path.
>>"%LOG%" echo [OK] python312._pth contains exact .. line.

pushd "%DIST%" >nul
"%WORKER%" -c "import sys,json,os; import backend.app.llama_worker, llama_cpp; print(json.dumps({'ok': True, 'python': sys.executable, 'cwd': os.getcwd(), 'llama_cpp': getattr(llama_cpp,'__version__','unknown'), 'path': sys.path[:10]}, ensure_ascii=False, indent=2))" >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
popd >nul
if not "%RC%"=="0" goto fail_with_log

echo.
echo [OK] Smoke test completed.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%LOG%' -Tail 120"
exit /b 0

:check_file
if not exist "%~1" (
  echo [ERROR] Missing %~2: %~1
  >>"%LOG%" echo [ERROR] Missing %~2: %~1
  exit /b 1
)
echo [OK] Found %~2: %~1
>>"%LOG%" echo [OK] Found %~2: %~1
exit /b 0

:fail_with_log
echo [ERROR] Smoke test failed. Last log lines:
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath '%LOG%') { Get-Content -LiteralPath '%LOG%' -Tail 180 }"

:fail
echo.
echo ============================================================
echo FAILED
echo ============================================================
echo Log: %LOG%
echo.
echo Useful checks:
echo   dir dist\worker_runtime
echo   type dist\worker_runtime\python312._pth
echo   tools\28_check_dist_portable.bat
echo.
exit /b 1
