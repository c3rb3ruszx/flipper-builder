# Builds dist\FlipperBuilder\FlipperBuilder.exe (one-folder app) with PyInstaller.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py tools\make_icon.py
& $py -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name FlipperBuilder `
    --icon "$root\assets\icon.ico" `
    --add-data "$root\assets;assets" `
    --collect-submodules ufbt `
    --copy-metadata ufbt `
    --version-file "$root\tools\version_info.txt" `
    --distpath dist --workpath .cache\pyinstaller --specpath .cache\pyinstaller `
    "$root\flipper_builder.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built $root\dist\FlipperBuilder\FlipperBuilder.exe"
