# Changelog

Downloads for every version are on the [Releases](../../releases) page.

## 1.6.0

- **Renamed to Flipper Workbench.** It started as a builder and now also manages the apps, files,
  backups and screen of your Flipper, so the name says so. The repo moved to
  `c3rb3ruszx/flipper-workbench`; the old link redirects.
- **CLI**: a terminal for your Flipper's own command line on the On Flipper tab. Output streams
  back live, Up/Down recall earlier commands, Stop or Ctrl+C ends commands that keep running (like
  `log`), and commands that delete, change settings or transmit ask first.
- **Full screen mirror**: while mirroring, click the Flipper screen to fill the whole display with
  it, like qFlipper. Esc, F11 or another click goes back; the buttons and keyboard keep working.
- Upgrading from Flipper Builder: unzip the new release anywhere, close Flipper Builder, then move
  `.cache`, `output`, `backups`, `library.json` and `settings.json` from the old folder into the new
  `FlipperWorkbench` folder and delete the old one. Library entries find their built apps again after
  the move, and backups made by Flipper Builder still restore.
- `install.ps1` (for running from source) migrates an old Flipper Builder install by itself.
- Fix: building one app from a repo no longer deletes the other apps you built earlier from the same
  repo (for example two folders of one collection) from `output`.

## 1.5.0

- **Screen mirror + remote**: the app's LCD shows your Flipper's real screen live; the d-pad and
  keyboard press its buttons (hold for long press), and a camera button saves screenshots.
- **Files**: browse the whole SD card, upload, download, rename, make folders and delete (with confirmation).
- **Backups**: back up chosen SD card folders to a zip on your PC and restore them later. Apps,
  firmware updates and security keys are left out by default.
- Sizes over 1 MB show in MB.

## 1.4.0

- **On Flipper** tab: every app on the Flipper's SD card with its real name, version and icon (read
  from each `.fap`'s manifest and cached), SD card space, and which apps won't open on the current
  firmware. Open, remove (with confirmation) or update any app from your Library.
- The installer waits for a running copy to close before replacing its files.
- Long links no longer cause a horizontal scrollbar.

## 1.3.0

- Detects your Flipper over USB, reads its firmware and API version, and selects the matching SDK.
- *Install to SD* and *Install & open* after building.
- **Library** of every app you've built, with badges and one-click **Rebuild all** after a firmware update.
- **Discover** tab: search and build the 500+ apps in Flipper's official Apps Catalog.
- Checklist picker for repos with several apps (no more 25-app limit).
- Failed builds explain what went wrong in plain English, with "try another firmware" buttons.
- Build from a local folder, with watch mode that rebuilds on every save.
- Notice in the header when a new version is out.

## 1.2.0

- Unleashed dev firmware.
- The firmware picker is a 3x2 grid: each firmware's release on top, its dev build below.

## 1.1.0

- New look: a Flipper-style device whose LCD plays the firmware's own dolphin animations for every
  build stage, with a working d-pad, stage tracker, progress bar and slide-up log.
- Result cards with each app's icon and SD card folder, recent builds, cancel, and drag-and-drop links.
- Momentum dev firmware.

## 1.0.0

- First release: paste a GitHub link, pick Official, Momentum or Unleashed, get a `.fap`.
