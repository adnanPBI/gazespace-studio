<#
.SYNOPSIS
Build GazeSpace Studio and CLI as separate Windows onedir distributions.
.DESCRIPTION
Runs the software tests, builds the Python wheel, checks a wheel installation,
and packages the native Qt Studio and console CLI. No Windows result is claimed
until this script succeeds on Windows. Signing is optional and uses only the
explicitly supplied existing CurrentUser certificate; no certificate is created.
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', '3.12', '3.11')]
    [string]$PythonVersion = 'auto',
    [switch]$Basler,
    [switch]$Depth,
    [string]$CertificateThumbprint = '',
    [string]$TimestampServer = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows executables must be built on Windows; cross-platform source tests do not create a Windows release.'
}
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $ProjectRoot
$PythonExecutable = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BuildDirectory = Join-Path $ProjectRoot 'build'
$DistributionDirectory = Join-Path $ProjectRoot 'dist'
$PackageDirectory = Join-Path $DistributionDirectory 'python_packages'
$EvidenceDirectory = Join-Path $ProjectRoot 'release_evidence'

function Invoke-Checked {
    param([Parameter(Mandatory=$true)][string]$Executable,
          [Parameter(Mandatory=$true)][string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE : $Executable $($Arguments -join ' ')"
    }
}

# Certificate checks precede the expensive build. Never select an arbitrary
# certificate and never create a self-signed substitute for a publisher identity.
$SigningCertificate = $null
if ($CertificateThumbprint) {
    $Thumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
    if ($Thumbprint -notmatch '^[0-9A-F]{40}$') { throw 'CertificateThumbprint must be a 40-digit SHA-1 certificate-store thumbprint.' }
    $SigningCertificate = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Thumbprint -eq $Thumbprint })
    if ($SigningCertificate.Count -ne 1) { throw 'The exact code-signing certificate with a private key was not found in Cert:\CurrentUser\My.' }
    $SigningCertificate = $SigningCertificate[0]
    if (-not $SigningCertificate.HasPrivateKey -or $SigningCertificate.NotAfter -le (Get-Date) -or $SigningCertificate.NotBefore -gt (Get-Date)) {
        throw 'The selected code-signing certificate is expired, not yet valid or has no accessible private key.'
    }
    if ($TimestampServer -and $TimestampServer -notmatch '^http://') {
        throw 'Windows PowerShell Authenticode timestamping requires an http:// timestamp server URL. HTTPS is not reliably supported by its underlying API.'
    }
} elseif ($TimestampServer) {
    throw 'TimestampServer requires an explicit CertificateThumbprint.'
}

& (Join-Path $PSScriptRoot 'setup_windows.ps1') -PythonVersion $PythonVersion -Basler:$Basler -Depth:$Depth -NoLaunch
if (-not $?) { throw 'Windows environment setup failed' }
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pip', 'install', 'build', 'pyinstaller>=6.9,<7')
New-Item -ItemType Directory -Path $BuildDirectory, $DistributionDirectory, $PackageDirectory, $EvidenceDirectory -Force | Out-Null
& (Join-Path $PSScriptRoot 'verify_windows.ps1') -BaslerEmulator:$Basler -DepthSdk:$Depth
if (-not $?) { throw 'Windows software verification failed' }
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'build', '--wheel', '--sdist', '--outdir', $PackageDirectory)
$Version = (& $PythonExecutable -c 'from gazespace import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+([a-zA-Z0-9.+-]*)?$') { throw 'Unable to resolve the package version' }
$Wheels = @(Get-ChildItem -LiteralPath $PackageDirectory -Filter "gazespace-$Version-*.whl")
if ($Wheels.Count -ne 1) { throw 'Expected exactly one wheel for the current version' }
$WheelCheckDirectory = Join-Path $BuildDirectory 'wheel_install_check'
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pip', 'install', '--no-deps', '--upgrade', '--target', $WheelCheckDirectory, $Wheels[0].FullName)
$PriorWheelCheck = $env:GAZESPACE_WHEEL_CHECK
try {
    $env:GAZESPACE_WHEEL_CHECK = $WheelCheckDirectory
    $WheelCheckCode = "import os,sys,pathlib; root=pathlib.Path(os.environ['GAZESPACE_WHEEL_CHECK']); sys.path.insert(0,str(root)); import gazespace; assert pathlib.Path(gazespace.__file__).is_relative_to(root); import gazespace.desktop,gazespace.ui.main_window,gazespace.engine; print('Installed wheel imports passed:',gazespace.__version__)"
    Invoke-Checked -Executable $PythonExecutable -Arguments @('-c', $WheelCheckCode)
} finally { $env:GAZESPACE_WHEEL_CHECK = $PriorWheelCheck }

$CommonArguments = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--noupx',
    '--paths', (Join-Path $ProjectRoot 'src'), '--paths', $PSScriptRoot, '--distpath', $DistributionDirectory,
    '--specpath', $BuildDirectory, '--collect-all', 'mediapipe', '--collect-all', 'cv2',
    '--collect-all', 'jax', '--collect-all', 'jaxlib',
    '--collect-all', 'fastapi', '--collect-all', 'uvicorn', '--copy-metadata', 'gazespace',
    '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.auto',
    '--hidden-import', 'uvicorn.protocols.http.auto', '--hidden-import', 'uvicorn.protocols.websockets.auto',
    '--hidden-import', 'uvicorn.lifespan.on', '--hidden-import', 'starlette.websockets')
if ($Basler) { $CommonArguments += @('--collect-all', 'pypylon') }
else { $CommonArguments += @('--exclude-module', 'pypylon') }
if ($Depth) { $CommonArguments += @('--collect-all', 'pyrealsense2') }
else { $CommonArguments += @('--exclude-module', 'pyrealsense2') }
# PyInstaller's built-in PySide6 hooks collect the Qt modules/plugins actually
# imported by this program. Collecting every Qt addon would greatly enlarge it.
Invoke-Checked -Executable $PythonExecutable -Arguments ($CommonArguments + @('--workpath', (Join-Path $BuildDirectory 'studio'), '--windowed', '--name', 'GazeSpaceStudio', (Join-Path $PSScriptRoot 'gazespace_studio_entry.py')))
Invoke-Checked -Executable $PythonExecutable -Arguments ($CommonArguments + @('--workpath', (Join-Path $BuildDirectory 'cli'), '--console', '--name', 'GazeSpaceCLI', (Join-Path $PSScriptRoot 'gazespace_entry.py')))
$Executables = @((Join-Path $DistributionDirectory 'GazeSpaceStudio\GazeSpaceStudio.exe'),
                (Join-Path $DistributionDirectory 'GazeSpaceCLI\GazeSpaceCLI.exe'))
$SignatureResults = @()
foreach ($Executable in $Executables) {
    if (-not (Test-Path -LiteralPath $Executable)) { throw "Expected output missing: $Executable" }
    $AppDirectory = Split-Path -Parent $Executable
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md'), (Join-Path $ProjectRoot 'LICENSE') -Destination $AppDirectory -Force
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs') -Destination (Join-Path $AppDirectory 'docs') -Recurse -Force
    $AcceptanceScripts = Join-Path $AppDirectory 'scripts'
    New-Item -ItemType Directory -Path $AcceptanceScripts -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'collect_windows_acceptance.ps1') -Destination $AcceptanceScripts -Force
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'examples')) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot 'examples') -Destination (Join-Path $AppDirectory 'examples') -Recurse -Force
    }
    if ($null -ne $SigningCertificate) {
        $SignParameters = @{ LiteralPath=$Executable; Certificate=$SigningCertificate; HashAlgorithm='SHA256' }
        if ($TimestampServer) { $SignParameters.TimestampServer = $TimestampServer }
        $Signature = Set-AuthenticodeSignature @SignParameters
        if ($Signature.Status -ne 'Valid') { throw "Signing failed for $Executable : $($Signature.StatusMessage)" }
        $VerifiedSignature = Get-AuthenticodeSignature -LiteralPath $Executable
        if ($VerifiedSignature.Status -ne 'Valid' -or $VerifiedSignature.SignerCertificate.Thumbprint -ne $SigningCertificate.Thumbprint) {
            throw "Signature verification failed for $Executable"
        }
        if ($TimestampServer -and $null -eq $VerifiedSignature.TimeStamperCertificate) { throw "Timestamp verification failed for $Executable" }
        $SignatureResults += @{file=(Split-Path -Leaf $Executable);status='Valid';thumbprint=$SigningCertificate.Thumbprint;timestamped=($null -ne $VerifiedSignature.TimeStamperCertificate)}
    } else {
        $SignatureResults += @{file=(Split-Path -Leaf $Executable);status='Unsigned developer build'}
    }
}
# Frozen CLI smoke includes model calibration, real generated-image detection,
# recording, replay/report export and video writing, not only a --help check.
& (Join-Path $PSScriptRoot 'verify_windows.ps1') -SkipTests -PackagedCli $Executables[1] -PackagedStudio $Executables[0] -BaslerEmulator:$Basler -DepthSdk:$Depth
if (-not $?) { throw 'Packaged CLI or Studio smoke verification failed' }
$SigningLabel = if ($null -ne $SigningCertificate) { 'signed' } else { 'unsigned' }
$Archive = Join-Path $DistributionDirectory "GazeSpace-Windows-$Version-$SigningLabel.zip"
Compress-Archive -LiteralPath (Join-Path $DistributionDirectory 'GazeSpaceStudio'), (Join-Path $DistributionDirectory 'GazeSpaceCLI') -DestinationPath $Archive -CompressionLevel Optimal -Force
$Receipt = [ordered]@{
    status='built_and_software_verified_on_windows'
    version=$Version
    built_at_utc=[DateTime]::UtcNow.ToString('o')
    distribution='Two independent onedir folders; keep each executable with its _internal folder'
    studio='GazeSpaceStudio/GazeSpaceStudio.exe'
    cli='GazeSpaceCLI/GazeSpaceCLI.exe'
    basler_sdk_included=[bool]$Basler
    realsense_sdk_included=[bool]$Depth
    signatures=$SignatureResults
    archive=(Split-Path -Leaf $Archive)
    sha256=(Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
    physical_camera_acceptance='Not established by this build'
    manual_desktop_acceptance='Launch Studio and verify actual Windows monitor/camera workflows with the operator'
}
$Receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'windows_build.json') -Encoding UTF8
Write-Host "Created $Archive"
Write-Host "SHA256: $($Receipt.sha256)"
Write-Host "Studio: $($Executables[0])"
Write-Host "CLI: $($Executables[1])"
if ($null -eq $SigningCertificate) { Write-Host 'These are unsigned developer executables. No publisher identity was invented.' }
