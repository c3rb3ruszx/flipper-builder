"""Flipper-IRDB (github.com/Lucaslhm/Flipper-IRDB): thousands of community IR remotes as Flipper .ir files.

One tree API call lists every file; it's cached for a week. Single remotes come from
raw.githubusercontent.com when someone opens or installs one.
"""

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "Lucaslhm/Flipper-IRDB"
TREE_URL = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
MAX_AGE = 7 * 24 * 3600
UA = {"User-Agent": "flipper-workbench"}


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def load(cache_dir, force=False):
    """{"fetched", "count", "items": [[path, size], ...]} sorted by path."""
    cache = Path(cache_dir) / "irdb.json"
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < MAX_AGE:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    tree = json.loads(_get(TREE_URL))
    items = sorted([t["path"], t.get("size", 0)] for t in tree.get("tree", [])
                   if t.get("type") == "blob" and t["path"].lower().endswith(".ir"))
    data = {"fetched": int(time.time()), "count": len(items), "items": items}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def fetch(path):
    """The text of one remote. Only paths that look like IRDB .ir files are accepted."""
    if not path.lower().endswith(".ir") or ".." in path.split("/") or path.startswith("/"):
        raise ValueError("Not an IRDB remote.")
    data = _get(RAW + urllib.parse.quote(path), timeout=30)
    if len(data) > 512 * 1024:
        raise ValueError("That file is unusually large for a remote.")
    return data.decode("utf-8", "replace")
