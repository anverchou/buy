# Runs the daily price snapshot on Windows. Safe to schedule hourly: products
# already checked today are skipped, so only the first run of the day calls the APIs.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
New-Item -ItemType Directory -Force -Path (Join-Path $root "instance") | Out-Null
& $python -m flask --app app snapshot @args *>> (Join-Path $root "instance\snapshot.log")
exit $LASTEXITCODE
