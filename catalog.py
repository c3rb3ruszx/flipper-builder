"""Searchable index of the official Flipper Apps Catalog (github.com/flipperdevices/flipper-application-catalog).

Every app there has applications/<Category>/<id>/manifest.yml pointing at its source: a git origin,
a pinned commit (the version Flipper reviewed) and a subfolder. We list the tree in one API call,
fetch the small manifests in parallel from raw.githubusercontent.com, and cache the result for a week.
"""

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "flipperdevices/flipper-application-catalog"
TREE_URL = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
MAX_AGE = 7 * 24 * 3600
UA = {"User-Agent": "flipper-builder"}


def _get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _field(text, key):
    m = re.search(rf"^\s*{key}:\s*['\"]?([^'\"\n#]+?)['\"]?\s*$", text, re.M)
    return m.group(1).strip() if m else None


def _pretty(app_id):
    words = re.split(r"[_\-]+", app_id)
    return " ".join(w if w.isupper() or len(w) <= 2 and w.isupper() else w.capitalize() for w in words if w)


def _entry(path):
    _, category, app_id, _ = path.split("/")
    try:
        text = _get(RAW + path, timeout=20).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    origin = _field(text, "origin")
    sha = _field(text, "commit_sha")
    subdir = _field(text, "subdir") or ""
    if not origin:
        return None
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", origin)
    if not m:
        return None  # only GitHub sources can be built from a link
    owner, repo = m.group(1), m.group(2)
    url = f"https://github.com/{owner}/{repo}"
    if sha:
        url += f"/tree/{sha}" + (f"/{subdir.strip('/')}" if subdir else "")
    return {"id": app_id, "name": _pretty(app_id), "category": category, "repo": f"{owner}/{repo}",
            "url": url, "subdir": subdir}


def load(cache_dir, force=False, log=lambda *_: None):
    cache = Path(cache_dir) / "catalog.json"
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < MAX_AGE:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    log("Fetching the Flipper Apps Catalog index...")
    tree = json.loads(_get(TREE_URL))
    paths = [t["path"] for t in tree.get("tree", [])
             if re.fullmatch(r"applications/[^/]+/[^/]+/manifest\.yml", t["path"])]
    with ThreadPoolExecutor(max_workers=16) as pool:
        apps = [a for a in pool.map(_entry, paths) if a]
    apps.sort(key=lambda a: (a["category"], a["name"].lower()))
    data = {"fetched": int(time.time()), "count": len(apps), "apps": apps}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data
