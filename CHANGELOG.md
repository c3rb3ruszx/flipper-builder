# Changelog

Downloads for every version are on the [Releases](../../releases) page.

## 1.13.3

- **Typing in text boxes** works again everywhere: in the BadUSB editor, the IR remote builder, NFC maker, speech
  bubbles, Music, Passport and Profiles, Enter, the arrow keys and Backspace no longer jump to Build, move the d-pad
  or press your Flipper's buttons while the screen mirror is on.

## 1.13.2

- **BadUSB scripts**: the editor's syntax highlighting now shows. It was there all along, hidden under the
  text box since 1.13.0.
- New **[user guide](docs/GUIDE.md)**: every feature explained step by step, with screenshots.

## 1.13.1

- **Health check**: *Shrink* is only offered for frames that really get smaller. Detailed pictures don't
  compress, so Flipper's own packer stores them uncompressed on purpose; 1.13.0 counted those too and offered
  a shrink that saved nothing. The button now shows how much it will save.

## 1.13.0

- **Health check** (On Flipper > Tools): free memory with a live graph, the lowest it dropped since the
  last restart, battery and SD card. *Check my packs* shows how much memory every asset pack and the
  dolphin folder need, their heaviest animations, and shrinks uncompressed frames (from old tools or
  Workbench before 1.11) in place. It never touches the pack your Flipper is using.
- **Studio**: speech bubbles on animations, like the dolphin's own; Line, Box, Circle and Text tools in the
  frame editor, with fill, white ink and *All frames*; choose the mood and levels an animation plays at;
  and a rough preview of a pack's passport and main menu before installing it.
- **IR remote builder**: make one remote from buttons of any remote: the IR database, remotes on your
  Flipper, learned from a real remote, or typed in. Rename, reorder and test each button, then save it to
  the Flipper's Infrared app or to your PC.
- **BadUSB scripts**: edit the scripts on your Flipper, subfolders included, with highlighting and a checker
  that flags commands and keys the BadUSB app doesn't know, and pick the keyboard layout it uses.
- **Passport**: change your Flipper's name (Momentum) with a preview, and jump to the passport pictures.
- **Profiles**: save your Flipper's setup (Momentum settings, name, app list and optionally the apps) and
  switch between setups. Apps are only removed if you tick that box.
- **Record as MP4**: a setting in Tools makes the record button save a video instead of a GIF.
- **Update all** now includes app updates from Flipper's catalog, built and installed one after another.

## 1.12.0

- **Studio**: a new tab under On Flipper with everything for your Flipper's look in one place: Animation
  maker, Pack maker and the pack library, next to your live Flipper screen.
- **Video in**: the Animation maker now takes MP4, WebM, MOV, MKV and AVI as well as GIFs and pictures.
  Pick or drop a file, then trim it with Start and End.
- **More control**: frame rate up to 30 fps, max frames up to 255, threshold, contrast, dither, Fill, Fit or
  Stretch, zoom and move for framing, invert, and a Background option (Auto flips dark footage so its
  background comes out empty, with no speckles).
- **Frame editor**: a strip of every frame with play, pause and step. Draw and erase pixels, duplicate,
  reorder or remove frames, with undo. Drawings stay when you change the look.
- **Show on my Flipper**: play the animation live on your Flipper's real screen before installing it.
- Choose where an animation goes: an asset pack or the dolphin folder.

## 1.11.0

- **Pack maker** (On Flipper > Tools): make your own Momentum asset pack. Add desktop animations from
  GIFs or pictures, passport art, any of Flipper's 288 icons (animated ones too) and fonts, with a
  black-and-white preview of each. Built-in tips explain how packs work and how to get a good result.
- **Import asset packs** from a zip or a folder: ready-made packs, PNG source packs and plain dolphin
  folders. Every file is checked the way the firmware reads it; anything broken is left out and listed.
- A memory bar shows how much of your Flipper's memory a pack needs, and warns before it gets risky.
- Install in one click, save as a zip to share, and see or remove the packs on your Flipper. Workbench
  won't replace or remove the pack your Flipper is using.
- Animation maker now compresses frames the way Flipper's own packer does. Animations take far less of
  your Flipper's memory while they play, and the maker shows how much they need.

## 1.10.0

- **NFC maker** (On Flipper > Tools): build an NFC tag from scratch. Choose a link, text, Wi-Fi network,
  contact card, phone call, text message, email, map location, Android app or Bluetooth pairing, fill in
  the fields and Workbench writes an NTAG213, NTAG215 or NTAG216 `.nfc` file in Flipper's own format.
  Put several records on one tag; a capacity meter shows how much room is left and suggests a bigger
  tag when it doesn't fit. *Save to Flipper*, *Save & emulate* (opens it in the NFC app right away) or
  *Save to PC*. A tag with the same name is only replaced after you confirm.
- Saving a tag waits until no app is open on the Flipper (writing under a running NFC app can restart
  it), and says so instead.
- Fix: opening a file in an app from Workbench passed the path in quotes, which the NFC app could not
  read ("Cannot load key file").

## 1.9.2

- Fix: with the screen mirrored (now on by itself in 1.9.1), opening a second folder, the SD map or
  anything else that talks to the Flipper right after another job could wait forever on "Reading...".
  The mirror was still restarting from the previous job and kept the USB port. Jobs now wait for it
  properly and the screen comes back about a second later.
- Fix: the IR viewer's *Edit buttons* no longer shows up on the Firmware, Clean up and new-file sheets.
- README: animated demos of building, mirroring and Discover, and fresh screenshots.

## 1.9.1

- **Your Flipper's screen appears by itself** when you plug it in: no more Mirror button. A small screen
  icon under the display pauses it (and gives the d-pad back to Workbench) until you plug in again;
  switch auto-mirroring off in Tools if you prefer the dolphin.
- The Bluetooth button is Flipper orange, and the device LED turns blue while Bluetooth is connected,
  like a real Flipper. Reconnecting to a paired Flipper takes about 2 seconds, even when Windows has
  already connected it.
- **Bluetooth button under the screen**, next to Mirror my Flipper: one click connects (the first time it
  scans and pairs; after that it reconnects to your Flipper in about 2 seconds). It glows blue while
  connected and turns into Disconnect.
- **Bluetooth pairing inside Workbench**: the first connection asks for the PIN your Flipper shows,
  in Workbench itself; no Windows Settings needed. Tested: mirror over Bluetooth runs at about 8 frames
  a second.
- Bluetooth scan listens up to 15 seconds (Flippers advertise slowly) and stops as soon as it hears one.
- Connecting finds the Flipper by a fresh scan (more reliable on Windows), with progress on the
  mirror button, and the header shows "Bluetooth · mirror only" while mirroring wirelessly.

## 1.9.0

- **App dev kit**: Tools > New app creates a project from a template (hello screen, menu app, LED
  blinker, JavaScript app); one click builds, installs and watches it. CLI > Follow log streams the
  Flipper's log filtered to your app, coloured by level.
- **SD card map**: treemap of the SD card by folder, drill-down, open in Files, space by file type.
- **NFC**: compare two dumps byte by byte; manage your own MIFARE Classic keys.
- **Framed screenshots and GIFs**: optionally inside a drawn Flipper.
- **Wi-Fi dev board flasher**: official Black Magic firmware (checksum-verified from Flipper's
  server) or any compatible file you drop in; every part is checked to be an ESP32-S2 image first.
- **Phone remote**: screen + d-pad on your phone over home Wi-Fi (off by default, private network
  only, secret link, QR code).
- **Several Flippers**: pick which one to use from the header.
- **Bluetooth** (experimental): mirror over BLE.
- `tools/release.ps1`: version bump, build, zip, Scoop manifest, privacy check, release in one go.

## 1.8.0

- **Updates itself**: one click downloads the new release, checks it against GitHub's SHA-256 and
  restarts into it; only the program files change, and a failed swap puts the old version back.
- **IR remote library**: Discover > IR remotes searches the 8,900 remotes of Flipper-IRDB; preview
  and try buttons before installing (never overwrites a remote you have).
- **IR remote editing**: rename, reorder, remove buttons and merge another remote's buttons in.
- **Tools** (On Flipper > Tools):
  - *Music*: MIDI and RTTTL ringtones to Music Player songs, with an in-app preview.
  - *Animation maker*: GIFs and pictures to desktop animations (dithering, brightness, contrast,
    speed, live preview); Momentum gets a Workbench asset pack, other firmware `dolphin/`.
  - *Asset packs*: install Momentum's community packs (checksum-verified).
  - *Folder sync*: PC folder <-> SD card folder, one way or both, MD5-compared, every change shown
    first, conflicts settled by you, deletions opt-in.
  - *Companion app*: a small Flipper app (source in `companion/`) built for your firmware, showing
    what Workbench last did.
- **Scoop** install from the repo.

## 1.7.0

- **Firmware updates**: Official, Momentum and Unleashed, release or dev. Two steps: *Prepare*
  (optional SD backup, download, SHA-256 check, copy to the Flipper with every file MD5-checked)
  and *Install* after a second confirm, through the Flipper's own updater like qFlipper. Checks the
  battery (50%+) and free space first, warns when switching firmware family, waits for the Flipper
  to come back on the new version, then offers to rebuild your apps for it. A package that was
  already on the SD card is left alone if you decide not to install.
- **Capture viewer**: click a `.sub`, `.ir`, `.nfc`, `.rfid` or `.ibtn` file in Files to read it in
  plain language: Sub-GHz frequency, modulation and a drawing of the signal; every IR button, with
  *Send*; NFC type, UID and blocks; RFID and iButton keys.
- **Script editor**: edit BadUSB and JavaScript files with syntax highlighting, *New file*, save to
  the SD card (Ctrl+S; written to a temp file first, then swapped in), and *Save & run* for JS apps.
- **GIF recording** of the mirrored screen (up to 60 s; repeated frames merged, Flipper colours).
- **Auto backup** when you plug in your Flipper: once a day, skipped when nothing changed since the
  last one (same files and sizes), the last 5 kept, your own backups never deleted. On by default;
  switch it off in Backups.
- **Catalog updates**: apps on your Flipper with a newer release in Flipper's official catalog get
  a badge and a one-click update (built for your firmware, installed over the old one). Apps that
  ship with your firmware (read from its official package) are left to firmware updates.
- **Clean up**: duplicate apps, apps that won't open on your firmware, old update packages and
  leftover temp files. Suggestions only; you tick what to delete and confirm.
- **macOS**: the code now runs builds the macOS way and keeps data in Application Support, and
  `tools/build_mac.sh` makes an `.app`. Untested so far.

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
