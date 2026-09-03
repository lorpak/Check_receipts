$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot "resources\python"
if (Test-Path -LiteralPath (Join-Path $runtimeDir "python.exe")) {
    Write-Host "Embedded Python is already prepared: $runtimeDir"
    exit 0
}

$launcher = Get-Command py -ErrorAction SilentlyContinue
if (-not $launcher) {
    throw "Python launcher 'py' is required. Install Python 3.13 first."
}

$installedVersion = & $launcher.Source -3.13 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($installedVersion -ne "3.13") {
    throw "Python 3.13 is required to install compatible binary dependencies."
}

$pythonVersion = "3.13.12"
$archiveUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"
$expectedSha256 = "76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0"
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("check-receipts-python-" + [guid]::NewGuid())
$archivePath = "$tempDir.zip"

try {
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath
    $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "Python archive checksum mismatch."
    }

    New-Item -ItemType Directory -Path $tempDir | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $tempDir

    $sitePackages = Join-Path $tempDir "Lib\site-packages"
    New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
    [IO.File]::WriteAllLines(
        (Join-Path $tempDir "python313._pth"),
        @("python313.zip", ".", "Lib\site-packages")
    )

    & $launcher.Source -3.13 -m pip install `
        --disable-pip-version-check `
        --no-compile `
        --requirement (Join-Path $projectRoot "requirements.txt") `
        --target $sitePackages
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install embedded Python dependencies."
    }

    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    Get-ChildItem -LiteralPath $tempDir -Force |
        Move-Item -Destination $runtimeDir
    Write-Host "Embedded Python prepared: $runtimeDir"
}
finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
