# Geo Analyzer safe cleanup script
# Run from project root:
# powershell -ExecutionPolicy Bypass -File scripts/cleanup_safe.ps1

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

Write-Host "Geo Analyzer cleanup"
Write-Host "Project root: $Root"
Write-Host ""

$DirsToRemove = @(
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    "data/debug",
    "output",
    "logs"
)

$FilesToRemove = @(
    "GeoAnalyzer.spec",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.bak",
    "*.old"
)

function Remove-DirSafe {
    param(
        [string]$RelativePath
    )

    $Path = Join-Path $Root $RelativePath

    if (Test-Path $Path) {
        Write-Host "Removing directory: $RelativePath"
        Remove-Item -Path $Path -Recurse -Force
    }
}

function Remove-FilesByMask {
    param(
        [string]$Mask
    )

    Get-ChildItem -Path $Root -Recurse -Force -File -Filter $Mask |
        Where-Object {
            $_.FullName -notmatch "\\.git\\" -and
            $_.FullName -notmatch "\\venv\\" -and
            $_.FullName -notmatch "\\.venv\\"
        } |
        ForEach-Object {
            $Relative = $_.FullName.Replace($Root, "").TrimStart("\")
            Write-Host "Removing file: $Relative"
            Remove-Item -Path $_.FullName -Force
        }
}

foreach ($Dir in $DirsToRemove) {
    Remove-DirSafe $Dir
}

foreach ($Mask in $FilesToRemove) {
    Remove-FilesByMask $Mask
}

Get-ChildItem -Path $Root -Recurse -Force -Directory -Filter "__pycache__" |
    Where-Object {
        $_.FullName -notmatch "\\.git\\" -and
        $_.FullName -notmatch "\\venv\\" -and
        $_.FullName -notmatch "\\.venv\\"
    } |
    ForEach-Object {
        $Relative = $_.FullName.Replace($Root, "").TrimStart("\")
        Write-Host "Removing directory: $Relative"
        Remove-Item -Path $_.FullName -Recurse -Force
    }

Write-Host ""
Write-Host "Done. Source code, configs and docs were not removed."