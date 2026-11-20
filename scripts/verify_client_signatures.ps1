<#
.SYNOPSIS
Fail-closed verification of client-approved Authenticode publisher signatures.
.DESCRIPTION
Runs on the actual deployment Windows PC after signing both executable files.
Does not create, import, upload or select an arbitrary signing identity.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$StudioExe,
    [Parameter(Mandatory=$true)][string]$CliExe,
    [Parameter(Mandatory=$true)][string]$SignerThumbprint,
    [Parameter(Mandatory=$true)][string]$Output,
    [switch]$RequireTimestamp
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Publisher signature verification must run on the target Windows workstation.'
}
$Expected = $SignerThumbprint.Replace(' ','').ToUpperInvariant()
if ($Expected -notmatch '^[0-9A-F]{40}$') {
    throw 'SignerThumbprint must be an explicitly approved 40-hex-digit certificate SHA-1 thumbprint.'
}
$Results = @()
foreach ($Requested in @(@('GazeSpaceStudio.exe',$StudioExe),@('GazeSpaceCLI.exe',$CliExe))) {
    $Name = [string]$Requested[0]
    $File = [IO.Path]::GetFullPath([string]$Requested[1])
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { throw "Missing $Name : $File" }
    if ([IO.Path]::GetFileName($File) -ne $Name) { throw "Expected $Name, found $File" }
    $Signature = Get-AuthenticodeSignature -LiteralPath $File
    $Thumbprint = if ($null -ne $Signature.SignerCertificate) {
        [string]$Signature.SignerCertificate.Thumbprint
    } else { $null }
    $Timestamped = $null -ne $Signature.TimeStamperCertificate
    $Valid = ($Signature.Status -eq 'Valid' -and $Thumbprint -eq $Expected)
    if ($RequireTimestamp -and -not $Timestamped) { $Valid = $false }
    $Results += [ordered]@{
        executable=$Name
        path=$File
        sha256=(Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
        signature_status=[string]$Signature.Status
        signer_thumbprint=$Thumbprint
        signer_subject=if ($null -ne $Signature.SignerCertificate) { [string]$Signature.SignerCertificate.Subject } else { $null }
        timestamped=$Timestamped
        valid_for_approved_publisher=[bool]$Valid
    }
}
$Passed = @($Results | Where-Object { -not $_['valid_for_approved_publisher'] }).Count -eq 0
$Receipt = [ordered]@{
    schema_version=1
    created_at_utc=[DateTime]::UtcNow.ToString('o')
    status=if ($Passed) {'passed'} else {'failed'}
    scope='Publisher signature verification only; not eye tracking or camera acceptance'
    expected_signer_thumbprint=$Expected
    require_timestamp=[bool]$RequireTimestamp
    executables=$Results
    physical_hardware_tested=$false
    host_application_integrated=$false
}
$File = [IO.Path]::GetFullPath($Output)
$Folder = [IO.Path]::GetDirectoryName($File)
[IO.Directory]::CreateDirectory($Folder) | Out-Null
$Receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $File -Encoding UTF8
Write-Host "Publisher signature receipt: $File"
if (-not $Passed) { throw 'Publisher verification failed: unsigned, invalid, mismatched identity or missing required timestamp.' }
