param(
    [string]$UiPython = '',
    [string]$SeparationPython = '',
    [string]$StylingPython = '',
    [string]$AceSource = '',
    [switch]$FinalizeOnly
)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectPath
if (!$UiPython) { $UiPython = Join-Path $projectPath '.venv/Scripts/python.exe' }
function Run-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program" }
}
if (!$FinalizeOnly) {
    $buildArguments = @('scripts/build_portable.py', '--ui-python', $UiPython)
    if ($SeparationPython) { $buildArguments += @('--separation-python', $SeparationPython) }
    if ($StylingPython) { $buildArguments += @('--styling-python', $StylingPython) }
    if ($AceSource) { $buildArguments += @('--ace-source', $AceSource) }
    Run-Checked $UiPython $buildArguments
}
$stagingPath = Join-Path $projectPath 'dist/KaraMorph-0.1.0-preview'
$iconPath = Join-Path $projectPath 'build/KaraMorph.ico'
Run-Checked $UiPython @((Join-Path $PSScriptRoot 'build_icon.py'), '--output', $iconPath)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
Run-Checked $compiler @("/win32icon:$iconPath", '/nologo', '/target:winexe', '/platform:x64', '/reference:System.Windows.Forms.dll',
    "/out:$stagingPath/KaraMorph.exe", (Join-Path $PSScriptRoot 'KaraMorphLauncher.cs'))
Run-Checked (Join-Path $stagingPath '.runtime/ui/python.exe') @((Join-Path $stagingPath 'run_app.py'), '--self-test')
Run-Checked $UiPython @('scripts/review_licenses.py', $stagingPath)
Run-Checked $UiPython @('scripts/build_portable.py', '--finalize')
