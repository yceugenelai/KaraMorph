param([string]$AceAssets = "", [switch]$SkipModels)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectPath
$env:UV_CACHE_DIR = Join-Path $projectPath '.app_data/cache/uv'
$env:UV_CREDENTIALS_DIR = Join-Path $projectPath '.app_data/cache/uv-credentials'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectPath '.runtime/python'
$env:UV_PYTHON_BIN_DIR = Join-Path $projectPath '.runtime/bin'
$env:PIP_CACHE_DIR = Join-Path $projectPath '.app_data/cache/pip'
$env:TEMP = Join-Path $projectPath '.app_data/cache/tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable $Arguments" }
}
$uv = Join-Path $projectPath '.tools/bin/uv.exe'
if (!(Test-Path -LiteralPath $uv)) {
    # Bootstrap with any available Python; the created environments use project-owned Python.
    Invoke-Checked 'python' @('-m', 'pip', 'install', '--target', '.tools', 'uv==0.12.21', '--cache-dir', $env:PIP_CACHE_DIR)
}
Invoke-Checked $uv @('python', 'install', '3.10.21', '3.11.16', '--no-registry', '--no-bin')
$py310 = Join-Path $projectPath '.runtime/python/cpython-3.10.21-windows-x86_64-none/python.exe'
$py311 = Join-Path $projectPath '.runtime/python/cpython-3.11.16-windows-x86_64-none/python.exe'
foreach ($entry in @(@('.venv', $py310), @('.runtime/separation', $py310), @('.runtime/styling', $py311))) {
    $envPath = Join-Path $projectPath $entry[0]
    if (!(Test-Path -LiteralPath (Join-Path $envPath 'Scripts/python.exe'))) {
        Invoke-Checked $uv @('venv', $envPath, '--python', $entry[1])
    }
    Invoke-Checked (Join-Path $envPath 'Scripts/python.exe') @('-c', 'import pathlib,sys; root=pathlib.Path(sys.argv[1]).resolve(); base=pathlib.Path(sys.base_prefix).resolve(); assert root in base.parents, base', '.runtime/python')
}
Invoke-Checked $uv @('pip', 'sync', '--python', '.venv/Scripts/python.exe', 'scripts/requirements-ui-windows.lock.txt')
Invoke-Checked $uv @('pip', 'sync', '--python', '.runtime/separation/Scripts/python.exe', 'scripts/requirements-separation-windows.lock.txt', '--extra-index-url', 'https://download.pytorch.org/whl/cu124', '--index-strategy', 'unsafe-best-match')
Invoke-Checked $uv @('pip', 'sync', '--python', '.runtime/styling/Scripts/python.exe', 'scripts/requirements-styling-windows.lock.txt', '--extra-index-url', 'https://download.pytorch.org/whl/cu128', '--index-strategy', 'unsafe-best-match')
$assetArgs = @('scripts/prepare_assets.py')
if ($SkipModels) { $assetArgs += '--source-only' }
if ($AceAssets) { $assetArgs += @('--ace-assets', $AceAssets) }
Invoke-Checked (Join-Path $projectPath '.venv/Scripts/python.exe') $assetArgs
Write-Host 'Ready. Start UI: .\.venv\Scripts\python.exe run_app.py'
