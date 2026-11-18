<#
.SYNOPSIS
Install GazeSpace into a local user-writable virtual environment and open Studio.
.DESCRIPTION
Requires an existing 64-bit Python 3.12 or 3.11 installation. No administrator
rights, global packages, machine execution-policy changes or camera drivers
are installed by this script. Optional SDK packages still need vendor drivers.
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', '3.12', '3.11')]
    [string]$PythonVersion = 'auto',
    [switch]$Basler,
    [switch]$Depth,
    [switch]$NoLaunch,
    [switch]$Demo
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This launcher targets Windows. On Linux/macOS use python -m gazespace desktop.'
}
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $ProjectRoot
$VirtualEnvironment = Join-Path $ProjectRoot '.venv'
$PythonExecutable = Join-Path $VirtualEnvironment 'Scripts\python.exe'

function Invoke-Checked {
    param([Parameter(Mandatory=$true)][string]$Executable,
          [Parameter(Mandatory=$true)][string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE : $Executable $($Arguments -join ' ')"
    }
}

$CompatibilityCheck = "import sys,struct; ok=sys.version_info[:2] in ((3,11),(3,12)) and struct.calcsize('P')==8; print(sys.version); sys.exit(0 if ok else 2)"
if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    $Created = $false
    # setup-python selects an exact x64 tool-cache interpreter on hosted CI.
    # Prefer it to a machine-wide py launcher registry or install-manager alias.
    if ($env:GITHUB_ACTIONS -eq 'true') {
        $ActionsPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $ActionsPython) {
            & $ActionsPython.Source -c $CompatibilityCheck
            $Compatible = ($LASTEXITCODE -eq 0)
            if ($Compatible -and $PythonVersion -ne 'auto') {
                $Detected = (& $ActionsPython.Source -c "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))").Trim()
                $Compatible = ($LASTEXITCODE -eq 0 -and $Detected -eq $PythonVersion)
            }
            if ($Compatible) {
                Invoke-Checked -Executable $ActionsPython.Source -Arguments @('-m', 'venv', $VirtualEnvironment)
                $Created = $true
            }
        }
    }
    $PythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    $Candidates = if ($PythonVersion -eq 'auto') { @('3.12', '3.11') } else { @($PythonVersion) }
    if (-not $Created -and $null -ne $PythonLauncher) {
        foreach ($Candidate in $Candidates) {
            try {
                & $PythonLauncher.Source "-$Candidate" -c $CompatibilityCheck 2>$null
                $Compatible = ($LASTEXITCODE -eq 0)
            } catch { $Compatible = $false }
            if ($Compatible) {
                Invoke-Checked -Executable $PythonLauncher.Source -Arguments @("-$Candidate", '-m', 'venv', $VirtualEnvironment)
                $Created = $true
                break
            }
        }
    }
    if (-not $Created) {
        $FallbackPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $FallbackPython) {
            try {
                & $FallbackPython.Source -c $CompatibilityCheck 2>$null
                $Compatible = ($LASTEXITCODE -eq 0)
                if ($Compatible -and $PythonVersion -ne 'auto') {
                    $Detected = (& $FallbackPython.Source -c "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))").Trim()
                    $Compatible = ($LASTEXITCODE -eq 0 -and $Detected -eq $PythonVersion)
                }
            } catch { $Compatible = $false }
            if ($Compatible) {
                Invoke-Checked -Executable $FallbackPython.Source -Arguments @('-m', 'venv', $VirtualEnvironment)
                $Created = $true
            }
        }
    }
    if (-not $Created) {
        throw 'Install 64-bit Python 3.12 or 3.11 from python.org, including the py launcher, then run this script again.'
    }
}
Invoke-Checked -Executable $PythonExecutable -Arguments @('-c', $CompatibilityCheck)
if ($PythonVersion -ne 'auto') {
    $ExistingVersion = (& $PythonExecutable -c "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))").Trim()
    if ($LASTEXITCODE -ne 0 -or $ExistingVersion -ne $PythonVersion) {
        throw "Existing .venv uses Python $ExistingVersion. Move that environment aside before selecting Python $PythonVersion."
    }
}
$Extras = @('full', 'test')
if ($Basler) { $Extras += 'basler' }
if ($Depth) { $Extras += 'depth' }
$InstallTarget = '.[' + ($Extras -join ',') + ']'
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pip', 'install', '-e', $InstallTarget)
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'pip', 'check')
Invoke-Checked -Executable $PythonExecutable -Arguments @('-m', 'gazespace', 'doctor')
Write-Host 'GazeSpace is installed in the project .venv.'
if ($Basler) { Write-Host 'Basler SDK package installed. Connect a camera using its explicit serial; install its vendor driver separately when required.' }
if ($Depth) { Write-Host 'RealSense SDK package installed. Compatible camera firmware and drivers are still required.' }
if (-not $NoLaunch) {
    $PythonWindowed = Join-Path $VirtualEnvironment 'Scripts\pythonw.exe'
    $StudioEntry = Join-Path $PSScriptRoot 'gazespace_studio_entry.py'
    # Start-Process joins its argument array. Quote this local path so paths with
    # spaces work on Windows PowerShell 5.1 as well as PowerShell 7.
    $StudioArguments = @('"' + $StudioEntry + '"')
    if ($Demo) { $StudioArguments += '--demo' }
    $StudioProcess = Start-Process -FilePath $PythonWindowed -ArgumentList $StudioArguments -WorkingDirectory $ProjectRoot -PassThru
    Start-Sleep -Milliseconds 800
    if ($StudioProcess.HasExited -and $StudioProcess.ExitCode -ne 0) {
        throw 'Studio failed during startup. See the message box and %LOCALAPPDATA%\GazeSpace\logs\startup_error.log.'
    }
    Write-Host 'GazeSpace Studio launched.'
}
