"""Keep a folder on this PC and a folder on the Flipper's SD card in step.

Files are compared by size, and by MD5 when the sizes match (the Flipper hashes its own copy), so
nothing is copied that's already the same. Modes:
  to_flipper  PC -> Flipper (new and changed files)
  to_pc       Flipper -> PC
  both        copy what's missing on each side; files that differ on both sides are conflicts the
              user settles one by one
Deleting extras is opt-in. Files deleted on the PC side go to <folder>/.workbench-trash/<time>/,
never straight away.
"""

import hashlib
import os
import shutil
import time
from pathlib import Path

import device

SKIP_NAMES = {".workbench-trash", ".git", "desktop.ini", "thumbs.db", ".ds_store"}


def _local_files(root):
    root = Path(root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_NAMES and not d.startswith(".")]
        for f in filenames:
            if f.lower() in SKIP_NAMES or f.startswith("."):
                continue
            p = Path(dirpath) / f
            out[p.relative_to(root).as_posix()] = p.stat().st_size
    return out


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(port, sdk_dirs, local_root, remote_root, should_stop=lambda: False):
    """{"pc_only", "flipper_only", "differ", "same"}: lists of relative paths (sizes alongside)."""
    remote_root = device._norm(remote_root)
    if not remote_root.startswith("/ext/") or remote_root.rstrip("/") in ("/ext", "/ext/update"):
        raise device.DeviceError("Pick a folder inside the SD card (not the whole card).")
    local = _local_files(local_root)

    def fn(mod, st):
        if not st.exist_dir(remote_root):
            return {}, {}
        remote = {p[len(remote_root) + 1:]: s for p, s in device._walk(st, remote_root)}
        hashes = {}
        for rel in sorted(set(local) & set(remote)):
            if should_stop():
                raise device.DeviceError("Cancelled.")
            if local[rel] == remote[rel]:
                hashes[rel] = st.hash_flipper(f"{remote_root}/{rel}").strip().lower()
        return remote, hashes
    remote, hashes = device.storage_call(port, sdk_dirs, fn, seconds=60 + len(local) * 2)
    same, differ = [], []
    for rel in sorted(set(local) & set(remote)):
        if rel in hashes and hashes[rel] == _md5(Path(local_root) / rel):
            same.append(rel)
        else:
            differ.append(rel)
    return {"pc_only": sorted(set(local) - set(remote)), "flipper_only": sorted(set(remote) - set(local)),
            "differ": differ, "same": same, "local_sizes": local, "remote_sizes": remote}


def plan(diff, mode, delete_extras=False):
    """-> list of (action, rel) with action in upload / download / delete_remote / delete_local / conflict."""
    steps = []
    if mode == "to_flipper":
        steps += [("upload", r) for r in diff["pc_only"] + diff["differ"]]
        if delete_extras:
            steps += [("delete_remote", r) for r in diff["flipper_only"]]
    elif mode == "to_pc":
        steps += [("download", r) for r in diff["flipper_only"] + diff["differ"]]
        if delete_extras:
            steps += [("delete_local", r) for r in diff["pc_only"]]
    else:
        steps += [("upload", r) for r in diff["pc_only"]] + [("download", r) for r in diff["flipper_only"]]
        steps += [("conflict", r) for r in diff["differ"]]
    return steps


def apply(port, sdk_dirs, local_root, remote_root, steps, progress=lambda done, total, name: None,
          should_stop=lambda: False):
    """Run upload/download/delete steps (conflicts must already be turned into upload or download)."""
    local_root, remote_root = Path(local_root), device._norm(remote_root)
    trash = local_root / ".workbench-trash" / time.strftime("%Y-%m-%d_%H%M%S")
    total, done = len(steps) or 1, 0
    with device._exclusive():
        mod, st = device._open_guarded(port, sdk_dirs)
        try:
            ops = mod.FlipperStorageOperations(st)
            for action, rel in steps:
                if should_stop():
                    raise device.DeviceError("Sync stopped. What was copied so far stays.")
                if ".." in rel.split("/") or rel.startswith("/"):
                    raise device.DeviceError(f"Unsafe path: {rel}")
                lp, rp = local_root / rel, f"{remote_root}/{rel}"
                size = lp.stat().st_size if lp.exists() else 0
                with device._Watchdog(st, 30 + size / 4096):
                    if action == "upload":
                        ops.mkpath(rp.rsplit("/", 1)[0])
                        st.send_file(str(lp), rp)
                    elif action == "download":
                        lp.parent.mkdir(parents=True, exist_ok=True)
                        tmp = lp.with_name(lp.name + ".fwpart")
                        tmp.write_bytes(bytes(st.read_file(rp)))
                        os.replace(tmp, lp)
                    elif action == "delete_remote":
                        st.remove(rp)
                    elif action == "delete_local":
                        dst = trash / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(lp), str(dst))
                done += 1
                progress(done, total, rel)
        except mod.FlipperStorageException as e:
            raise device.DeviceError(str(e))
        finally:
            device._safe_stop(st)
    return done
