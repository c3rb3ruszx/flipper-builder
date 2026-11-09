# Flipper Builder

Paste a GitHub link to a Flipper Zero app, press **Build**, get a `.fap`.

A small Windows app around [ufbt](https://github.com/flipperdevices/flipperzero-ufbt), Flipper's official
micro build tool. No command line, no SDK setup: it fetches the right SDK for your firmware, clones the
repo, finds the app(s), builds them and opens the folder with the result.

![icon](assets/icon.png)

## Features

- Accepts any GitHub link: a repo, a branch, a subfolder (`/tree/dev/some_app`), a file (`/blob/...`)
  or a commit. Branch names with slashes are resolved correctly.
- Repos with several apps: builds all of them (up to 25; for big collections paste the app's folder link).
- Firmware SDKs: **Official** (release / dev), **Momentum** (release / dev), **Unleashed**.
  Pick the one your Flipper runs, or the app will show "API mismatch".
- SDK refreshes itself once a day; **Update SDK now** forces it (handy right after a firmware update).
- Optional **Send to Flipper over USB** after building (`ufbt launch`; close qFlipper first).
- Tells you which SD card folder each `.fap` belongs in (`apps/<Category>/`).
- Works without git too (downloads a zip; submodules are then missing).

## Install

Download `FlipperBuilder-win64.zip` from [Releases](../../releases), unzip anywhere, run
`FlipperBuilder.exe`. The first build downloads Flipper's toolchain (a few hundred MB, once).
Downloaded SDKs and built apps are kept next to the exe.

## Run from source

Needs Windows and Python 3.10+ (with tkinter). Double-click `Flipper Builder.bat`, or:

```
python -m venv .venv
.venv\Scripts\pip install ufbt
.venv\Scripts\python flipper_builder.py                      # GUI
.venv\Scripts\python flipper_builder.py <github-url> --fw momentum-dev [--send]   # CLI
```

`--fw` is one of `official`, `official-dev`, `momentum`, `momentum-dev`, `unleashed`.

## Build the exe

```
.venv\Scripts\pip install pyinstaller pillow
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1     # -> dist\FlipperBuilder\
powershell -ExecutionPolicy Bypass -File tools\install.ps1       # optional: Start menu, desktop, Apps list
```

`install.ps1` installs to `%LOCALAPPDATA%\Programs\FlipperBuilder` by default (`-Target <folder>` to
change it) and registers a per-user uninstaller. No admin rights needed.

## How it works

The exe bundles ufbt's SDK downloader. Compiling runs inside Flipper's own toolchain
(`fbtenv.cmd` + SCons, exactly what the `ufbt` command does), which ships its own Python, so the exe
doesn't need Python installed.

## License

GPL-3.0, same as ufbt, which the exe bundles.
