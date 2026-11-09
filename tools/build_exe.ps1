# Builds dist\FlipperBuilder\FlipperBuilder.exe (one-folder app) with PyInstaller.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py tools\make_icon.py
Copy-Item "$root\assets\icon.png" "$root\ui\icon.png" -Force
& $py -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name FlipperBuilder `
    --icon "$root\assets\icon.ico" `
    --add-data "$root\assets;assets" `
    --add-data "$root\ui;ui" `
    --collect-all webview `
    --hidden-import clr `
    --hidden-import gui `
    --hidden-import device `
    --hidden-import catalog `
    --hidden-import flipper_storage `
    --hidden-import serial.tools.list_ports_windows `
    --collect-submodules ufbt `
    --copy-metadata ufbt `
    --version-file "$root\tools\version_info.txt" `
    --distpath dist --workpath .cache\pyinstaller --specpath .cache\pyinstaller `
    "$root\flipper_builder.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built $root\dist\FlipperBuilder\FlipperBuilder.exe"
