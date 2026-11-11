#!/usr/bin/env bash
# Builds dist/Flipper Workbench.app on macOS with PyInstaller.
# UNTESTED: written on Windows, run it on a Mac and report back. Needs Python 3.10+ (python.org or Homebrew).
#
#   python3 -m venv .venv
#   .venv/bin/pip install ufbt pywebview pyserial pyinstaller pillow heatshrink2
#   bash tools/build_mac.sh
#
# Data (SDKs, built apps, library, settings, backups) goes to ~/Library/Application Support/Flipper Workbench.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
py="$root/.venv/bin/python"
"$py" tools/make_icon.py
cp assets/icon.png ui/icon.png
"$py" -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name "Flipper Workbench" \
  --icon "$root/assets/icon.png" \
  --osx-bundle-identifier "io.github.c3rb3ruszx.flipperworkbench" \
  --add-data "$root/assets:assets" \
  --add-data "$root/ui:ui" \
  --add-data "$root/companion:companion" \
  --collect-all webview \
  --hidden-import gui \
  --hidden-import device \
  --hidden-import catalog \
  --hidden-import capture \
  --hidden-import firmware \
  --hidden-import irdb \
  --hidden-import music \
  --hidden-import anim \
  --hidden-import assetpacks \
  --hidden-import sync \
  --hidden-import devkit \
  --hidden-import remote \
  --hidden-import devboard \
  --hidden-import ble \
  --hidden-import qrcode \
  --collect-all esptool \
  --collect-all bleak \
  --collect-submodules qrcode \
  --hidden-import PIL.ImageSequence \
  --hidden-import flipper_storage \
  --hidden-import fapmeta \
  --hidden-import serial.tools.list_ports_osx \
  --collect-submodules ufbt \
  --copy-metadata ufbt \
  --distpath dist --workpath .cache/pyinstaller-mac --specpath .cache/pyinstaller-mac \
  "$root/flipper_workbench.py"
echo "Built $root/dist/Flipper Workbench.app"
echo "First launch: right-click the app > Open (it isn't signed by Apple)."
