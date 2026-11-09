# Flipper Workbench

[![Latest release](https://img.shields.io/github/v/release/c3rb3ruszx/flipper-workbench?color=ff8200&label=release)](../../releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-ff8200)](../../releases/latest)
[![License](https://img.shields.io/github/license/c3rb3ruszx/flipper-workbench?color=ff8200)](LICENSE)

**Build, install and manage your Flipper Zero's apps from your PC.**

![Flipper Workbench compiling an app while the dolphin solders](assets/screenshot.png)

A Windows desktop companion for the Flipper Zero. Paste a GitHub link and it builds the app for your
exact firmware with [ufbt](https://github.com/flipperdevices/flipperzero-ufbt), Flipper's official
build tool, then installs it over USB. No command line, no SDK setup. It also lists and manages
every app on your Flipper, browses and backs up the SD card, and mirrors the Flipper's screen with a
working remote. The dolphin keeps you company on a Flipper-style LCD the whole time: it reads the
repo, solders while it compiles, DJs when it's done and cries when the build breaks.

*Called Flipper Builder up to version 1.5.0.*

**[Download the latest release](../../releases/latest)** · [What's new](CHANGELOG.md)

## Quick start

1. Download `FlipperWorkbench-win64.zip`, unzip it anywhere, and run `FlipperWorkbench.exe`.
2. Plug in your Flipper (close qFlipper). The app reads its firmware and picks the matching SDK.
3. Paste a GitHub link to an app, or pick one on the **Discover** tab, and press **Build**.

## Features

### Build

- **Any GitHub link**: a repo, a branch, a subfolder (`/tree/dev/some_app`), a file or a commit.
  Drag a link onto the window, or copy one before opening the app and it's pasted in for you.
- **Every big firmware**: Official, Momentum and Unleashed, each as a release or dev build.
- **Pick the apps you want**: repos with several apps open a checklist with each app's icon.
- **Live progress**: the dolphin, build stages, a compile progress bar, and the full log in a slide-up drawer.
- **Errors in plain English**: a failed build says why (a function your firmware doesn't have, a
  missing library, an outdated app) and offers one-click "try another firmware" buttons.
- **Local folders + watch mode**: build from a folder on your PC and rebuild (and reinstall)
  automatically on every save. Handy when you write your own apps.

### Your Flipper

- **Knows your Flipper**: plug it in and the app reads its firmware and API version, selects the
  matching SDK, and warns you before building something that won't run on it.
- **Install straight to it**: *Install to SD* copies the `.fap` into `apps/<Category>/`, or
  *Install & open* also launches it.
- **See what's on it**: the *On Flipper* tab lists every app on the SD card with its real name,
  version and icon, plus SD card space and which apps won't open on your firmware. Open, remove or
  update any of them.
- **Screen mirror + remote**: the app's LCD shows your Flipper's real screen, live. The d-pad and
  your keyboard (arrows, Enter, Backspace) press the real buttons, holding one does a long press,
  and one click saves a screenshot.
- **Files**: browse the whole SD card; upload, download, rename, make folders and delete.
- **Backups**: one click backs up your captures, NFC/RFID dumps, IR remotes, BadUSB scripts and app
  data to a zip on your PC, and puts them back later. Ideal before a firmware update or SD card swap.

### Library & Discover

- **Library + Rebuild all**: every app you build is remembered, with badges for what runs on your
  Flipper and what's outdated. After a firmware update, one click rebuilds them all (and installs them).
- **Discover**: search the 500+ apps in Flipper's official Apps Catalog and build any of them in
  one click, from the exact version Flipper reviewed.

### Little things

- The d-pad works like a Flipper's: left/right switches firmware, OK builds, Back cancels. Keyboard:
  arrows, Enter, Esc, Ctrl+L (link), Ctrl+F (search). Poke the dolphin.
- SDKs refresh themselves once a day; the **SDK** button in the header forces it.
- A notice in the header when a new version of Flipper Workbench is out.

## Screenshots

| | |
|---|---|
| ![Build finished, with the app's own icon and its SD card folder](assets/screenshot-done.png) | ![The apps on a Flipper, with their own icons](assets/screenshot-onflipper.png) |
| **Done**: the dolphin DJs, and the app's card shows where it goes on the SD card | **On Flipper**: everything on the SD card, with real names, icons and versions |
| ![The library of built apps](assets/screenshot-library.png) | ![Discover: the official Flipper Apps Catalog](assets/screenshot-discover.png) |
| **Library**: every build, per firmware, with Rebuild all | **Discover**: 500+ apps from Flipper's official catalog |
| ![Your Flipper's real screen, mirrored live in the app](assets/screenshot-mirror.png) | ![Browsing the SD card](assets/screenshot-files.png) |
| **Mirror**: your Flipper's real screen, controlled from the app | **Files**: the whole SD card, with upload, download and backups |

## Install

Download `FlipperWorkbench-win64.zip` from [Releases](../../releases/latest), unzip anywhere and run
`FlipperWorkbench.exe`. Needs Windows 10 or 11 with Microsoft Edge WebView2 (built into Windows 11).
The first build downloads Flipper's toolchain (a few hundred MB, once). SDKs and built apps are kept
next to the exe. For USB features, close qFlipper: only one program can use the Flipper's USB port.

## Run from source

Needs Windows and Python 3.10+. Double-click `Flipper Workbench.bat`, or:

```
python -m venv .venv
.venv\Scripts\pip install ufbt pywebview pyserial
.venv\Scripts\python flipper_workbench.py                                                    # GUI
.venv\Scripts\python flipper_workbench.py <github-url-or-folder> --fw momentum-dev [--send]  # CLI
```

`--fw` is one of `official`, `official-dev`, `momentum`, `momentum-dev`, `unleashed`, `unleashed-dev`.

The UI is plain HTML/CSS/JS in `ui/`. Open `ui/index.html` through any local web server and it runs
against a built-in mock, which is handy for working on the design.

## Build the exe

```
.venv\Scripts\pip install pyinstaller pillow
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1     # -> dist\FlipperWorkbench\
powershell -ExecutionPolicy Bypass -File tools\install.ps1       # optional: Start menu, desktop, Apps list
```

`install.ps1` installs to `%LOCALAPPDATA%\Programs\FlipperWorkbench` by default (`-Target <folder>` to
change it) and registers a per-user uninstaller. No admin rights needed. It also takes over an old
Flipper Builder install: SDKs, built apps, library and settings move to the new folder, and the old
shortcuts and Apps entry are removed.

## How it works

The window is a WebView2 page (pywebview) talking to the Python build engine. The exe bundles
ufbt's SDK downloader; compiling runs inside Flipper's own toolchain (`fbtenv.cmd` + SCons, exactly
what the `ufbt` command does), which ships its own Python, so the exe doesn't need Python installed.
The screen mirror switches the port to Flipper's protobuf RPC (the protocol qFlipper uses) and
streams screen frames and button events; other USB jobs pause it and hand the port back. USB uses
the SDK's own storage client (`scripts/flipper/storage.py`, the code fbt uses); a copy ships
as `flipper_storage.py` for before any SDK is downloaded. App names, versions and icons come from
each `.fap`'s `.fapmeta` manifest (`fapmeta.py`). Discover indexes
[flipper-application-catalog](https://github.com/flipperdevices/flipper-application-catalog) and
caches it for a week.

## Credits

- **Dolphin animations**: from the [Flipper Zero firmware](https://github.com/flipperdevices/flipperzero-firmware)
  (`assets/dolphin/external`), © Flipper Devices Inc., GPL-3.0. `tools/fetch_assets.py` downloads
  and packs them into `ui/vendor/`.
- **Fonts**: [Silkscreen](https://fonts.google.com/specimen/Silkscreen) and
  [Pixelify Sans](https://fonts.google.com/specimen/Pixelify+Sans), SIL Open Font License 1.1
  (license texts in `ui/vendor/fonts/`).
- **ufbt** and the **storage client** (`flipper_storage.py`) by Flipper Devices, GPL-3.0.
- **App catalog index**: Flipper's official Apps Catalog; each app belongs to its author.

Flipper Workbench is an unofficial community tool and is not affiliated with or endorsed by Flipper Devices Inc.
"Flipper Zero" is a trademark of Flipper Devices Inc.

## License

GPL-3.0, same as ufbt and the firmware artwork it bundles.
