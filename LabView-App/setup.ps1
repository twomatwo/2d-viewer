param(
    [string]$Python = '',
    [string]$PySpinWheel = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $PySpinWheel) {
    $PySpinWheel = Join-Path $PSScriptRoot 'install\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl'
}
$wheelPath = (Resolve-Path -LiteralPath $PySpinWheel).Path
$environmentPath = Join-Path $PSScriptRoot '.venv'
$environmentPython = Join-Path $environmentPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $environmentPython)) {
    if ($Python) { & $Python -m venv $environmentPath }
    else { & py -3.12 -m venv $environmentPath }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the environment. Check the Python executable.' }
}
if ($PySpinWheel) {
    $pythonTag = & $environmentPython -c "import sys; print('cp%d%d' % sys.version_info[:2])"
    if ([IO.Path]::GetFileName($wheelPath) -notmatch "-$pythonTag-$pythonTag-win_amd64\.whl$") {
        throw "PySpin wheel does not match $pythonTag. Use the Python version named in the wheel."
    }
}
& $environmentPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if ($PySpinWheel) {
    & $environmentPython -m pip install $wheelPath
    if ($LASTEXITCODE -ne 0) { throw 'PySpin installation failed. Match SDK, Python and x64 architecture.' }
}
Write-Host 'Ready. Double-click Start 2D Viewer.cmd.'
Write-Host 'This environment uses:'
& $environmentPython -c 'import sys; print(sys.executable); print(sys.version)'
