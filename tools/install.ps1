# Installs (or updates) Flipper Builder from dist\FlipperBuilder into %LOCALAPPDATA%\Programs\FlipperBuilder (or -Target <folder>).
# App data (SDKs in .cache, output, settings.json) lives next to the exe and survives updates.
param([string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\FlipperBuilder'))
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist\FlipperBuilder'
if (-not (Test-Path "$dist\FlipperBuilder.exe")) { throw "Build first: tools\build_exe.ps1" }

Get-Process FlipperBuilder -ErrorAction SilentlyContinue | Stop-Process -Force
New-Item -ItemType Directory -Force $Target | Out-Null
if (Test-Path "$Target\_internal") { Remove-Item -Recurse -Force "$Target\_internal" }
Copy-Item "$dist\*" $Target -Recurse -Force

# reuse SDKs already downloaded by the source version (same drive: instant move)
$srcSdk = Join-Path $root '.cache\sdk'
if ((Test-Path $srcSdk) -and -not (Test-Path "$Target\.cache\sdk")) {
    New-Item -ItemType Directory -Force "$Target\.cache" | Out-Null
    Move-Item $srcSdk "$Target\.cache\sdk"
}

$exe = "$Target\FlipperBuilder.exe"
$shell = New-Object -ComObject WScript.Shell
$links = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Flipper Builder.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Flipper Builder.lnk')
)
foreach ($l in $links) {
    $s = $shell.CreateShortcut($l)
    $s.TargetPath = $exe; $s.WorkingDirectory = $Target; $s.IconLocation = "$exe,0"
    $s.Description = 'Paste a GitHub link, get a Flipper Zero .fap'
    $s.Save()
}

# uninstaller + entry in Settings > Apps (per-user, no admin)
@"
@echo off
taskkill /im FlipperBuilder.exe /f >nul 2>&1
del "$($links[0])" "$($links[1])" >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\FlipperBuilder" /f >nul 2>&1
echo Flipper Builder removed. Built apps and SDKs are still in $Target (delete the folder to free the space).
pause
"@ | Set-Content -Encoding ascii "$Target\uninstall.cmd"

$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\FlipperBuilder'
New-Item -Force $key | Out-Null
$size = [int]((Get-ChildItem $Target -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
@{ DisplayName = 'Flipper Builder'; DisplayVersion = '1.3.0'; Publisher = 'c3rb3ruszx'
   DisplayIcon = "$exe,0"; InstallLocation = $Target; UninstallString = "`"$Target\uninstall.cmd`""
}.GetEnumerator() | ForEach-Object { Set-ItemProperty $key $_.Key $_.Value }
Set-ItemProperty $key EstimatedSize $size -Type DWord
Set-ItemProperty $key NoModify 1 -Type DWord
Set-ItemProperty $key NoRepair 1 -Type DWord
Write-Host "Installed to $Target"
