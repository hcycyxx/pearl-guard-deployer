param(
    [switch]$SkipBuild,
    [switch]$InstallerOnly,
    [string]$GuardExe = "",
    [string]$MinerArchive = "C:\Mining\PRL-KRig\_download\SRBMiner-Multi-3-8-0-win64.zip",
    [string]$Python = "C:\Users\Blank\.workbuddy\binaries\python\versions\3.13.12.old.36692\python.exe",
    [string]$ISCC = "C:\Users\Blank\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$version = "2.3.0"
$dist = Join-Path $project "dist"
$onefile = Join-Path $dist "onefile"
$work = Join-Path $project "build_v2_3_0"
$package = Join-Path $project "package\v2.3.0"
$minerDest = Join-Path $package "miner"
$installerSource = Join-Path $project "installer"
$env:PEARL_PACKAGE_ROOT = $package
$env:PEARL_OUTPUT_DIR = $dist
$env:PEARL_PROJECT_ROOT = $project

if (-not (Test-Path -LiteralPath $ISCC -PathType Leaf)) { throw "Inno Setup 编译器不存在：$ISCC" }
if (-not (Test-Path -LiteralPath $MinerArchive -PathType Leaf)) { throw "SRBMiner 3.8.0 ZIP 不存在：$MinerArchive" }
# GitHub Release digest for SRBMiner-Multi-3-8-0-win64.zip.
$minerHash = "33EEF5F8946259290013CE370A746A85F91C2BBCD81FCEFA1AAD904DD9A912F8"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $MinerArchive).Hash -ne $minerHash) {
    throw "内置 SRBMiner ZIP 的 SHA256 与官方发布不符"
}

New-Item -ItemType Directory -Path $onefile, $minerDest, $work -Force | Out-Null
$builtGuardExe = Join-Path $onefile "PearlGuard-V$version.exe"
if (-not ($SkipBuild -or $InstallerOnly)) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python 构建环境不存在：$Python" }
    $pyInstallerArgs = @(
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "PearlGuard-V$version", "--distpath", $onefile, "--workpath", $work,
        "--icon", (Join-Path $project "assets\pearl.ico"),
        "--specpath", $work, (Join-Path $project "pearl_guard.py")
    )
    & $Python -m PyInstaller @pyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }
    $GuardExe = $builtGuardExe
} elseif (-not $GuardExe) {
    $GuardExe = $builtGuardExe
}
if (-not (Test-Path -LiteralPath $GuardExe -PathType Leaf)) { throw "守护器构建产物不存在：$GuardExe" }

$minerZip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $MinerArchive).Path)
try {
    foreach ($name in @("SRBMiner-MULTI.exe", "ReadMe.txt", "WinRing0x64.sys")) {
        $entries = @($minerZip.Entries | Where-Object { $_.Name -ieq $name })
        if ($entries.Count -ne 1) { throw "官方 ZIP 中未找到唯一的 $name" }
        $dest = Join-Path $minerDest $name
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $dest, $true)
    }
} finally {
    $minerZip.Dispose()
}

Copy-Item -LiteralPath $GuardExe -Destination (Join-Path $package "PearlGuard.exe") -Force
Copy-Item -LiteralPath (Join-Path $installerSource "gpu_probe.ps1") -Destination $package -Force
Copy-Item -LiteralPath (Join-Path $installerSource "config.template.json") -Destination $package -Force
Copy-Item -LiteralPath (Join-Path $project "使用说明_V2.3.0.txt") -Destination $package -Force
& $ISCC (Join-Path $installerSource "Pearl守护器_V2.3.0.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 构建失败" }

$setupName = "PearlGuard-V$version-Setup.exe"
$setup = Join-Path $dist $setupName
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $setup
"$($hash.Hash.ToLowerInvariant())  $setupName" | Set-Content -LiteralPath "$setup.sha256" -Encoding ascii
$hash | Select-Object Path, Hash
