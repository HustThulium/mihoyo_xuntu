param([string]$ArchiveDirectory = (Join-Path $PSScriptRoot 'dist/release'))

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot '.cache/pyinstaller'
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements-lock.txt first. See README.md.'
}
& $projectPython -c 'from cv2.version import headless; raise SystemExit(0 if headless else 1)'
if ($LASTEXITCODE -ne 0) {
    throw 'This release requires opencv-python-headless. See README.md for upgrading an existing .venv.'
}
$version = & $projectPython -c 'from map_overlay import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Could not read the release version from map_overlay.'
}
$packageDir = Join-Path $PSScriptRoot 'dist/RouteOverlay'
$settingsPath = Join-Path $packageDir 'settings.json'
$settingsBackup = $null
if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
    $backupDir = Join-Path $PSScriptRoot '.cache'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $settingsBackup = Join-Path $backupDir ([IO.Path]::GetRandomFileName() + '.settings.json')
    [IO.File]::Copy($settingsPath, $settingsBackup)
}
try {
    & $projectPython -m PyInstaller --noconfirm 'packaging/route_overlay.spec'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally {
    if ($settingsBackup) {
        # COLLECT replaces the output folder. Preserve the local configuration across builds.
        if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
            New-Item -ItemType Directory -Path $packageDir -Force | Out-Null
            [IO.File]::Copy($settingsBackup, $settingsPath)
        }
        Remove-Item -LiteralPath $settingsBackup
    }
}
Copy-Item -LiteralPath 'README.md', 'packaging/RELEASE_NOTES.md', 'THIRD_PARTY_NOTICES.md' -Destination $packageDir
$licenseRoot = Join-Path $PSScriptRoot 'dist/RouteOverlay/THIRD_PARTY_LICENSES'
New-Item -ItemType Directory -Path $licenseRoot -Force | Out-Null
Get-ChildItem -LiteralPath 'packaging/licenses' -File | Copy-Item -Destination $licenseRoot
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
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the build interpreter.' }
$pythonLicense = Join-Path $pythonBase 'LICENSE.txt'
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw 'The build interpreter must include LICENSE.txt.'
}
Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenseRoot 'Python-LICENSE.txt') -Force

# Archive only release files. The local installation can contain personal settings.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$releaseDir = (New-Item -ItemType Directory -Path $ArchiveDirectory -Force).FullName
$zipName = "RouteOverlay-v$version-windows-x64.zip"
$zipPath = Join-Path $releaseDir $zipName
$temporaryZip = Join-Path $releaseDir ([IO.Path]::GetRandomFileName())
$packageFiles = 'RouteOverlay.exe', '_internal', 'README.md', 'RELEASE_NOTES.md', 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_LICENSES' |
    ForEach-Object { Get-ChildItem -LiteralPath (Join-Path $packageDir $_) -File -Recurse -Force }
try {
    $archive = [IO.Compression.ZipFile]::Open($temporaryZip, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $packageFiles) {
            $relativePath = $file.FullName.Substring($packageDir.Length + 1).Replace('\', '/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive, $file.FullName, "RouteOverlay/$relativePath", [IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    } finally {
        $archive.Dispose()
    }
    Move-Item -LiteralPath $temporaryZip -Destination $zipPath -Force
} finally {
    if (Test-Path -LiteralPath $temporaryZip -PathType Leaf) {
        Remove-Item -LiteralPath $temporaryZip
    }
}
$sha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$zipPath.sha256" -Value "$sha256 *$zipName" -Encoding Ascii
Write-Output 'Built: dist/RouteOverlay/RouteOverlay.exe'
Write-Output "Release: $zipPath"
