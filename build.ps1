$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.cache/pyinstaller'
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements-lock.txt first. See README.md.'
}
& $projectPython -m PyInstaller --noconfirm 'packaging/route_overlay.spec'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Copy-Item -LiteralPath 'README.md', 'THIRD_PARTY_NOTICES.md' -Destination 'dist/RouteOverlay'
$licenseRoot = Join-Path $PSScriptRoot 'dist/RouteOverlay/THIRD_PARTY_LICENSES'
New-Item -ItemType Directory -Path $licenseRoot -Force | Out-Null
Get-ChildItem -LiteralPath '.venv/Lib/site-packages' -Directory -Filter '*.dist-info' | ForEach-Object {
    $destination = Join-Path $licenseRoot $_.Name
    $licenseDir = Join-Path $_.FullName 'licenses'
    if (Test-Path -LiteralPath $licenseDir) {
        Copy-Item -LiteralPath $licenseDir -Destination $destination -Recurse -Force
    } else {
        $licenseFiles = Get-ChildItem -LiteralPath $_.FullName -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE)' }
        if ($licenseFiles) {
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
            $licenseFiles | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $destination -Force }
        }
    }
}
$pythonBase = & $projectPython -c 'import sys; print(sys.base_prefix)'
$pythonLicense = Join-Path $pythonBase 'LICENSE.txt'
if (Test-Path -LiteralPath $pythonLicense) {
    Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenseRoot 'Python-LICENSE.txt') -Force
}
Write-Output 'Built: dist/RouteOverlay/RouteOverlay.exe'
