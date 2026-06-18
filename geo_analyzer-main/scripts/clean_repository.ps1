param(
    [switch]$All,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Get-ProjectRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Remove-PathSafe {
    param(
        [string]$Root,
        [string]$RelativePath
    )

    $target = Join-Path $Root $RelativePath
    $resolvedRoot = (Resolve-Path $Root).Path

    if (-not (Test-Path $target)) {
        return
    }

    $resolvedTarget = (Resolve-Path $target).Path

    if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside project root: $resolvedTarget"
    }

    if ($DryRun) {
        Write-Host "[DRY] Remove $RelativePath" -ForegroundColor Yellow
        return
    }

    Remove-Item -Path $resolvedTarget -Recurse -Force
    Write-Host "[OK] Removed $RelativePath" -ForegroundColor Green
}

$Root = Get-ProjectRoot
Set-Location $Root

$paths = @(
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    "tmp",
    "temp",
    "logs",
    "cache\parking_supply",
    "cache\places",
    "cache\dgis_category_catalog",
    "data\cache",
    "data\data",
    "data\debug",
    "data\output",
    "data\benchmarks",
    "reports\generated",
    "reports\tmp"
)

if ($All) {
    $paths += @(
        "*.egg-info"
    )
}

foreach ($path in $paths) {
    if ($path.Contains("*")) {
        Get-ChildItem -Path $Root -Filter $path -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $relative = Resolve-Path -Path $_.FullName -Relative
            Remove-PathSafe -Root $Root -RelativePath $relative
        }
        continue
    }

    Remove-PathSafe -Root $Root -RelativePath $path
}

$keepDirs = @(
    "cache",
    "cache\graphs",
    "data",
    "data\cache",
    "data\output",
    "data\raw",
    "data\processed",
    "data\debug",
    "data\benchmarks",
    "reports",
    "reports\generated",
    "reports\tmp"
)

foreach ($dir in $keepDirs) {
    $full = Join-Path $Root $dir
    if (-not (Test-Path $full) -and -not $DryRun) {
        New-Item -ItemType Directory -Path $full -Force | Out-Null
    }
}

Write-Host "Cleanup completed." -ForegroundColor Cyan
