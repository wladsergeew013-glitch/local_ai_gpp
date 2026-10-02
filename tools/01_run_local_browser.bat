@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
if not exist "backend\.venv\Scripts\python.exe" (
  echo Create backend\.venv first. See README.md.
  exit /b 1
)
if not exist "frontend\dist\index.html" (
  echo Build the frontend first: cd frontend ^&^& npm ci ^&^& npm run build
  exit /b 1
)
if not defined LOCAL_AI_GPP_PORT set "LOCAL_AI_GPP_PORT=8765"
echo Local AI: http://127.0.0.1:%LOCAL_AI_GPP_PORT%
"backend\.venv\Scripts\python.exe" run_server.py
exit /b %ERRORLEVEL%
