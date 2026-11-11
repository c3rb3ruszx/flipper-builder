# Builds dist\FlipperWorkbench\FlipperWorkbench.exe (one-folder app) with PyInstaller.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py tools\make_icon.py
Copy-Item "$root\assets\icon.png" "$root\ui\icon.png" -Force
& $py -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name FlipperWorkbench `
    --icon "$root\assets\icon.ico" `
    --add-data "$root\assets;assets" `
    --add-data "$root\ui;ui" `
    --add-data "$root\companion;companion" `
    --collect-all webview `
    --hidden-import clr `
    --hidden-import gui `
    --hidden-import device `
    --hidden-import catalog `
    --hidden-import flipper_storage `
    --hidden-import fapmeta `
    --hidden-import capture `
    --hidden-import firmware `
    --hidden-import irdb `
    --hidden-import music `
    --hidden-import anim `
    --hidden-import assetpacks `
    --hidden-import packmaker `
    --hidden-import iconslots `
    --hidden-import heatshrink2 `
    --collect-all imageio_ffmpeg `
    --hidden-import sync `
    --hidden-import devkit `
    --hidden-import remote `
    --hidden-import devboard `
    --hidden-import ble `
    --hidden-import qrcode `
    --collect-all esptool `
    --collect-all bleak `
    --collect-all winrt `
    --collect-submodules qrcode `
    --hidden-import PIL.ImageSequence `
    --hidden-import PIL.Image `
    --hidden-import PIL.GifImagePlugin `
    --hidden-import serial.tools.list_ports_windows `
    --collect-submodules ufbt `
    --copy-metadata ufbt `
    --version-file "$root\tools\version_info.txt" `
    --distpath dist --workpath .cache\pyinstaller --specpath .cache\pyinstaller `
    "$root\flipper_workbench.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built $root\dist\FlipperWorkbench\FlipperWorkbench.exe"
