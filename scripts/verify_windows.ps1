<#
.SYNOPSIS
Run Windows software acceptance tests and a complete synthetic session.
.DESCRIPTION
Produces real local receipts only when this script executes successfully on
Windows. It does not establish camera hardware or human gaze accuracy.
#>
[CmdletBinding()]
param(
    [switch]$BaslerEmulator,
    [switch]$DepthSdk,
    [string]$PackagedCli = '',
    [string]$PackagedStudio = '',
    [switch]$SkipTests
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows verification must run on Windows.'
}
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $ProjectRoot
$PythonExecutable = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw 'Run scripts\setup_windows.ps1 -NoLaunch first.'
}
$EvidenceDirectory = Join-Path $ProjectRoot 'release_evidence'
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null

function Invoke-Checked {
    param([Parameter(Mandatory=$true)][string]$Executable,
          [Parameter(Mandatory=$true)][string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE : $Executable $($Arguments -join ' ')"
    }
}
$PriorQtPlatform = $env:QT_QPA_PLATFORM
$PriorQtApi = $env:QT_API
$PriorEmulatorOptIn = $env:GAZESPACE_TEST_PYLON_EMULATOR
$PriorPylonEmulator = $env:PYLON_CAMEMU
$PriorPytestEvidenceDir = $env:GAZESPACE_PYTEST_EVIDENCE_DIR
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:QT_API = 'pyside6'
    if ($BaslerEmulator) {
        $env:GAZESPACE_TEST_PYLON_EMULATOR = '1'
        $env:PYLON_CAMEMU = '1'
        $env:GAZESPACE_PYTEST_EVIDENCE_DIR = $EvidenceDirectory
    } else {
        Remove-Item Env:GAZESPACE_TEST_PYLON_EMULATOR -ErrorAction SilentlyContinue
        Remove-Item Env:GAZESPACE_PYTEST_EVIDENCE_DIR -ErrorAction SilentlyContinue
    }
    Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pip', 'check')
    if (-not $SkipTests) {
        Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pytest', '-q', '--junitxml', (Join-Path $EvidenceDirectory 'windows_pytest.xml'))
    }
    Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'gazespace', 'doctor')
    Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'gazespace', 'simulate', '--output', (Join-Path $EvidenceDirectory 'windows_simulation'), '--seconds', '2', '--record-video')
    if ($BaslerEmulator) {
        Invoke-Checked -Executable $PythonExecutable -Arguments @((Join-Path $PSScriptRoot 'verify_pypylon_emulator.py'), '--output', (Join-Path $EvidenceDirectory 'pypylon_sdk_emulator_windows.json'))
    }
    if ($DepthSdk -and -not ($SkipTests -and $PackagedCli)) {
        $SourceEnvironmentPath = Join-Path $EvidenceDirectory 'windows_source_environment.json'
        if (Test-Path -LiteralPath $SourceEnvironmentPath) { Remove-Item -LiteralPath $SourceEnvironmentPath -Force }
        Invoke-Checked -Executable $PythonExecutable -Arguments @((Join-Path $PSScriptRoot 'environment_acceptance.py'), '--output', $SourceEnvironmentPath, '--sdk-inventory', 'realsense')
        $SourceEnvironment = Get-Content -LiteralPath $SourceEnvironmentPath -Raw | ConvertFrom-Json
        if ($SourceEnvironment.sdk_inventory.realsense.status -ne 'enumerated') {
            throw "RealSense SDK could not initialize/enumerate devices: $($SourceEnvironment.sdk_inventory.realsense.error)"
        }
    }
    $PackagedChecked = $false
    $PackagedFaceChecked = $false
    if ($PackagedCli) {
        $ResolvedCli = (Resolve-Path -LiteralPath $PackagedCli).Path
        Invoke-Checked -Executable $ResolvedCli -Arguments @('doctor')
        $BundledFaceImage = Join-Path (Split-Path -Parent $ResolvedCli) 'examples\reference_face_nasa.png'
        if (-not (Test-Path -LiteralPath $BundledFaceImage)) { throw 'The frozen CLI distribution is missing its attributed reference-face fixture' }
        $FaceReceiptPath = Join-Path $EvidenceDirectory 'windows_packaged_face_inference.json'
        if (Test-Path -LiteralPath $FaceReceiptPath) { Remove-Item -LiteralPath $FaceReceiptPath -Force }
        Invoke-Checked -Executable $ResolvedCli -Arguments @('face-smoke', '--image', $BundledFaceImage, '--output', $FaceReceiptPath)
        $FaceReceipt = Get-Content -LiteralPath $FaceReceiptPath -Raw | ConvertFrom-Json
        if ($FaceReceipt.status -ne 'passed' -or -not $FaceReceipt.frozen_executable -or -not $FaceReceipt.face_detected -or $FaceReceipt.landmark_count -ne 478 -or $FaceReceipt.iris_count -ne 2 -or -not $FaceReceipt.features_finite) {
            throw 'The frozen MediaPipe facial/iris model failed the real-reference-photo inference gate'
        }
        if ($FaceReceipt.image_sha256 -ne (Get-FileHash -LiteralPath $BundledFaceImage -Algorithm SHA256).Hash) {
            throw 'The frozen face-inference receipt does not identify the bundled reference image'
        }
        if ($FaceReceipt.hardware_validated -or $FaceReceipt.gaze_accuracy_validated -or $FaceReceipt.head_angle_accuracy_validated -or $FaceReceipt.human_gaze_accuracy_measured) {
            throw 'A reference-photo inference check must not claim human gaze, pose accuracy or hardware acceptance'
        }
        $PackagedFaceChecked = $true
        Invoke-Checked -Executable $ResolvedCli -Arguments @('simulate', '--output', (Join-Path $EvidenceDirectory 'windows_packaged_simulation'), '--seconds', '2', '--record-video')
        $EnvironmentReceiptPath = Join-Path $EvidenceDirectory 'windows_packaged_environment.json'
        if (Test-Path -LiteralPath $EnvironmentReceiptPath) { Remove-Item -LiteralPath $EnvironmentReceiptPath -Force }
        $InventorySdk = if ($BaslerEmulator -and $DepthSdk) {'all'} elseif ($BaslerEmulator) {'basler'} elseif ($DepthSdk) {'realsense'} else {'none'}
        Invoke-Checked -Executable $ResolvedCli -Arguments @('environment-probe', '--output', $EnvironmentReceiptPath, '--sdk-inventory', $InventorySdk)
        if ($BaslerEmulator -or $DepthSdk) {
            $EnvironmentReceipt = Get-Content -LiteralPath $EnvironmentReceiptPath -Raw | ConvertFrom-Json
        }
        if ($DepthSdk -and $EnvironmentReceipt.sdk_inventory.realsense.status -ne 'enumerated') {
            throw "The packaged RealSense SDK could not initialize/enumerate devices: $($EnvironmentReceipt.sdk_inventory.realsense.error)"
        }
        if ($BaslerEmulator) {
            if ($EnvironmentReceipt.sdk_inventory.basler.status -ne 'enumerated') {
                throw "The packaged Basler SDK could not enumerate devices: $($EnvironmentReceipt.sdk_inventory.basler.error)"
            }
            $Emulators = @($EnvironmentReceipt.sdk_inventory.basler.devices | Where-Object { $_.sdk_emulator })
            if (-not $Emulators.Count) { throw 'The packaged Basler SDK did not enumerate its camera emulator' }
            $FrozenEmulatorReceipt = Join-Path $EvidenceDirectory 'windows_packaged_sdk_emulator.json'
            if (Test-Path -LiteralPath $FrozenEmulatorReceipt) { Remove-Item -LiteralPath $FrozenEmulatorReceipt -Force }
            Invoke-Checked -Executable $ResolvedCli -Arguments @('camera-soak', '--backend', 'pylon', '--source', [string]$Emulators[0].serial, '--seconds', '2', '--width', '640', '--height', '480', '--fps', '30', '--allow-simulated', '--output', $FrozenEmulatorReceipt)
        }
        $PackagedChecked = $true
    }
    $PackagedStudioChecked = $false
    if ($PackagedStudio) {
        $ResolvedStudio = (Resolve-Path -LiteralPath $PackagedStudio).Path
        $StudioReceiptPath = Join-Path $EvidenceDirectory 'windows_studio_smoke.json'
        if (Test-Path -LiteralPath $StudioReceiptPath) { Remove-Item -LiteralPath $StudioReceiptPath -Force }
        # A windowed executable has no console and must be waited on explicitly.
        $StudioProcess = Start-Process -FilePath $ResolvedStudio -ArgumentList @('--smoke-test', ('"' + $StudioReceiptPath + '"')) -PassThru
        [void]$StudioProcess.Handle
        if (-not $StudioProcess.WaitForExit(60000)) {
            Stop-Process -Id $StudioProcess.Id -Force
            $StudioProcess.Dispose()
            throw 'Packaged Studio exceeded the 60-second smoke-test timeout'
        }
        $StudioProcess.Refresh()
        $StudioExitCode = $StudioProcess.ExitCode
        $StudioProcess.Dispose()
        if ($StudioExitCode -ne 0) { throw "Packaged Studio smoke failed with exit code $StudioExitCode" }
        $StudioReceipt = Get-Content -LiteralPath $StudioReceiptPath -Raw | ConvertFrom-Json
        if ($StudioReceipt.status -ne 'passed' -or -not $StudioReceipt.frozen_executable -or $StudioReceipt.native_ui_pages_painted -lt 1) {
            throw 'Packaged Studio did not return a valid native UI verification receipt'
        }
        if ($StudioReceipt.font_verification.status -ne 'passed' -or $StudioReceipt.font_verification.ascii_glyphs_checked -ne 95 -or $StudioReceipt.font_verification.unicode_glyphs_checked -ne 14 -or @($StudioReceipt.font_verification.missing_glyphs).Count -ne 0 -or $StudioReceipt.font_verification.distinct_probe_rasters -ne 12 -or $StudioReceipt.font_verification.expected_probe_rasters -ne 12) {
            throw 'Packaged Studio text is not verified: required glyphs or distinct rendered character shapes are missing'
        }
        $FontProof = Join-Path $EvidenceDirectory $StudioReceipt.font_verification.raster_probe_png
        if (-not (Test-Path -LiteralPath $FontProof)) { throw 'Packaged Studio did not save its actual Qt glyph rendering proof' }
        $PackagedStudioChecked = $true
    }
    $PythonVersion = (& $PythonExecutable -c 'import platform; print(platform.python_version())').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read Python version' }
    $Receipt = [ordered]@{
        status = 'passed'
        verified_at_utc = [DateTime]::UtcNow.ToString('o')
        operating_system = [Environment]::OSVersion.VersionString
        python = $PythonVersion
        software_tests_executed = -not [bool]$SkipTests
        synthetic_end_to_end_session = 'passed'
        packaged_cli_smoke_test = $PackagedChecked
        packaged_face_iris_inference_test = $PackagedFaceChecked
        packaged_studio_smoke_test = $PackagedStudioChecked
        basler_sdk_emulator_test = [bool]$BaslerEmulator
        realsense_sdk_enumeration_test = [bool]$DepthSdk
        physical_hardware_tested = $false
        real_person_gaze_accuracy_established = $false
        interactive_windows_monitor_acceptance = 'Requires a real operator and calibrated display/cameras'
    }
    $ReceiptName = if ($SkipTests -and ($PackagedChecked -or $PackagedStudioChecked)) { 'windows_packaged_verification.json' } else { 'windows_verification.json' }
    $Receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory $ReceiptName) -Encoding UTF8
    Write-Host 'Windows software verification completed; receipts are in release_evidence.'
} finally {
    $env:QT_QPA_PLATFORM = $PriorQtPlatform
    $env:QT_API = $PriorQtApi
    $env:GAZESPACE_TEST_PYLON_EMULATOR = $PriorEmulatorOptIn
    $env:PYLON_CAMEMU = $PriorPylonEmulator
    $env:GAZESPACE_PYTEST_EVIDENCE_DIR = $PriorPytestEvidenceDir
}
