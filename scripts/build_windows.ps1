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
    '--collect-data', 'gazespace',
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
    # These acceptance tools ship with each portable application. Their Python
    # scripts require a compatible separate Python environment when used onsite.
    $ClientAcceptanceScripts = @('collect_windows_acceptance.ps1',
                                 'verify_client_signatures.ps1',
                                 'evaluate_client_hil.py',
                                 'verify_host_contract.py')
    foreach ($ScriptName in $ClientAcceptanceScripts) {
        $ScriptPath = Join-Path $PSScriptRoot $ScriptName
        if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
            throw "Required client-acceptance script missing: $ScriptName"
        }
        Copy-Item -LiteralPath $ScriptPath -Destination $AcceptanceScripts -Force
        if (-not (Test-Path -LiteralPath (Join-Path $AcceptanceScripts $ScriptName))) {
            throw "Client-acceptance script failed to package: $ScriptName"
        }
    }
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
# Python's ZIP implementation includes hidden files that Compress-Archive can
# omit. Verify every file's decompressed bytes, not only an archive CRC/listing.
# Split archives must exactly match their folder in the combined payload.
$ArchiveHelper = Join-Path $BuildDirectory 'verify_delivery_archive.py'
$ArchiveHelperCode = @'
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import traceback
import zipfile

def digest_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()

def digest_file(path):
    with path.open('rb') as stream:
        return digest_stream(stream)

parser = argparse.ArgumentParser()
parser.add_argument('--archive', type=Path, required=True)
parser.add_argument('--receipt', type=Path, required=True)
parser.add_argument('--folder', type=Path, action='append', required=True)
parser.add_argument('--max-bytes', type=int, default=0)
parser.add_argument('--reference', type=Path)
args = parser.parse_args()
archive = args.archive.resolve()
temporary = archive.with_name(archive.name + '.partial')
receipt = {'status': 'checking', 'archive': archive.name,
           'archive_path': str(archive), 'max_bytes_exclusive': args.max_bytes or None}
try:
    files, directories, sources = {}, set(), {}
    roots = [folder.resolve(strict=True) for folder in args.folder]
    if len({root.name for root in roots}) != len(roots):
        raise ValueError('Archive folders must have unique top-level names')
    for root in roots:
        if not root.is_dir():
            raise ValueError(f'Payload root is not a directory: {root}')
        for path in [root, *sorted(root.rglob('*'))]:
            name = path.relative_to(root.parent).as_posix()
            if path.is_symlink():
                raise ValueError(f'Unverified symbolic link in Windows payload: {name}')
            if path.is_dir():
                directories.add(name + '/')
            elif path.is_file():
                files[name] = {'bytes': path.stat().st_size, 'sha256': digest_file(path)}
                sources[name] = path
            else:
                raise ValueError(f'Unsupported payload entry: {name}')
    if not files:
        raise ValueError('Application payload contains no files')
    if args.reference:
        reference = json.loads(args.reference.read_text(encoding='utf-8-sig'))
        if reference.get('status') != 'passed':
            raise ValueError('Combined archive reference did not pass verification')
        prefixes = tuple(root.name + '/' for root in roots)
        expected = {name: value for name, value in reference['files'].items()
                    if name.startswith(prefixes)}
        expected_dirs = {name for name in reference['directories'] if name.startswith(prefixes)}
        if files != expected or directories != expected_dirs:
            raise ValueError('Split payload differs from its complete combined-archive folder')
        receipt['combined_inventory_reference'] = args.reference.name
        receipt['matches_combined_payload'] = True
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True, strict_timestamps=False) as output:
        for name in sorted(directories):
            output.writestr(name, b'')
        for name, path in sorted(sources.items()):
            output.write(path, name)
    with zipfile.ZipFile(temporary) as checked:
        names = checked.namelist()
        if len(names) != len(set(names)) or set(names) != set(files) | directories:
            raise ValueError('Archive payload inventory has omissions, additions or duplicates')
        for name, expected in files.items():
            if checked.getinfo(name).file_size != expected['bytes']:
                raise ValueError(f'Archive payload size differs: {name}')
            with checked.open(name) as stream:
                if digest_stream(stream) != expected['sha256']:
                    raise ValueError(f'Archive payload bytes differ: {name}')
    size = temporary.stat().st_size
    if args.max_bytes and size >= args.max_bytes:
        raise ValueError(f'Delivery archive must be below {args.max_bytes} bytes; got {size}')
    receipt.update(status='passed', bytes=size, sha256=digest_file(temporary),
                   payload_verified=True, file_count=len(files),
                   payload_bytes=sum(value['bytes'] for value in files.values()),
                   files=files, directories=sorted(directories),
                   verified_at_utc=datetime.now(timezone.utc).isoformat())
    os.replace(temporary, archive)
except Exception as exc:
    receipt.update(status='failed', error=f'{type(exc).__name__}: {exc}',
                   traceback=traceback.format_exc())
    raise
finally:
    temporary.unlink(missing_ok=True)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
print(f"Verified {archive.name}: {receipt['file_count']} files, {receipt['bytes']} bytes, SHA256 {receipt['sha256']}")
'@
[IO.File]::WriteAllText($ArchiveHelper, $ArchiveHelperCode, [Text.UTF8Encoding]::new($false))

function New-VerifiedDeliveryArchive {
    param([string]$Application, [string]$ArchivePath, [string[]]$Folders,
          [long]$LimitBytes = 0, [string]$ReferenceManifest = '')
    $InventoryPath = Join-Path $EvidenceDirectory "windows_archive_inventory_$Application.json"
    $ArchiveArguments = @($ArchiveHelper, '--archive', $ArchivePath, '--receipt', $InventoryPath,
                          '--max-bytes', [string]$LimitBytes)
    foreach ($Folder in $Folders) { $ArchiveArguments += @('--folder', $Folder) }
    if ($ReferenceManifest) { $ArchiveArguments += @('--reference', $ReferenceManifest) }
    Invoke-Checked -Executable $PythonExecutable -Arguments $ArchiveArguments | Out-Host
    $Inventory = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
    if ($Inventory.status -ne 'passed' -or -not $Inventory.payload_verified) {
        throw "Archive payload verification failed: $ArchivePath"
    }
    return $Inventory
}

$PackagedCollector = Join-Path (Split-Path -Parent $Executables[1]) 'scripts\collect_windows_acceptance.ps1'
$CollectorSdkInventory = if ($Basler -and $Depth) {'all'} elseif ($Basler) {'basler'} elseif ($Depth) {'realsense'} else {'none'}

function Invoke-DeliveryCollector {
    param([string]$Application, $Inventory)
    # Use the already verified frozen executables and the collector shipped in
    # their folder for EACH exact ZIP. No camera acquisition is requested.
    $AcceptanceDirectory = Join-Path $EvidenceDirectory ('windows_release_acceptance_' + $Application + '_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss_fff'))
    $CollectorSucceeded = $false
    try {
        & $PackagedCollector -OutputDirectory $AcceptanceDirectory -PackagedCli $Executables[1] -PackagedStudio $Executables[0] -ReleaseArchive $Inventory.archive_path -CameraBackend none -SdkInventory $CollectorSdkInventory
        $CollectorSucceeded = $?
    } finally { Set-Location -LiteralPath $ProjectRoot }
    if (-not $CollectorSucceeded) { throw "Packaged acceptance collector failed for $Application" }
    $Summary = Get-Content -LiteralPath (Join-Path $AcceptanceDirectory 'acceptance_summary.json') -Raw | ConvertFrom-Json
    if ($Summary.status -ne 'environment_collected_camera_not_requested' -or -not $Summary.windows_executed) {
        throw "The full collector did not complete inventory for $Application"
    }
    if ($Summary.camera_acquisition_requested -or $Summary.physical_hardware_tested -or $Summary.real_person_gaze_accuracy_established -or $Summary.source_integration_acceptance_established) {
        throw 'Hosted Windows inventory must not claim physical/human/client acceptance'
    }
    foreach ($StepName in @('environment_probe', 'packaged_cli_doctor', 'packaged_studio')) {
        $Step = $Summary.steps.$StepName
        if ($Step.status -ne 'passed' -or $Step.exit_code -ne 0 -or $Step.timed_out) {
            throw "Collector frozen executable gate failed: $Application / $StepName"
        }
    }
    $Hardware = Get-Content -LiteralPath (Join-Path $AcceptanceDirectory 'windows_hardware_inventory.json') -Raw | ConvertFrom-Json
    if ($Hardware.operating_system.status -ne 'collected' -or @($Hardware.operating_system.records).Count -lt 1) {
        throw 'The collector did not obtain the Windows operating-system CIM baseline'
    }
    $Identity = Get-Content -LiteralPath (Join-Path $AcceptanceDirectory 'release_identity.json') -Raw | ConvertFrom-Json
    $ArchiveIdentity = @($Identity.artifacts | Where-Object { $_.full_path -eq $Inventory.archive_path })
    if (-not $Identity.complete_release_archive_hashed -or $ArchiveIdentity.Count -ne 1 -or $ArchiveIdentity[0].sha256 -ne $Inventory.sha256 -or $ArchiveIdentity[0].bytes -ne $Inventory.bytes) {
        throw "Collector receipt does not identify the exact $Application archive"
    }
    foreach ($Executable in $Executables) {
        $ExecutableIdentity = @($Identity.artifacts | Where-Object { $_.full_path -eq $Executable })
        if ($ExecutableIdentity.Count -ne 1 -or $ExecutableIdentity[0].sha256 -ne (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash) {
            throw "Collector receipt does not identify the tested executable: $Executable"
        }
    }
    $AcceptanceArchive = $AcceptanceDirectory + '.zip'
    if (-not (Test-Path -LiteralPath $AcceptanceArchive)) { throw 'The full collector did not produce its evidence ZIP' }
    return [ordered]@{
        application=$Application; archive=$Inventory.archive; bytes=$Inventory.bytes; sha256=$Inventory.sha256
        payload_verified=$true; payload_file_count=$Inventory.file_count; payload_bytes=$Inventory.payload_bytes
        payload_inventory="windows_archive_inventory_$Application.json"
        full_windows_collector_executed=$true
        full_windows_collector_mode='inventory_only_no_camera_selected'
        full_windows_collector_status=$Summary.status
        full_windows_collector_directory=(Split-Path -Leaf $AcceptanceDirectory)
        full_windows_collector_evidence_archive=(Split-Path -Leaf $AcceptanceArchive)
        full_windows_collector_evidence_bytes=(Get-Item -LiteralPath $AcceptanceArchive).Length
        full_windows_collector_evidence_sha256=(Get-FileHash -LiteralPath $AcceptanceArchive -Algorithm SHA256).Hash
        physical_hardware_tested=$false; real_person_gaze_accuracy_established=$false
    }
}

$Archive = Join-Path $DistributionDirectory "GazeSpace-Windows-$Version-$SigningLabel.zip"
$ApplicationFolders = @((Join-Path $DistributionDirectory 'GazeSpaceStudio'), (Join-Path $DistributionDirectory 'GazeSpaceCLI'))
$CombinedInventory = New-VerifiedDeliveryArchive -Application 'combined' -ArchivePath $Archive -Folders $ApplicationFolders
$CombinedCollector = Invoke-DeliveryCollector -Application 'combined' -Inventory $CombinedInventory
$DeliveryArchives = @()
foreach ($Definition in @(@{application='studio';label='Studio';folder=$ApplicationFolders[0]},
                          @{application='cli';label='CLI';folder=$ApplicationFolders[1]})) {
    $SplitArchive = Join-Path $DistributionDirectory "GazeSpace-$($Definition.label)-Windows-$Version-$SigningLabel.zip"
    $SplitInventory = New-VerifiedDeliveryArchive -Application $Definition.application -ArchivePath $SplitArchive -Folders @($Definition.folder) -LimitBytes 524288000 -ReferenceManifest (Join-Path $EvidenceDirectory 'windows_archive_inventory_combined.json')
    $SplitCollector = Invoke-DeliveryCollector -Application $Definition.application -Inventory $SplitInventory
    $SplitCollector['max_bytes_exclusive']=524288000
    $SplitCollector['matches_combined_payload']=[bool]$SplitInventory.matches_combined_payload
    $DeliveryArchives += $SplitCollector
}

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
    bytes=$CombinedInventory.bytes
    sha256=$CombinedInventory.sha256
    payload_verified=$true
    payload_inventory='windows_archive_inventory_combined.json'
    delivery_archives=$DeliveryArchives
    full_windows_collector_executed=$true
    full_windows_collector_mode='inventory_only_no_camera_selected'
    full_windows_collector_status=$CombinedCollector.full_windows_collector_status
    full_windows_collector_directory=$CombinedCollector.full_windows_collector_directory
    full_windows_collector_evidence_archive=$CombinedCollector.full_windows_collector_evidence_archive
    full_windows_collector_evidence_sha256=$CombinedCollector.full_windows_collector_evidence_sha256
    physical_camera_acceptance='Not established by this build'
    manual_desktop_acceptance='Launch Studio and verify actual Windows monitor/camera workflows with the operator'
}
$Receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'windows_build.json') -Encoding UTF8
Write-Host "Created $Archive"
Write-Host "SHA256: $($Receipt.sha256)"
Write-Host "Studio: $($Executables[0])"
Write-Host "CLI: $($Executables[1])"
if ($null -eq $SigningCertificate) { Write-Host 'These are unsigned developer executables. No publisher identity was invented.' }
