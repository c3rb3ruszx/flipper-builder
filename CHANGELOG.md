# Changelog

Downloads for every version are on the [Releases](../../releases) page.

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
