$ErrorActionPreference = 'Stop'
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw 'Create .venv and install requirements-lock.txt first. See README.md.'
}

Push-Location -LiteralPath $PSScriptRoot
$stage = $null
try {
    $manifestPath = 'packaging/qt-sources.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $qtVersion = $manifest.version
    & $projectPython -c 'import sys, PySide6, shiboken6; from PySide6.QtCore import qVersion; sys.exit(0 if qVersion() == PySide6.__version__ == shiboken6.__version__ == sys.argv[1] else 1)' $qtVersion
    if ($LASTEXITCODE -ne 0) {
        throw "The installed Qt, PySide6 and Shiboken6 must match the source manifest ($qtVersion)."
    }

    $originalPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        & $projectPython -m unittest discover -s tests
        if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }
    } finally {
        $env:QT_QPA_PLATFORM = $originalPlatform
    }

    $cacheDir = (New-Item -ItemType Directory -Path '.cache' -Force).FullName
    $sourceCache = (New-Item -ItemType Directory -Path (Join-Path $cacheDir "qt-$qtVersion-source") -Force).FullName
    $sourceFiles = foreach ($archive in $manifest.archives) {
        $name = [IO.Path]::GetFileName(([uri]$archive.url).AbsolutePath)
        $cached = Join-Path $sourceCache $name
        if (-not (Test-Path -LiteralPath $cached -PathType Leaf) -or (Get-FileHash -LiteralPath $cached).Hash -ne $archive.sha256) {
            $partial = Join-Path $sourceCache ([IO.Path]::GetRandomFileName())
            try {
                Write-Host "Downloading $name"
                Invoke-WebRequest -Uri $archive.url -OutFile $partial -TimeoutSec 180 -UseBasicParsing
                if ((Get-FileHash -LiteralPath $partial).Hash -ne $archive.sha256) {
                    throw "Qt source checksum mismatch: $name"
                }
                Move-Item -LiteralPath $partial -Destination $cached -Force
            } finally {
                if (Test-Path -LiteralPath $partial -PathType Leaf) { Remove-Item -LiteralPath $partial }
            }
        }
        $cached
    }

    $stage = (New-Item -ItemType Directory -Path (Join-Path $cacheDir ([IO.Path]::GetRandomFileName()))).FullName
    $sourceZip = Join-Path $stage "RouteOverlay-Qt-$qtVersion-source.zip"
    & $projectPython -m zipfile -c $sourceZip 'packaging/QT_SOURCES.md' $manifestPath @sourceFiles
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Qt source ZIP.' }

    & (Join-Path $PSScriptRoot 'build.ps1') -ArchiveDirectory $stage
    $runtimeZip = Get-ChildItem -LiteralPath $stage -File -Filter 'RouteOverlay-v*-windows-x64.zip'
    & $projectPython 'tools/check_release.py' $runtimeZip.FullName $sourceZip
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed.' }

    $releaseDir = (New-Item -ItemType Directory -Path 'dist/release' -Force).FullName
    foreach ($file in $sourceZip, $runtimeZip.FullName, "$($runtimeZip.FullName).sha256") {
        Move-Item -LiteralPath $file -Destination $releaseDir -Force
    }
    Write-Output "Verified release files: $releaseDir"
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage -PathType Container)) {
        $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
        if ([IO.Path]::GetDirectoryName($resolvedStage) -eq $cacheDir) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
    Pop-Location
}
