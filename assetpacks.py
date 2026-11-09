"""Momentum asset packs (animations, icons, passport art) from Momentum's own pack directory.

Each pack comes as a zip with a SHA-256 in the directory. Installing = unpacking it and copying its
folder(s) to /ext/asset_packs/ on the SD card; the pack is then picked on the Flipper itself
(Momentum Settings > Interface > Graphics > Asset pack). Nothing on the Flipper is switched for you.
"""

import hashlib
import json
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

DIRECTORY = "https://up.momentum-fw.dev/asset-packs/directory.json"
MAX_AGE = 24 * 3600
UA = {"User-Agent": "flipper-workbench"}


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def load(cache_dir, force=False):
    cache = Path(cache_dir) / "asset_packs.json"
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < MAX_AGE:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    d = json.loads(_get(DIRECTORY))
    packs = []
    for p in d.get("packs") or []:
        z = next((f for f in p.get("files") or [] if f.get("type") == "pack_zip" and f.get("sha256")), None)
        if not z:
            continue
        st = p.get("stats") or {}
        packs.append({"id": p.get("id"), "name": p.get("name"), "author": p.get("author"),
                      "description": p.get("description") or "", "source": p.get("source_url"),
                      "zip": z["url"], "sha256": z["sha256"].lower(), "previews": (p.get("preview_urls") or [])[:4],
                      "anims": st.get("anims", 0), "icons": st.get("icons", 0), "folders": st.get("folders") or [],
                      "updated": st.get("updated")})
    data = {"fetched": int(time.time()), "packs": packs}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def download(pack, work_dir):
    """Download + SHA-256 check + safe unzip. Returns the pack folders (each goes to /ext/asset_packs/<name>)."""
    work_dir = Path(work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    data = _get(pack["zip"], timeout=120)
    if hashlib.sha256(data).hexdigest() != pack["sha256"]:
        raise ValueError("The download doesn't match the checksum in Momentum's directory.")
    zp = work_dir / "pack.zip"
    zp.write_bytes(data)
    out = work_dir / "unpacked"
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                raise ValueError(f"Unsafe path in the pack: {n}")
        z.extractall(out)
    folders = [d for d in out.iterdir() if d.is_dir()]
    if not folders:
        raise ValueError("The pack zip is empty.")
    return folders
