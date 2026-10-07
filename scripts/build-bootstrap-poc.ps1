param([string]$UiPython = '', [switch]$WithUv, [switch]$Repack)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (!$UiPython) { $UiPython = Join-Path $projectPath '.venv/Scripts/python.exe' }
$seedPath = Join-Path $projectPath 'build/bootstrap-poc-seed'
New-Item -ItemType Directory -Force $seedPath | Out-Null
$iconPath = Join-Path $seedPath 'KaraMorph.ico'
& $UiPython (Join-Path $PSScriptRoot 'build_icon.py') --output $iconPath
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $compiler "/win32icon:$iconPath" /nologo /target:winexe /platform:x64 /reference:System.Web.Extensions.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$seedPath/KaraMorph.exe" (Join-Path $PSScriptRoot 'BootstrapPoc.cs')
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap compiler failed' }
$arguments = @((Join-Path $PSScriptRoot 'build_bootstrap_poc.py'))
if ($Repack) { $arguments += '--repack' }
if ($WithUv) {
    if (!(Test-Path -LiteralPath (Join-Path $seedPath 'uv.exe'))) { throw 'Copy the verified uv.exe into build/bootstrap-poc-seed before --WithUv' }
    $arguments += '--with-uv'
}
& $UiPython @arguments
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap packaging failed' }
