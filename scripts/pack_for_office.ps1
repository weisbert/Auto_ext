# Pack the Auto_ext working tree into a single .tar.gz for cross-zone transfer.
#
# Why: our cross-zone (yellow -> red) transfer tool corrupts hidden files
# (.git/, .pytest_cache/, etc.). Wrapping the working tree in a single
# non-hidden tarball sidesteps that bug -- the transfer tool only sees
# Auto_ext_bundle.tar.gz.
#
# Usage:
#   From VS Code terminal (cwd = repo root):
#       .\scripts\pack_for_office.ps1
#   From any Windows shell or File Explorer double-click:
#       scripts\pack.bat
#   If PowerShell ExecutionPolicy blocks the .ps1 directly, use pack.bat
#   instead -- it always passes -ExecutionPolicy Bypass.
#
# Output: ..\Auto_ext_bundle.tar.gz (one level above repo root, so the bundle
# never includes itself if you re-run pack).

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$bundleName = 'Auto_ext_bundle.tar.gz'
$bundlePath = Join-Path (Split-Path $repoRoot -Parent) $bundleName

$includes = @(
    'auto_ext',
    'config',
    'docs',
    'examples',
    'scripts',
    'templates',
    'tests',
    'pyproject.toml',
    'README.md',
    'run.sh'
)

foreach ($item in $includes) {
    if (-not (Test-Path $item)) {
        Write-Error "Missing required item at repo root: $item"
        exit 1
    }
}

$excludes = @(
    '.git',
    '.venv',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.claude',
    '.vscode',
    '.idea',
    '__pycache__',
    '*.pyc',
    '*.pyo',
    '*.egg-info',
    'wheels',
    '.DS_Store',
    'Thumbs.db',
    'Auto_ext_bundle.tar.gz'
)

$excludeArgs = @()
foreach ($pat in $excludes) {
    $excludeArgs += '--exclude'
    $excludeArgs += $pat
}

Write-Host "Packing Auto_ext working tree..."
Write-Host "  Source: $repoRoot"
Write-Host "  Output: $bundlePath"
Write-Host ""

if (Test-Path $bundlePath) {
    Remove-Item $bundlePath -Force
}

# Resolve tar.exe robustly. Windows 10 1803+ / Windows 11 ship bsdtar at
# %WINDIR%\System32\tar.exe, but PATH may not include System32 in some
# shells (e.g. when a venv activate script clobbers PATH ordering). Fall
# back to PATH lookup if the canonical path is missing.
$tarExe = Join-Path $env:WINDIR 'System32\tar.exe'
if (-not (Test-Path $tarExe)) {
    $tarCmd = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($null -eq $tarCmd) {
        Write-Error @"
tar.exe not found. Windows 10 1803+ and Windows 11 include it at
$env:WINDIR\System32\tar.exe. If you're on an older Windows, install
Git for Windows or 7-Zip and ensure tar.exe is on PATH.
"@
        exit 1
    }
    $tarExe = $tarCmd.Source
}

Write-Host "  tar:    $tarExe"
Write-Host ""

& $tarExe -czf $bundlePath @excludeArgs $includes

if ($LASTEXITCODE -ne 0) {
    Write-Error "tar failed (exit code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

$bundleSize = [math]::Round((Get-Item $bundlePath).Length / 1MB, 2)

Write-Host ""
Write-Host "Bundle written:"
Write-Host "  Path: $bundlePath"
Write-Host "  Size: ${bundleSize} MB"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Transfer $bundleName via the cross-zone tool to the Linux workarea."
Write-Host "  2. On the Linux side, from your Auto_ext_pro/ repo root, run:"
Write-Host "       bash scripts/unpack_in_office.sh /path/to/$bundleName"
