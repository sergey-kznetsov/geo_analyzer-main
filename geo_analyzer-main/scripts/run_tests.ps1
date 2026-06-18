$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

if (!(Test-Path $PythonExe)) {
    python -m venv .venv
}

& $PythonExe -m pip install -U pip
& $PythonExe -m pip install -r requirements.txt
& $PythonExe -m pytest tests -q