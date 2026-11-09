# Installs (or updates) Flipper Builder from dist\FlipperBuilder into %LOCALAPPDATA%\Programs\FlipperBuilder (or -Target <folder>).
# App data (SDKs in .cache, output, settings.json) lives next to the exe and survives updates.
param([string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\FlipperBuilder'))
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist\FlipperBuilder'
if (-not (Test-Path "$dist\FlipperBuilder.exe")) { throw "Build first: tools\build_exe.ps1" }

# Close a running copy and wait until Windows releases its files (DLLs stay locked for a moment)
$running = Get-Process FlipperBuilder -ErrorAction SilentlyContinue
if ($running) { $running | Stop-Process -Force; $running | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force $Target | Out-Null
if (Test-Path "$Target\_internal") {
    for ($i = 1; $i -le 10; $i++) {
        try { Remove-Item -Recurse -Force "$Target\_internal" -ErrorAction Stop; break }
        catch { if ($i -eq 10) { throw "Couldn't replace the old files (still in use?): $_" }; Start-Sleep -Milliseconds 700 }
    }
}
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
    # Arguments must be cleared: an older shortcut (from running the source) kept a .py argument,
    # which made the exe start in command-line mode and quit without a window.
    $s.TargetPath = $exe; $s.Arguments = ''; $s.WorkingDirectory = $Target; $s.IconLocation = "$exe,0"
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
@{ DisplayName = 'Flipper Builder'; DisplayVersion = '1.5.0'; Publisher = 'c3rb3ruszx'
   DisplayIcon = "$exe,0"; InstallLocation = $Target; UninstallString = "`"$Target\uninstall.cmd`""
}.GetEnumerator() | ForEach-Object { Set-ItemProperty $key $_.Key $_.Value }
Set-ItemProperty $key EstimatedSize $size -Type DWord
Set-ItemProperty $key NoModify 1 -Type DWord
Set-ItemProperty $key NoRepair 1 -Type DWord
Write-Host "Installed to $Target"
