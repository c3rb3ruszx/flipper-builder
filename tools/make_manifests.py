"""Writes the Scoop and winget manifests for a release from the actual release zip (so the SHA-256 matches).

    python tools/make_manifests.py 1.8.0 path/to/FlipperWorkbench-win64.zip

Scoop: packaging/scoop/flipper-workbench.json works straight from the repo:
    scoop install https://raw.githubusercontent.com/c3rb3ruszx/flipper-workbench/main/packaging/scoop/flipper-workbench.json
winget: packaging/winget/... is what gets submitted as a pull request to microsoft/winget-pkgs.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/c3rb3ruszx/flipper-workbench"
DESC = ("Windows companion for the Flipper Zero: build apps from a GitHub link or the official catalog, install them "
        "over USB, update firmware, manage apps, files and backups, mirror the screen and use the Flipper's CLI.")


def main(version, zip_path):
    sha = hashlib.sha256(Path(zip_path).read_bytes()).hexdigest()
    url = f"{REPO}/releases/download/v{version}/FlipperWorkbench-win64.zip"

    scoop = {
        "version": version,
        "description": DESC,
        "homepage": REPO,
        "license": "GPL-3.0-only",
        "architecture": {"64bit": {"url": url, "hash": sha}},
        "extract_dir": "FlipperWorkbench",
        "bin": [["FlipperWorkbench.exe", "flipper-workbench"]],
        "shortcuts": [["FlipperWorkbench.exe", "Flipper Workbench"]],
        "persist": [".cache", "output", "backups", "recordings", "screenshots", "library.json", "settings.json"],
        "checkver": "github",
        "autoupdate": {"architecture": {"64bit": {
            "url": f"{REPO}/releases/download/v$version/FlipperWorkbench-win64.zip"}}},
    }
    sp = ROOT / "packaging" / "scoop" / "flipper-workbench.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(scoop, indent=2) + "\n", encoding="utf-8", newline="\n")

    pid = "c3rb3ruszx.FlipperWorkbench"
    wd = ROOT / "packaging" / "winget" / "manifests" / "c" / "c3rb3ruszx" / "FlipperWorkbench" / version
    wd.mkdir(parents=True, exist_ok=True)
    head = "# yaml-language-server: $schema=https://aka.ms/winget-manifest.{kind}.1.6.0.schema.json\n\n"
    (wd / f"{pid}.yaml").write_text(head.format(kind="version") + f"""PackageIdentifier: {pid}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.6.0
""", encoding="utf-8", newline="\n")
    (wd / f"{pid}.installer.yaml").write_text(head.format(kind="installer") + f"""PackageIdentifier: {pid}
PackageVersion: {version}
InstallerType: zip
NestedInstallerType: portable
NestedInstallerFiles:
  - RelativeFilePath: FlipperWorkbench\\FlipperWorkbench.exe
    PortableCommandAlias: flipper-workbench
ReleaseDate: {time.strftime('%Y-%m-%d')}
Installers:
  - Architecture: x64
    InstallerUrl: {url}
    InstallerSha256: {sha.upper()}
ManifestType: installer
ManifestVersion: 1.6.0
""", encoding="utf-8", newline="\n")
    (wd / f"{pid}.locale.en-US.yaml").write_text(head.format(kind="defaultLocale") + f"""PackageIdentifier: {pid}
PackageVersion: {version}
PackageLocale: en-US
Publisher: c3rb3ruszx
PublisherUrl: https://github.com/c3rb3ruszx
PackageName: Flipper Workbench
PackageUrl: {REPO}
License: GPL-3.0
LicenseUrl: {REPO}/blob/main/LICENSE
ShortDescription: {json.dumps("Build, install and manage your Flipper Zero's apps from your PC.")}
Description: {json.dumps(DESC)}
Moniker: flipper-workbench
Tags:
  - flipper-zero
  - flipperzero
  - ufbt
  - qflipper
ReleaseNotesUrl: {REPO}/releases/tag/v{version}
ManifestType: defaultLocale
ManifestVersion: 1.6.0
""", encoding="utf-8", newline="\n")
    print("scoop:", sp.relative_to(ROOT))
    print("winget:", wd.relative_to(ROOT))
    print("sha256:", sha)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
