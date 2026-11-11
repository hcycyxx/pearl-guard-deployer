param(
    [switch]$SkipBuild,
    [switch]$InstallerOnly,
    [string]$GuardExe = ""
)

$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$python = "C:\Users\Blank\.workbuddy\binaries\python\versions\3.13.12.old.36692\python.exe"
$pythonDir = Split-Path -Parent $python
$iscc = "C:\Users\Blank\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
$minerSource = "C:\Mining\PRL-KRig\srbminer-3.8.0\SRBMiner-Multi-3-8-0"
$version = "2.2.0"
$dist = Join-Path $project "dist"
$package = Join-Path $project "package"
$minerDest = Join-Path $package "miner"
$env:PEARL_PACKAGE_ROOT = $package
$env:PEARL_OUTPUT_DIR = $dist
$env:PEARL_PROJECT_ROOT = $project

if (-not (Test-Path -LiteralPath $python)) { throw "Python 构建环境不存在：$python" }
if (-not (Test-Path -LiteralPath $iscc)) { throw "Inno Setup 编译器不存在：$iscc" }
if (-not (Test-Path -LiteralPath $minerSource)) { throw "SRBMiner 源目录不存在：$minerSource" }
if ($InstallerOnly) {
    if (-not $GuardExe) {
        $candidate = @(Get-ChildItem -LiteralPath (Join-Path $dist "onefile") -Filter "*.exe" -File)
        if ($candidate.Count -eq 0) { throw "未找到已构建的守护器 EXE" }
        $GuardExe = $candidate[0].FullName
    }
    $guardExe = (Resolve-Path -LiteralPath $GuardExe).Path
}

New-Item -ItemType Directory -Path $dist -Force | Out-Null
New-Item -ItemType Directory -Path $package -Force | Out-Null
New-Item -ItemType Directory -Path $minerDest -Force | Out-Null

if (-not $SkipBuild) {
    $env:PATH = @(
        "$env:SystemRoot\System32",
        $env:SystemRoot,
        $pythonDir,
        "$pythonDir\Scripts",
        "$pythonDir\Lib\site-packages\PyQt6\Qt6\bin"
    ) -join ";"
    $work = Join-Path $project "build_v2_2_0"
    $onefile = Join-Path $dist "onefile"
    New-Item -ItemType Directory -Path $onefile -Force | Out-Null
    Get-ChildItem -LiteralPath $onefile -Filter "*.exe" -File | Remove-Item -Force
    $builtGuardExe = Join-Path $onefile ("PearlGuard-V{0}.exe" -f $version)
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name "PearlGuard-V2.2.0" --distpath $onefile --workpath $work `
        --icon (Join-Path $project "assets\pearl.ico") `
        --specpath $dist (Join-Path $project "pearl_guard.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }
    $guardExe = $builtGuardExe
    if (-not (Test-Path -LiteralPath $guardExe)) { throw "守护器构建产物不存在：$guardExe" }
}

$minerFiles = @(
    "SRBMiner-MULTI.exe",
    "ReadMe.txt",
    "WinRing0x64.sys"
)
foreach ($name in $minerFiles) {
    Copy-Item -LiteralPath (Join-Path $minerSource $name) -Destination (Join-Path $minerDest $name) -Force
}

Copy-Item -LiteralPath $guardExe -Destination (Join-Path $package "PearlGuard.exe") -Force

$installerSource = Join-Path $project "installer"
Copy-Item -LiteralPath (Join-Path $installerSource "gpu_probe.ps1") -Destination $package -Force
Copy-Item -LiteralPath (Join-Path $installerSource "config.template.json") -Destination $package -Force
$readme = @(Get-ChildItem -LiteralPath $installerSource -File |
    Where-Object { $_.Extension -eq ".txt" })[0]
Copy-Item -LiteralPath $readme.FullName -Destination $package -Force

$iss = Join-Path $installerSource "Pearl守护器_V2.2.0.iss"
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 构建失败" }

$setup = @(Get-ChildItem -LiteralPath $dist -Filter "PearlGuard-V2.2.0-Setup*.exe" -File)[0].FullName
if (-not $setup -or -not (Test-Path -LiteralPath $setup)) { throw "安装包未生成" }
Get-FileHash -Algorithm SHA256 -LiteralPath $setup | Select-Object Path,Hash
