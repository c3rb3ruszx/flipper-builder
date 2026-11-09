"""Firmware updates for the Flipper: the official update packages, checked before they go anywhere near it.

The update itself is the Flipper's own: the package goes to /ext/update/<name>/ and `update install`
runs it, exactly like qFlipper and the SDK's scripts/selfupdate.py. The Flipper verifies the package
again before it restarts into its updater, and its DFU recovery mode stays available whatever happens.
"""

import hashlib
import json
import shutil
import tarfile
import time
import urllib.request
from pathlib import Path

INDEXES = {
    "official": "https://update.flipperzero.one/firmware/directory.json",
    "momentum": "https://up.momentum-fw.dev/firmware/directory.json",
    "unleashed": "https://up.unleashedflip.com/directory.json",
}
LABELS = {"official": "Official", "momentum": "Momentum", "unleashed": "Unleashed"}
# app firmware ids (flipper_workbench.FIRMWARES) -> (family, channel id in directory.json)
TARGETS = {
    "official": ("official", "release"), "official-dev": ("official", "development"),
    "momentum": ("momentum", "release"), "momentum-dev": ("momentum", "development"),
    "unleashed": ("unleashed", "release"), "unleashed-dev": ("unleashed", "development"),
}
UA = {"User-Agent": "flipper-workbench"}


def _get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def available():
    """Latest update package per firmware id: version, date, url, sha256, changelog. Best effort per index."""
    out, errors = {}, {}
    for family, url in INDEXES.items():
        try:
            d = json.loads(_get(url))
        except Exception as e:  # noqa: BLE001
            errors[family] = str(e)
            continue
        for ch in d.get("channels") or []:
            for fw_id, (fam, chan) in TARGETS.items():
                if fam != family or ch.get("id") != chan:
                    continue
                for v in ch.get("versions") or []:
                    files = [f for f in v.get("files") or [] if f.get("target") == "f7" and f.get("type") == "update_tgz"]
                    if files and files[0].get("sha256"):
                        out[fw_id] = {"id": fw_id, "family": LABELS[family], "channel": "dev" if chan == "development" else "release",
                                      "version": str(v.get("version")), "timestamp": v.get("timestamp"),
                                      "url": files[0]["url"], "sha256": files[0]["sha256"].lower(),
                                      "changelog": (v.get("changelog") or "")[:6000]}
                        break
    return {"targets": out, "errors": errors}


def download(target, cache_dir, progress=lambda done, total: None, should_stop=lambda: False):
    """Download the package (kept in cache_dir) and check its SHA-256 against the index. Returns the path."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = target["url"].rsplit("/", 1)[-1]
    if not name.endswith(".tgz") or "/" in name or ".." in name:
        raise ValueError("Unexpected package name in the firmware index.")
    dest = cache_dir / name
    if dest.exists() and _sha256(dest) == target["sha256"]:
        return dest
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(target["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if should_stop():
                raise InterruptedError("Cancelled.")
            chunk = r.read(65536)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            progress(done, total or done)
    if _sha256(tmp) != target["sha256"]:
        tmp.unlink(missing_ok=True)
        raise ValueError("The download doesn't match the checksum in the firmware index, so it was thrown away. Try again later.")
    tmp.replace(dest)
    return dest


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(tgz, work_dir):
    """Unpack safely (no absolute paths, no '..', no links) and return the folder holding update.fuf."""
    work_dir = Path(work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    with tarfile.open(tgz, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            parts = Path(m.name).parts
            if m.name.startswith(("/", "\\")) or ".." in parts or m.issym() or m.islnk() or m.isdev():
                raise ValueError(f"Unsafe entry in the update package: {m.name}")
        tf.extractall(work_dir, members=members)
    fufs = list(work_dir.rglob("update.fuf"))
    if len(fufs) != 1:
        raise ValueError("The package doesn't contain exactly one update.fuf, so it isn't a normal update.")
    pkg = fufs[0].parent
    if pkg.parent != work_dir:
        raise ValueError("Unexpected layout inside the update package.")
    return pkg


def bundled_apps(tgz):
    """File names (lowercase) of every .fap the firmware package installs: its resources.tar.gz listing."""
    import io
    with tarfile.open(tgz, "r:gz") as outer:
        res = next((m for m in outer.getmembers() if m.name.endswith("resources.tar.gz") and m.isfile()), None)
        if res is None:
            return []
        data = outer.extractfile(res).read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as inner:
        return sorted({Path(m.name).name.lower() for m in inner.getmembers() if m.name.lower().endswith(".fap")})


def matches(target, dev):
    """Is this package the build the Flipper runs?"""
    v = str(target.get("version") or "")
    ver, commit = str(dev.get("version") or ""), str(dev.get("commit") or "")
    return bool(v) and (v == ver or v == commit or (commit and (v.startswith(commit) or commit.startswith(v))))


def describe_age(ts):
    if not ts:
        return ""
    days = int((time.time() - int(ts)) // 86400)
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"
