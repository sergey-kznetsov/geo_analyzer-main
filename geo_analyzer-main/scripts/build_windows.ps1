param(
    [switch]$SkipTests,
    [switch]$NoZip,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Fail {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
    exit 1
}

function Get-ProjectRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-VenvPython {
    param([string]$Root)

    $candidates = @(
        (Join-Path $Root "venv\Scripts\python.exe"),
        (Join-Path $Root ".venv\Scripts\python.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $venvDir = Join-Path $Root "venv"
    python -m venv $venvDir

    if ($LASTEXITCODE -ne 0) {
        Fail "Could not create virtual environment."
    }

    $created = Join-Path $venvDir "Scripts\python.exe"

    if (-not (Test-Path $created)) {
        Fail "Virtual environment was created but python.exe was not found."
    }

    return $created
}

function Get-EnvValue {
    param(
        [string]$Path,
        [string[]]$Names
    )

    if (-not (Test-Path $Path)) {
        return ""
    }

    $lines = Get-Content -Path $Path -Encoding UTF8

    foreach ($name in $Names) {
        foreach ($line in $lines) {
            $trimmed = $line.Trim()

            if ($trimmed -eq "" -or $trimmed.StartsWith("#")) {
                continue
            }

            if ($trimmed -match "^\s*$([regex]::Escape($name))\s*=\s*(.+?)\s*$") {
                $value = $Matches[1].Trim()
                $value = $value.Trim('"').Trim("'")

                if ($value -ne "") {
                    return $value
                }
            }
        }
    }

    return ""
}

function Write-EmbeddedSecret {
    param(
        [string]$ApiKey,
        [string]$TargetPath
    )

    $xorKey = 73
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($ApiKey)
    $encoded = New-Object System.Collections.Generic.List[string]

    foreach ($byte in $bytes) {
        $encoded.Add([string]($byte -bxor $xorKey))
    }

    $encodedText = $encoded -join ", "
    $parent = Split-Path -Path $TargetPath -Parent

    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $content = @"
from __future__ import annotations

_XOR_KEY = $xorKey
_ENCODED_DGIS_API_KEY = [$encodedText]


def get_embedded_dgis_api_key() -> str:
    try:
        return bytes([value ^ _XOR_KEY for value in _ENCODED_DGIS_API_KEY]).decode("utf-8")
    except Exception:
        return ""
"@

    Set-Content -Path $TargetPath -Value $content -Encoding UTF8
}

function Ensure-Directory {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

$Root = Get-ProjectRoot
Set-Location $Root

Write-Step "Check project structure"

$requiredPaths = @(
    "gui.py",
    "main.py",
    "build.spec",
    "requirements.txt",
    "pyproject.toml",
    "config\config.yaml",
    "src\geo_analyzer\__init__.py",
    "src\geo_analyzer\gui\app.py",
    "src\geo_analyzer\cli\main.py",
    "src\geo_analyzer\core\settings.py",
    "src\geo_analyzer\core\secrets.py"
)

foreach ($path in $requiredPaths) {
    $fullPath = Join-Path $Root $path

    if (-not (Test-Path $fullPath)) {
        Fail "Required file not found: $path"
    }
}

Write-Ok "Project structure is valid"

Write-Step "Read 2GIS API key"

$envPath = Join-Path $Root ".env"
$apiKey = Get-EnvValue -Path $envPath -Names @(
    "DGIS_API_KEY",
    "DGIS_KEY",
    "TWOGIS_API_KEY",
    "API_KEY_2GIS",
    "2GIS_API_KEY"
)

if ($apiKey -eq "") {
    Fail "2GIS API key is missing. Create .env in project root and add DGIS_API_KEY=your_key"
}

$embeddedSecretPath = Join-Path $Root "src\geo_analyzer\core\_embedded_secret.py"
Write-EmbeddedSecret -ApiKey $apiKey -TargetPath $embeddedSecretPath
Write-Ok "2GIS API key was embedded for portable build"

Write-Step "Prepare virtual environment"

$venvPython = Get-VenvPython -Root $Root
Write-Ok "Python: $venvPython"

Write-Step "Install dependencies"

& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    Fail "pip upgrade failed"
}

& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Fail "requirements installation failed"
}

Write-Ok "Dependencies installed"

Write-Step "Compile check"

$oldPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $Root "src"

& $venvPython -m compileall -q src main.py gui.py
if ($LASTEXITCODE -ne 0) {
    $env:PYTHONPATH = $oldPythonPath
    Fail "Compile check failed"
}

Write-Ok "Compile check passed"

if (-not $SkipTests) {
    Write-Step "Run tests"

    & $venvPython -m pytest
    if ($LASTEXITCODE -ne 0) {
        $env:PYTHONPATH = $oldPythonPath
        Fail "Tests failed. Re-run build with -SkipTests only if this is expected."
    }

    Write-Ok "Tests passed"
} else {
    Write-Warn "Tests skipped"
}

if ($Clean) {
    Write-Step "Clean previous build"

    $buildDir = Join-Path $Root "build"
    $distDir = Join-Path $Root "dist"

    if (Test-Path $buildDir) {
        Remove-Item $buildDir -Recurse -Force
    }

    if (Test-Path $distDir) {
        Remove-Item $distDir -Recurse -Force
    }

    Write-Ok "Previous build removed"
}

Write-Step "Build EXE with PyInstaller"

& $venvPython -m PyInstaller --noconfirm --clean build.spec
if ($LASTEXITCODE -ne 0) {
    $env:PYTHONPATH = $oldPythonPath
    Fail "PyInstaller failed"
}

$env:PYTHONPATH = $oldPythonPath

$distDir = Join-Path $Root "dist"
$appDir = Join-Path $distDir "GeoAnalyzer"
$exePath = Join-Path $appDir "GeoAnalyzer.exe"

if (-not (Test-Path $exePath)) {
    Fail "GeoAnalyzer.exe not found after build: $exePath"
}

Write-Ok "EXE created: $exePath"

Write-Step "Create portable runtime folders"

$runtimeDirs = @(
    "data",
    "data\output",
    "data\cache",
    "data\benchmarks",
    "logs"
)

foreach ($relative in $runtimeDirs) {
    Ensure-Directory -Path (Join-Path $appDir $relative)
}

Write-Ok "Portable runtime folders created"

Write-Step "Add README_START.txt"

$readmePath = Join-Path $appDir "README_START.txt"
$readmeContent = @"
Geo Analyzer portable build for Windows

How to run:
1. Extract the ZIP archive into a normal folder.
2. Do not run GeoAnalyzer.exe directly from ZIP.
3. Open GeoAnalyzer.exe.
4. Enter one address or use comparison mode.
5. After completion, open the result folder or Excel report from the interface.

Runtime folders are located inside this portable folder:
- data\output: analysis results and comparison reports
- data\cache: API cache
- data\benchmarks: city benchmarks
- logs: application logs

Python is not required on the user's computer.
The 2GIS API key is embedded into this build.
"@

Set-Content -Path $readmePath -Value $readmeContent -Encoding UTF8
Write-Ok "README_START.txt added"

if (-not $NoZip) {
    Write-Step "Create portable ZIP"

    $zipPath = Join-Path $distDir "GeoAnalyzer_windows_portable.zip"

    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force
    }

    Compress-Archive -Path $appDir -DestinationPath $zipPath -Force

    if (-not (Test-Path $zipPath)) {
        Fail "ZIP was not created"
    }

    Write-Ok "ZIP created: $zipPath"
} else {
    Write-Warn "ZIP skipped"
}

Write-Step "Done"

Write-Host "EXE:" -ForegroundColor Green
Write-Host $exePath

if (-not $NoZip) {
    Write-Host ""
    Write-Host "ZIP:" -ForegroundColor Green
    Write-Host (Join-Path $distDir "GeoAnalyzer_windows_portable.zip")
}

Write-Host ""
Write-Host "Portable runtime folder:" -ForegroundColor Green
Write-Host $appDir