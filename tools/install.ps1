# Installs (or updates) Flipper Workbench from dist\FlipperWorkbench into %LOCALAPPDATA%\Programs\FlipperWorkbench (or -Target <folder>).
# App data (SDKs in .cache, output, library, settings, backups) lives next to the exe and survives updates.
# An install of the old "Flipper Builder" (before 1.6.0) is taken over: its data moves here, its program
# files, shortcuts and Settings > Apps entry are removed.
param([string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\FlipperWorkbench'))
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist\FlipperWorkbench'
if (-not (Test-Path "$dist\FlipperWorkbench.exe")) { throw "Build first: tools\build_exe.ps1" }
$version = '1.13.1'
$uninstRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'

# Close running copies (old and new name) and wait until Windows releases their files
$running = Get-Process FlipperWorkbench, FlipperBuilder -ErrorAction SilentlyContinue
if ($running) { $running | Stop-Process -Force; $running | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force $Target | Out-Null
$Target = (Resolve-Path $Target).Path
if (Test-Path "$Target\_internal") {
    for ($i = 1; $i -le 10; $i++) {
        try { Remove-Item -Recurse -Force "$Target\_internal" -ErrorAction Stop; break }
        catch { if ($i -eq 10) { throw "Couldn't replace the old files (still in use?): $_" }; Start-Sleep -Milliseconds 700 }
    }
}
Copy-Item "$dist\*" $Target -Recurse -Force

# Moves a file or folder into $destDir. Folders are renamed whole on the same drive: PowerShell 5.1's
# Move-Item walks the tree item by item and fails on links, like the toolchain's "current" junction.
# If the destination already has a folder of that name, the two are merged (nothing is overwritten).
function Move-Data([string]$src, [string]$destDir) {
    $dst = Join-Path $destDir (Split-Path -Leaf $src)
    $srcItem = Get-Item -Force $src
    $isDir = $srcItem.PSIsContainer
    $isLink = [bool]($srcItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
    # Test-Path follows links, so a broken link would look missing; ask the file system directly
    if ([IO.Directory]::Exists($dst) -and $isLink -and -not (Get-ChildItem -Force $dst)) {
        [IO.Directory]::Delete($dst)   # an empty stub left by an earlier failed move: the link replaces it
    }
    if ((Test-Path $dst) -or [IO.Directory]::Exists($dst) -or [IO.File]::Exists($dst)) {
        if ($isDir -and -not $isLink -and (Get-Item -Force $dst).PSIsContainer) {
            foreach ($c in Get-ChildItem -Force $src) { Move-Data $c.FullName $dst }
            if (-not (Get-ChildItem -Force $src)) { [IO.Directory]::Delete($src) }
        }
        return
    }
    if ([IO.Path]::GetPathRoot($src) -ne [IO.Path]::GetPathRoot($dst)) {
        if ($isDir) { robocopy $src $dst /E /MOVE /SL /NFL /NDL /NJH /NJS /NP | Out-Null; if ($LASTEXITCODE -ge 8) { throw "Couldn't move $src" } }
        else { Move-Item $src $dst }
    }
    elseif ($isDir) { [IO.Directory]::Move($src, $dst) }
    else { [IO.File]::Move($src, $dst) }
}

# ---- take over an old "Flipper Builder" install
$oldKey = "$uninstRoot\FlipperBuilder"
$oldDirs = @()
if (Test-Path $oldKey) { $oldDirs += (Get-ItemProperty $oldKey).InstallLocation }
$oldDirs += (Join-Path $env:LOCALAPPDATA 'Programs\FlipperBuilder')
foreach ($old in ($oldDirs | Where-Object { $_ } | Select-Object -Unique)) {
    if (-not (Test-Path $old)) { continue }
    $old = (Resolve-Path $old).Path
    if ($old -eq $Target) { continue }
    Write-Host "Moving data from the old Flipper Builder install in $old"
    foreach ($item in '.cache', 'output', 'backups', 'screenshots', 'settings.json', 'library.json') {
        $src = Join-Path $old $item
        if (Test-Path $src) { Move-Data $src $Target }
    }
    # built-app paths in the library point into the old folder (the app also repairs these on load)
    $lib = Join-Path $Target 'library.json'
    if (Test-Path $lib) {
        $esc = ($old.TrimEnd('\') + '\').Replace('\', '\\')
        $new = ($Target.TrimEnd('\') + '\').Replace('\', '\\')
        $text = [IO.File]::ReadAllText($lib)
        [IO.File]::WriteAllText($lib, $text.Replace($esc, $new))
    }
    foreach ($item in 'FlipperBuilder.exe', '_internal', 'uninstall.cmd') {
        $p = Join-Path $old $item
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    if (-not (Get-ChildItem -Force $old)) { Remove-Item -Force $old }
    else { Write-Host "Left in $old (not moved because $Target already has them): $((Get-ChildItem -Force -Name $old) -join ', ')" }
}
foreach ($l in (Join-Path ([Environment]::GetFolderPath('Programs')) 'Flipper Builder.lnk'),
               (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Flipper Builder.lnk')) {
    if (Test-Path $l) { Remove-Item -Force $l }
}
if (Test-Path $oldKey) { Remove-Item -Recurse -Force $oldKey }

# reuse SDKs already downloaded by the source version (same drive: instant move)
$srcSdk = Join-Path $root '.cache\sdk'
if ((Test-Path $srcSdk) -and -not (Test-Path "$Target\.cache\sdk")) {
    New-Item -ItemType Directory -Force "$Target\.cache" | Out-Null
    Move-Item $srcSdk "$Target\.cache\sdk"
}

$exe = "$Target\FlipperWorkbench.exe"
$shell = New-Object -ComObject WScript.Shell
$links = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Flipper Workbench.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Flipper Workbench.lnk')
)
foreach ($l in $links) {
    $s = $shell.CreateShortcut($l)
    # Arguments must be cleared: an older shortcut (from running the source) kept a .py argument,
    # which made the exe start in command-line mode and quit without a window.
    $s.TargetPath = $exe; $s.Arguments = ''; $s.WorkingDirectory = $Target; $s.IconLocation = "$exe,0"
    $s.Description = 'Build, install and manage Flipper Zero apps'
    $s.Save()
}

# uninstaller + entry in Settings > Apps (per-user, no admin)
@"
@echo off
taskkill /im FlipperWorkbench.exe /f >nul 2>&1
del "$($links[0])" "$($links[1])" >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\FlipperWorkbench" /f >nul 2>&1
echo Flipper Workbench removed. Built apps, backups and SDKs are still in $Target (delete the folder to free the space).
pause
"@ | Set-Content -Encoding ascii "$Target\uninstall.cmd"

$key = "$uninstRoot\FlipperWorkbench"
New-Item -Force $key | Out-Null
$size = [int]((Get-ChildItem $Target -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
@{ DisplayName = 'Flipper Workbench'; DisplayVersion = $version; Publisher = 'c3rb3ruszx'
   DisplayIcon = "$exe,0"; InstallLocation = $Target; UninstallString = "`"$Target\uninstall.cmd`""
}.GetEnumerator() | ForEach-Object { Set-ItemProperty $key $_.Key $_.Value }
Set-ItemProperty $key EstimatedSize $size -Type DWord
Set-ItemProperty $key NoModify 1 -Type DWord
Set-ItemProperty $key NoRepair 1 -Type DWord
Write-Host "Installed to $Target"
