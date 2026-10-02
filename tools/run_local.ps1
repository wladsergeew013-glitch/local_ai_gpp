param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Создайте backend\.venv и установите зависимости согласно README.md.' }
$env:LOCAL_AI_GPP_PORT = [string]$Port
Set-Location -LiteralPath $taskRoot
& $taskPython (Join-Path $taskRoot 'run_server.py')
exit $LASTEXITCODE
