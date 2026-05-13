@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

rem ============================================================
rem Local AI GPP - dump project tree
rem Output: tools\out\project_tree.txt
rem ============================================================

set "TOOLS_DIR=%~dp0"
for %%I in ("%TOOLS_DIR%..") do set "ROOT=%%~fI"
set "OUT_DIR=%TOOLS_DIR%out"
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%" >nul 2>nul

set "OUT=%OUT_DIR%\project_tree.txt"
set "LOG=%OUT_DIR%\04_dump_project_tree.log"

> "%LOG%" echo dump_project_tree started: %DATE% %TIME%
>>"%LOG%" echo Root: %ROOT%

> "%OUT%" echo ============================================================
>>"%OUT%" echo PROJECT TREE - LOCAL AI GPP
>>"%OUT%" echo ============================================================
>>"%OUT%" echo Root: %ROOT%
>>"%OUT%" echo Generated: %DATE% %TIME%
>>"%OUT%" echo.
>>"%OUT%" echo Folders overview:
if exist "%ROOT%\backend\"        >>"%OUT%" echo - backend\        - FastAPI backend and llama.cpp runtime
if exist "%ROOT%\frontend\"       >>"%OUT%" echo - frontend\       - React/Vite user interface
if exist "%ROOT%\models_storage\" >>"%OUT%" echo - models_storage\ - model registry, settings, branding assets
if exist "%ROOT%\tools\"          >>"%OUT%" echo - tools\          - launch, build and dump scripts
>>"%OUT%" echo.
>>"%OUT%" echo Tree:
>>"%OUT%" echo ------------------------------------------------------------
>>"%OUT%" echo [D] .

call :walk "%ROOT%" "  "
if errorlevel 1 goto fail

>>"%OUT%" echo.
>>"%OUT%" echo ============================================================
>>"%OUT%" echo END
>>"%OUT%" echo ============================================================

echo DONE: %OUT%
>>"%LOG%" echo DONE: %OUT%
pause
exit /b 0

:walk
setlocal EnableDelayedExpansion
set "CURRENT=%~1"
set "PREFIX=%~2"

for /f "delims=" %%D in ('dir /b /ad "%CURRENT%" 2^>nul') do (
  call :is_excluded_dir "%%D" "%CURRENT%\%%D"
  if "!SKIP!"=="0" (
    >>"%OUT%" echo !PREFIX![D] %%D
    call :walk "%CURRENT%\%%D" "!PREFIX!  "
  )
)

for /f "delims=" %%F in ('dir /b /a-d "%CURRENT%" 2^>nul') do (
  call :is_excluded_file "%%F" "%CURRENT%\%%F"
  if "!SKIP!"=="0" >>"%OUT%" echo !PREFIX![F] %%F
)

endlocal
exit /b 0

:is_excluded_dir
set "NAME=%~1"
set "FULL=%~2"
set "SKIP=0"
if /I "%NAME%"==".git" set "SKIP=1"
if /I "%NAME%"==".idea" set "SKIP=1"
if /I "%NAME%"==".vscode" set "SKIP=1"
if /I "%NAME%"==".venv" set "SKIP=1"
if /I "%NAME%"=="venv" set "SKIP=1"
if /I "%NAME%"=="env" set "SKIP=1"
if /I "%NAME%"=="__pycache__" set "SKIP=1"
if /I "%NAME%"=="node_modules" set "SKIP=1"
if /I "%NAME%"=="dist" set "SKIP=1"
if /I "%NAME%"=="build" set "SKIP=1"
if /I "%NAME%"=="coverage" set "SKIP=1"
if /I "%NAME%"=="pip-cache" set "SKIP=1"
if /I "%NAME%"=="tmp" set "SKIP=1"
if /I "%NAME%"=="out" (
  echo "%FULL%" | findstr /I "\\tools\\out$" >nul
  if not errorlevel 1 set "SKIP=1"
)
exit /b 0

:is_excluded_file
set "NAME=%~1"
set "SKIP=0"
if /I "%NAME%"=="package-lock.json" set "SKIP=1"
if /I "%NAME%"=="yarn.lock" set "SKIP=1"
if /I "%NAME%"=="pnpm-lock.yaml" set "SKIP=1"
if /I "%NAME%"=="tsconfig.tsbuildinfo" set "SKIP=1"
if /I "%NAME:~-5%"==".gguf" set "SKIP=1"
if /I "%NAME:~-4%"==".bin" set "SKIP=1"
if /I "%NAME:~-5%"==".safetensors" set "SKIP=1"
exit /b 0

:fail
echo FAILED. See log: %LOG%
if exist "%LOG%" type "%LOG%"
pause
exit /b 1
