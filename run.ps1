$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$environmentPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $environmentPython)) {
    $environmentPython = Join-Path $PSScriptRoot '.venv-camera\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $environmentPython)) {
    throw 'Run setup.ps1 first to install the camera application environment.'
}
& $environmentPython -m twodviewer --connect
exit $LASTEXITCODE
