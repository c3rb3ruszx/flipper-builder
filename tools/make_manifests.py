"""Writes the Scoop manifest for a release from the actual release zip (so the SHA-256 matches).

    python tools/make_manifests.py 1.8.0 path/to/FlipperWorkbench-win64.zip

packaging/scoop/flipper-workbench.json works straight from the repo:
    scoop install https://raw.githubusercontent.com/c3rb3ruszx/flipper-workbench/main/packaging/scoop/flipper-workbench.json
"""

import hashlib
import json
import sys
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
    print("scoop:", sp.relative_to(ROOT))
    print("sha256:", sha)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
