param(
    [Parameter(Mandatory = $true)][string]$SpinnakerInstaller,
    [string]$Python = '',
    [string]$ISCC = '',
    [switch]$KeepBuild
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$sourceRoot = Join-Path $projectRoot 'LabView-App'
if (-not $Python) { $Python = Join-Path $projectRoot '.venv-camera\Scripts\python.exe' }
if (-not $ISCC) {
    $ISCC = Join-Path $projectRoot '.build-tools\InnoSetup\ISCC.exe'
    if (-not (Test-Path -LiteralPath $ISCC)) {
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($compiler) { $ISCC = $compiler.Source }
    }
}
foreach ($required in @($Python, $ISCC, $SpinnakerInstaller)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "File not found: $required" }
}
$sdk = Get-Item -LiteralPath $SpinnakerInstaller
if ($sdk.Length -lt 200MB -or $sdk.VersionInfo.ProductVersion -ne '4.4.0.246') {
    throw 'Use the full offline Spinnaker 4.4.0.246 x64 installer (not a Package Cache bootstrapper).'
}
$signature = Get-AuthenticodeSignature -LiteralPath $sdk.FullName
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'FLIR') {
    throw 'Spinnaker installer signature validation failed.'
}
$versionMatch = Select-String -LiteralPath (Join-Path $sourceRoot 'labview\__init__.py') -Pattern '^__version__ = "([^"]+)"$'
$appVersion = $versionMatch.Matches[0].Groups[1].Value
if ($appVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Expected a three-part application version.' }
$buildRoot = Join-Path $projectRoot '.package-build'
$releaseRoot = Join-Path $projectRoot 'release'
New-Item -ItemType Directory -Path $buildRoot, $releaseRoot -Force | Out-Null
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot 'cache'
try {
    & $Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $buildRoot 'dist') --workpath (Join-Path $buildRoot 'work') (Join-Path $PSScriptRoot '2DViewer.spec')
    if ($LASTEXITCODE -ne 0) { throw 'Application bundling failed.' }
    $bundleRoot = Join-Path $buildRoot 'dist\2D Viewer'
    $guide = Get-Content -LiteralPath (Join-Path $PSScriptRoot '实验室安装与更新说明.html') -Raw -Encoding UTF8
    $guide = $guide -replace '2D-Viewer-Setup-\d+\.\d+\.\d+-x64\.exe', "2D-Viewer-Setup-$appVersion-x64.exe"
    $guidePath = Join-Path $bundleRoot '实验室安装与更新说明.html'
    [IO.File]::WriteAllText($guidePath, $guide, [Text.UTF8Encoding]::new($false))
    & $ISCC "/DAppVersion=$appVersion" "/DBundleDir=$bundleRoot" "/DSpinnakerInstaller=$($sdk.FullName)" "/DReleaseDir=$releaseRoot" (Join-Path $PSScriptRoot '2DViewer.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    Copy-Item -LiteralPath $guidePath -Destination $releaseRoot
    $installer = Join-Path $releaseRoot "2D-Viewer-Setup-$appVersion-x64.exe"
    $hash = Get-FileHash -LiteralPath $installer -Algorithm SHA256
    "$($hash.Hash)  $([IO.Path]::GetFileName($installer))" |
        Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Encoding ascii
    Write-Host "Created: $installer"
    Write-Host "SHA256: $($hash.Hash)"
} finally {
    if (-not $KeepBuild) {
        $resolvedBuild = (Resolve-Path -LiteralPath $buildRoot).Path
        if ($resolvedBuild -ne (Join-Path $projectRoot '.package-build')) {
            throw "Refusing to remove unexpected build path: $resolvedBuild"
        }
        Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
    }
}
exit 0
