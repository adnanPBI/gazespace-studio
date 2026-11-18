<#
.SYNOPSIS
Collect Windows environment/release evidence and optionally measure one camera.
.DESCRIPTION
Runs locally without administrator rights. Inventory does not acquire cameras.
Select CameraBackend explicitly for a bounded capture soak. The soak stores
scalar timing/image-quality statistics, never camera images or video. Physical
device confirmation is an operator statement and never overrides emulator flags.
This collector does not establish person-specific gaze accuracy or calibration.
#>
[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [string]$PythonExecutable = '',
    [string]$PackagedCli = '',
    [string]$PackagedStudio = '',
    [string]$ReleaseArchive = '',
    [ValidateSet('none', 'auto', 'dshow', 'msmf', 'pylon', 'realsense')]
    [string]$CameraBackend = 'none',
    [string]$CameraSource = '0',
    [ValidateRange(1, 300)][int]$SoakSeconds = 30,
    [ValidateRange(1, 8192)][int]$Width = 1280,
    [ValidateRange(1, 8192)][int]$Height = 720,
    [ValidateRange(1, 1000)][int]$Fps = 30,
    [Nullable[double]]$ExposureNative = $null,
    [Nullable[double]]$ExposureUs = $null,
    [Nullable[double]]$Gain = $null,
    [ValidateSet(0, 8, 10, 12, 14, 16)][int]$IrBitDepth = 0,
    [string]$PixelFormat = '',
    [string]$Fourcc = '',
    [Nullable[double]]$MinimumFps = $null,
    [Nullable[double]]$MaximumGapMs = $null,
    [ValidateRange(0, 100000)][int]$MaxReadFailures = 0,
    [switch]$RequireExactSize,
    [string[]]$RequiredControl = @(),
    [switch]$PhysicalDeviceConfirmed,
    [ValidateSet('none', 'basler', 'realsense', 'all')][string]$SdkInventory = 'all'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This collector must run on the actual Windows target. It does not create Windows evidence from another operating system.'
}
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
# Resolve caller-supplied paths before changing to the source/application root.
$InvocationDirectory = (Get-Location).ProviderPath
foreach ($Name in @('OutputDirectory','PythonExecutable','PackagedCli','PackagedStudio','ReleaseArchive')) {
    $Value = Get-Variable -Name $Name -ValueOnly
    if ($Value -and -not [IO.Path]::IsPathRooted($Value)) {
        Set-Variable -Name $Name -Value ([IO.Path]::GetFullPath((Join-Path $InvocationDirectory $Value)))
    }
}
Set-Location -LiteralPath $ProjectRoot
if (-not $OutputDirectory) {
    $EvidenceRoot = if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'src\gazespace')) {Join-Path $ProjectRoot 'release_evidence'} else {Join-Path $env:LOCALAPPDATA 'GazeSpace\acceptance'}
    $OutputDirectory = Join-Path $EvidenceRoot ('windows_acceptance_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss_fff'))
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if ((Test-Path -LiteralPath $OutputDirectory) -and @(Get-ChildItem -LiteralPath $OutputDirectory -Force).Count) {
    throw 'Choose a new/empty output directory so different acceptance runs are not mixed.'
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
if ($PackagedCli) { $PackagedCli = (Resolve-Path -LiteralPath $PackagedCli).Path }
if ($PackagedStudio) { $PackagedStudio = (Resolve-Path -LiteralPath $PackagedStudio).Path }
if ($ReleaseArchive) { $ReleaseArchive = (Resolve-Path -LiteralPath $ReleaseArchive).Path }
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not $PackagedCli -and -not (Test-Path -LiteralPath $PythonExecutable)) {
    throw 'Run scripts\setup_windows.ps1 -NoLaunch, provide -PythonExecutable, or use the packaged CLI from this release.'
}
if ($CameraBackend -in @('pylon', 'realsense') -and $CameraSource -in @('0', 'first', '')) {
    throw 'Use the actual industrial camera serial. Run inventory-only first to inspect available SDK devices.'
}

function Write-Receipt {
    param([string]$Name, $Value)
    $Text = $Value | ConvertTo-Json -Depth 25
    [IO.File]::WriteAllText((Join-Path $OutputDirectory $Name), $Text + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Read-CimInventory {
    param([string]$ClassName, [string[]]$Properties, [scriptblock]$RecordFilter = { $true })
    try {
        $Items = @(Get-CimInstance -ClassName $ClassName -OperationTimeoutSec 10 | Where-Object $RecordFilter | Select-Object -Property $Properties)
        return @{status='collected'; records=$Items}
    } catch { return @{status='unavailable'; error=$_.Exception.Message; records=@()} }
}

function Quote-ProcessArgument {
    param([string]$Value)
    # Windows filenames cannot contain quotes. These controlled command
    # arguments are scalar options/paths; reject quotes rather than interpreting
    # shell syntax. Double a trailing backslash before the closing quote.
    if ($Value.Contains('"')) { throw 'A command argument contains an unsupported double quote' }
    $Trailing = [regex]::Match($Value, '\\+$').Value
    return '"' + $Value + $Trailing + '"'
}

function Invoke-BoundedProcess {
    param([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds)
    $Stdout = Join-Path $OutputDirectory ($Name + '.stdout.log')
    $Stderr = Join-Path $OutputDirectory ($Name + '.stderr.log')
    $Started = [DateTime]::UtcNow
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Process = $null
    try {
        $NativeArguments = ($Arguments | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join ' '
        $Process = Start-Process -FilePath $Executable -ArgumentList $NativeArguments -WorkingDirectory $ProjectRoot -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
        [void]$Process.Handle
        $Completed = $Process.WaitForExit($TimeoutSeconds * 1000)
        if (-not $Completed) {
            # Only this collector's newly created process tree is terminated.
            # Camera acquisition has its own inner watchdog and partial receipt.
            try {
                & (Join-Path $env:WINDIR 'System32\taskkill.exe') /PID $Process.Id /T /F 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0 -and -not $Process.HasExited) { Stop-Process -Id $Process.Id -Force }
            } catch { if (-not $Process.HasExited) { Stop-Process -Id $Process.Id -Force } }
            return @{status='timeout'; timed_out=$true; exit_code=$null; duration_s=$Watch.Elapsed.TotalSeconds; started_at_utc=$Started.ToString('o'); executable=$Executable; arguments=$Arguments}
        }
        $Process.Refresh()
        return @{status=$(if ($Process.ExitCode -eq 0) {'passed'} else {'failed'}); timed_out=$false; exit_code=$Process.ExitCode; duration_s=$Watch.Elapsed.TotalSeconds; started_at_utc=$Started.ToString('o'); executable=$Executable; arguments=$Arguments}
    } catch {
        return @{status='launch_failed'; timed_out=$false; exit_code=$null; error=$_.Exception.Message; duration_s=$Watch.Elapsed.TotalSeconds; executable=$Executable; arguments=$Arguments}
    } finally { $Watch.Stop(); if ($null -ne $Process) { $Process.Dispose() } }
}

function Invariant-Number {
    param([double]$Value)
    return $Value.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
}

$Summary = [ordered]@{
    schema_version=1; status='collecting'; started_at_utc=[DateTime]::UtcNow.ToString('o')
    computer_name=$env:COMPUTERNAME; session_name=$env:SESSIONNAME
    windows_executed=$true; physical_hardware_tested=$false
    camera_acquisition_requested=($CameraBackend -ne 'none'); camera_images_saved=$false
    real_person_gaze_accuracy_established=$false; source_integration_acceptance_established=$false
    steps=[ordered]@{}; limitations=@()
}
Write-Receipt 'acceptance_summary.json' $Summary

$Inventory = [ordered]@{
    collected_at_utc=[DateTime]::UtcNow.ToString('o')
    operating_system=(Read-CimInventory 'Win32_OperatingSystem' @('Caption','Version','BuildNumber','OSArchitecture','LastBootUpTime'))
    processors=(Read-CimInventory 'Win32_Processor' @('Name','Manufacturer','NumberOfCores','NumberOfLogicalProcessors','MaxClockSpeed'))
    video_controllers=(Read-CimInventory 'Win32_VideoController' @('Name','DriverVersion','DriverDate','VideoProcessor','CurrentHorizontalResolution','CurrentVerticalResolution','CurrentRefreshRate'))
    usb_controllers=(Read-CimInventory 'Win32_USBController' @('Name','Manufacturer','PNPDeviceID','Status'))
    cameras=(Read-CimInventory 'Win32_PnPEntity' @('Name','Manufacturer','PNPClass','PNPDeviceID','Status','ConfigManagerErrorCode') { $_.PNPClass -in @('Camera','Image') -or $_.Name -match 'Camera|Basler|RealSense|pylon' })
    camera_and_usb_drivers=(Read-CimInventory 'Win32_PnPSignedDriver' @('DeviceName','DeviceClass','Manufacturer','DriverProviderName','DriverVersion','DriverDate','IsSigned','InfName','DeviceID') { $_.DeviceClass -in @('CAMERA','IMAGE','USB') -or $_.DeviceName -match 'Camera|Basler|RealSense|pylon' })
    inventory_only=$true; camera_acquisition_attempted=$false
}
Write-Receipt 'windows_hardware_inventory.json' $Inventory
$Summary.steps.hardware_inventory = 'collected_with_per_query_status'

$Artifacts = @()
foreach ($Candidate in @($ReleaseArchive, $PackagedCli, $PackagedStudio)) {
    if ($Candidate) {
        $Item = Get-Item -LiteralPath $Candidate
        $Record = [ordered]@{file=$Item.Name; full_path=$Item.FullName; bytes=$Item.Length; sha256=(Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256).Hash}
        if ($Item.Extension -ieq '.exe') {
            $Signature = Get-AuthenticodeSignature -LiteralPath $Item.FullName
            $Record.signature_status = [string]$Signature.Status
            $Record.signer_thumbprint = if ($null -ne $Signature.SignerCertificate) {$Signature.SignerCertificate.Thumbprint} else {$null}
        }
        $Artifacts += $Record
    }
}
$SourceHashes = @()
if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'src\gazespace')) {
    $SourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'src\gazespace') -Filter '*.py' -File -Recurse)
    $SourceFiles += @(Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object { $_.Extension -in @('.py','.ps1','.bat') })
    $SourceFiles += Get-Item -LiteralPath (Join-Path $ProjectRoot 'pyproject.toml')
    foreach ($Item in ($SourceFiles | Sort-Object FullName)) {
        $SourceHashes += @{file=$Item.FullName.Substring($ProjectRoot.Length + 1).Replace('\','/');bytes=$Item.Length;sha256=(Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256).Hash}
    }
}
Write-Receipt 'release_identity.json' @{
    artifacts=$Artifacts; source_files=$SourceHashes
    complete_release_archive_hashed=[bool]$ReleaseArchive
    limitation='An EXE hash alone does not identify adjacent DLLs/data; provide -ReleaseArchive to identify the complete distributed ZIP. Source hashes identify the tested local code but do not prove it matches another application.'
}
$Summary.steps.release_identity = 'recorded'

if ($PackagedCli) {
    $ProbeExecutable = $PackagedCli
    $ProbeArguments = @('environment-probe','--output',(Join-Path $OutputDirectory 'environment_probe.json'),'--sdk-inventory',$SdkInventory)
} else {
    $ProbeExecutable = $PythonExecutable
    $ProbeArguments = @((Join-Path $PSScriptRoot 'environment_acceptance.py'),'--output',(Join-Path $OutputDirectory 'environment_probe.json'),'--sdk-inventory',$SdkInventory)
}
$Summary.steps.environment_probe = Invoke-BoundedProcess 'environment_probe' $ProbeExecutable $ProbeArguments 90
if ($PackagedCli) {
    $Summary.steps.packaged_cli_doctor = Invoke-BoundedProcess 'packaged_cli_doctor' $PackagedCli @('doctor') 30
}
if ($PackagedStudio) {
    $Summary.steps.packaged_studio = Invoke-BoundedProcess 'packaged_studio' $PackagedStudio @('--smoke-test',(Join-Path $OutputDirectory 'packaged_studio.json')) 60
}
Write-Receipt 'acceptance_summary.json' $Summary

if ($CameraBackend -ne 'none') {
    $SoakArguments = @('--backend',$CameraBackend,'--source',$CameraSource,'--seconds',[string]$SoakSeconds,
        '--width',[string]$Width,'--height',[string]$Height,'--fps',[string]$Fps,
        '--max-read-failures',[string]$MaxReadFailures,'--output',(Join-Path $OutputDirectory 'camera_soak.json'))
    foreach ($Option in @(@('exposure-native',$ExposureNative),@('exposure-us',$ExposureUs),@('gain',$Gain),@('minimum-fps',$MinimumFps),@('maximum-gap-ms',$MaximumGapMs))) {
        if ($null -ne $Option[1]) { $SoakArguments += @('--' + $Option[0], (Invariant-Number $Option[1])) }
    }
    if ($IrBitDepth) { $SoakArguments += @('--ir-bit-depth',[string]$IrBitDepth) }
    if ($PixelFormat) { $SoakArguments += @('--pixel-format',$PixelFormat) }
    if ($Fourcc) { $SoakArguments += @('--fourcc',$Fourcc) }
    if ($RequireExactSize) { $SoakArguments += '--require-exact-size' }
    if ($PhysicalDeviceConfirmed) { $SoakArguments += '--physical-device-confirmed' }
    foreach ($Control in $RequiredControl) { $SoakArguments += @('--required-control',$Control) }
    if ($PackagedCli) { $SoakExecutable=$PackagedCli; $SoakArguments=@('camera-soak') + $SoakArguments }
    else { $SoakExecutable=$PythonExecutable; $SoakArguments=@((Join-Path $PSScriptRoot 'camera_acceptance.py')) + $SoakArguments }
    $Summary.steps.camera_soak = Invoke-BoundedProcess 'camera_soak' $SoakExecutable $SoakArguments ($SoakSeconds + 50)
    $CameraReceipt = Join-Path $OutputDirectory 'camera_soak.json'
    if (Test-Path -LiteralPath $CameraReceipt) {
        $CameraResult = Get-Content -LiteralPath $CameraReceipt -Raw | ConvertFrom-Json
        $Summary.physical_hardware_tested = [bool]$CameraResult.physical_hardware_tested
        $Summary.camera_capture_status = $CameraResult.status
    }
}
$Failed = @($Summary.steps.Values | Where-Object { $_ -is [hashtable] -and $_.ContainsKey('status') -and $_.status -ne 'passed' })
$Summary.status = if ($Failed.Count) {'collection_completed_with_failures'} elseif ($CameraBackend -eq 'none') {'environment_collected_camera_not_requested'} else {'capture_checks_completed_gaze_acceptance_pending'}
$Summary.completed_at_utc = [DateTime]::UtcNow.ToString('o')
$Summary.limitations = @(
    'Camera capture statistics do not establish eye/pupil/gaze accuracy or calibration quality.'
    'Physical-device confirmation is supplied by the operator; simulated/emulator metadata still prevents a physical-device claim.'
    'Host arrival timing does not establish sensor frame loss, hardware synchronization or exposure-to-display latency.'
    'Qt display/DPI values describe the active local/remote/virtual session; real-monitor target placement still requires operator validation.'
    'Client-code integration, independent human fixation targets, measured workspace geometry and signing identity require their actual inputs.'
)
Write-Receipt 'acceptance_summary.json' $Summary
$ReceiptArchive = $OutputDirectory + '.zip'
Compress-Archive -LiteralPath $OutputDirectory -DestinationPath $ReceiptArchive -CompressionLevel Optimal -Force
Write-Host "Acceptance receipt: $(Join-Path $OutputDirectory 'acceptance_summary.json')"
Write-Host "Evidence archive: $ReceiptArchive"
Write-Host "Evidence SHA256: $((Get-FileHash -LiteralPath $ReceiptArchive -Algorithm SHA256).Hash)"
if ($Failed.Count) { exit 2 }
