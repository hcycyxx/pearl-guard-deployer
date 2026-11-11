param(
    [string]$OutputFile = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$gpus = @(
    Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -and
            $_.Name -notmatch "Virtual|Remote|Basic Display|Microsoft"
        } |
        Select-Object Name, PNPDeviceID, DriverVersion
)

$system = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue |
    Select-Object Manufacturer, Model

function ConvertTo-Worker([string]$Name) {
    $text = $Name -replace "\(R\)|\(TM\)", ""
    $text = $text -replace "\bNVIDIA\b|\bGEFORCE\b|\bAMD\b|\bRADEON\b", ""
    $text = $text -replace "\bGRAPHICS\b|\bGPU\b|\bLAPTOP\b", ""
    $text = $text -replace "\bRTX\s*", "RTX"
    $text = $text -replace "\bGTX\s*", "GTX"
    $text = $text -replace "\bRX\s*", "RX"
    $text = $text -replace "\s+", ""
    if (-not $text) { $text = "PearlRig" }
    return $text
}

$vendor = "Unknown"
$model = "No supported discrete GPU detected"
$worker = "PearlRig"
$found = $false

foreach ($candidate in @($gpus)) {
    if ($candidate.Name -match "NVIDIA") {
        $vendor = "NVIDIA"
        $model = $candidate.Name
        $worker = ConvertTo-Worker $candidate.Name
        $found = $true
        break
    }
}
if (-not $found) {
    foreach ($candidate in @($gpus)) {
        if ($candidate.Name -match "AMD|Radeon") {
            $vendor = "AMD"
            $model = $candidate.Name
            $worker = ConvertTo-Worker $candidate.Name
            $found = $true
            break
        }
    }
}
if (-not $found -and @($gpus).Count -gt 0) {
    $model = $gpus[0].Name
    $worker = ConvertTo-Worker $model
}
if (-not $found) {
    $machine = (($system.Manufacturer, $system.Model) -join "-").Trim("- ")
    if ($machine) {
        $worker = ($machine -replace "[^A-Za-z0-9._-]+", "-").Trim("-._")
    }
}

$result = [ordered]@{
    ok = $found
    vendor = $vendor
    gpu_name = $model
    worker = $worker
    gpus = @($gpus)
}
$json = $result | ConvertTo-Json -Depth 4 -Compress

if ($OutputFile) {
    [System.IO.File]::WriteAllText($OutputFile, $json, [System.Text.UTF8Encoding]::new($false))
} else {
    Write-Output $json
}

if (-not $found) {
    exit 2
}
