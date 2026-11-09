"""Flipper Workbench window: a pywebview (WebView2) shell around ui/index.html.

Python -> JS: batched events via window.fb.onEvents([...]).
JS -> Python: the public methods of Api (pywebview's js_api bridge).
Background threads: event pump, Flipper USB monitor, local-folder watcher, update check.
"""

import ctypes
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import flipper_workbench as fb

MAX_RECENT = 8
AFTER_MODES = ("none", "install", "open")   # what happens after a build: nothing / copy to SD / copy + open


# ---------------------------------------------------------------- clipboard (Win32)

def _clip_get():
    if os.name != "nt":
        return ""
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return ""
    try:
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _clip_set(text):
    if os.name != "nt":
        return False
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u32.OpenClipboard(None):
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0042, len(data))  # GHND
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        u32.SetClipboardData(13, h)
        return True
    finally:
        u32.CloseClipboard()


def _github_link(text):
    text = (text or "").strip()
    if "github.com" in text and "\n" not in text and len(text) < 500:
        return text
    return ""


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3])


# ---------------------------------------------------------------- API exposed to JS

class Api:
    def __init__(self):
        self._window = None
        self._events = queue.Queue()
        self._job = None
        self._lock = threading.Lock()
        self._select_answer = None
        self._select_event = threading.Event()
        self._device = {"connected": False}
        self._device_refresh = threading.Event()
        self._watch = {"path": None, "snap": None}
        self._pending_changes = []
        threading.Thread(target=self._pump, daemon=True).start()

    # ------------------------------------------------ plumbing
    def _emit(self, kind, **data):
        data["type"] = kind
        self._events.put(data)

    def _pump(self):
        """Flush queued events to the page ~12x a second, in one JS call per batch."""
        while True:
            batch = [self._events.get()]
            time.sleep(0.08)
            while not self._events.empty() and len(batch) < 400:
                batch.append(self._events.get_nowait())
            if self._window is None:
                continue
            try:
                self._window.evaluate_js(f"window.fb && fb.onEvents({json.dumps(batch)})")
            except Exception:  # noqa: BLE001  (window closing)
                pass

    def _ask_select(self, apps):
        """Called from the build thread: show the picker and wait for the answer."""
        self._select_answer = None
        self._select_event.clear()
        self._emit("select", apps=apps)
        while not self._select_event.wait(0.25):
            if self._job and self._job.cancelled.is_set():
                return []
        return self._select_answer

    def _run_job(self, label, fn):
        job = fb.Job(
            log=lambda line: self._emit("log", line=line),
            stage=lambda name, detail="": self._emit("stage", stage=name, detail=detail),
            progress=lambda v: self._emit("progress", value=v),
            result=lambda res: self._emit("result", result=res),
            select=self._ask_select,
        )
        with self._lock:
            if self._job:
                return {"error": "Already working on something."}
            self._job = job

        def worker():
            try:
                payload = fn(job) or {}
                self._emit("finished", ok=True, **payload)
            except fb.Cancelled as e:
                self._emit("log", line=f"!! {e}")
                self._emit("finished", ok=False, cancelled=True, error=str(e))
            except fb.BuildError as e:
                self._emit("log", line="!! " + str(e))
                self._emit("finished", ok=False, error=str(e), hints=fb.hints(job.lines, label_fw[0]))
            except Exception as e:  # noqa: BLE001
                self._emit("log", line=f"!! Unexpected error: {e!r}")
                self._emit("finished", ok=False, error="Unexpected error (see log).")
            finally:
                self._job = None
                self._device_refresh.set()   # the port may have been busy; re-read the Flipper
        label_fw = [label]
        threading.Thread(target=worker, daemon=True, name=label).start()
        return {"ok": True}

    def _build_payload(self, results, out, fw, job, source=None, repo=None):
        ok = sum(r["ok"] for r in results)
        payload = {"built": ok, "total": len(results), "out": str(out), "sdk": fb.sdk_version(fw),
                   "api": fb.sdk_api(fw), "fw": fw, "install_error": getattr(job, "install_error", None),
                   "installed": any(r.get("installed") or r.get("sent") for r in results)}
        failed = [r for r in results if not r["ok"]]
        if failed:
            payload["hints"] = failed[0].get("hints") or fb.hints(job.lines, fw)
        if source:
            recent = [r for r in fb.load_settings().get("recent", []) if r.get("url") != source]
            recent.insert(0, {"url": source, "fw": fw, "repo": repo or source,
                              "name": ", ".join(r["name"] for r in results)[:60] or repo or source,
                              "ok": ok == len(results), "t": int(time.time())})
            fb.save_settings(recent=recent[:MAX_RECENT])
            payload["recent"] = recent[:MAX_RECENT]
        return payload

    # ------------------------------------------------ init / prefs
    def init(self):
        s = fb.load_settings()
        after = s.get("after") or ("open" if s.get("send") else "none")
        return {
            "version": fb.APP_VERSION,
            "repo": fb.REPO_URL,
            "firmwares": [{"id": k, "label": v[0], "short": v[1], "sdk": fb.sdk_version(k), "api": fb.sdk_api(k)}
                          for k, v in fb.FIRMWARES.items()],
            "fw": s.get("fw", "official") if s.get("fw") in fb.FIRMWARES else "official",
            "after": after if after in AFTER_MODES else "none",
            "auto_fw": s.get("auto_fw", True),
            "recent": s.get("recent", []),
            "clipboard": _github_link(_clip_get()),
            "out_dir": str(fb.OUT_DIR),
            "device": self._device,
            "library_count": len(fb.load_library()),
        }

    def clipboard(self):
        return _github_link(_clip_get())

    def set_prefs(self, fw=None, after=None, auto_fw=None):
        kw = {}
        if fw in fb.FIRMWARES:
            kw["fw"] = fw
        if after in AFTER_MODES:
            kw["after"] = after
        if auto_fw is not None:
            kw["auto_fw"] = bool(auto_fw)
        fb.save_settings(**kw)
        return True

    # ------------------------------------------------ building
    def build(self, source, fw, after="none"):
        source = (source or "").strip().strip('"')
        local = fb.is_local(source)
        if not local:
            try:
                owner, repo, _ = fb.parse_github_url(source)
            except fb.BuildError as e:
                return {"error": str(e) + " (A local folder path works too.)"}
            repo_label = f"{owner}/{repo}"
        else:
            repo_label = Path(source).name
        if fw not in fb.FIRMWARES:
            return {"error": "Unknown firmware."}
        fb.save_settings(fw=fw, after=after)

        def work(job):
            results, out = fb.build(source, fw, send=(after == "open"), job=job, install=(after == "install"))
            return self._build_payload(results, out, fw, job, source, repo_label)
        return self._run_job(fw, work)

    def answer_select(self, appids):
        self._select_answer = appids  # None = all
        self._select_event.set()
        return True

    def rebuild(self, keys, fw, after="none"):
        """Rebuild library entries (all, or the given keys) for firmware fw."""
        items = fb.load_library()
        if keys:
            items = [i for i in items if i["key"] in set(keys)]
        groups = {}
        for i in items:
            groups.setdefault(i["source"], set()).add(i["appid"])
        if not groups:
            return {"error": "Your library is empty. Build something first."}

        def work(job):
            all_results, done, failed_sources = [], 0, []
            for n, (source, appids) in enumerate(groups.items(), 1):
                job.check()
                job.log(f"\n######## {n}/{len(groups)}: {source}")
                self._emit("batch", index=n, total=len(groups), source=source)
                try:
                    results, out = fb.build(source, fw, job=job, only=sorted(appids))
                    all_results += results
                    done += sum(r["ok"] for r in results)
                except fb.Cancelled:
                    raise
                except fb.BuildError as e:
                    job.log(f"!! {source}: {e}")
                    failed_sources.append(source)
            good = [r for r in all_results if r["ok"]]
            if after in ("install", "open") and good:
                job.stage("send", f"{len(good)} app(s)")
                try:
                    fb.install_to_flipper(job, [(f["path"], r["category"]) for r in good for f in r["files"]])
                    for r in good:
                        r["installed"] = True
                except fb.BuildError as e:
                    job.log("!! " + str(e))
                    job.install_error = str(e)
            out = fb.OUT_DIR
            payload = self._build_payload(all_results, out, fw, job)
            payload.update({"rebuild": True, "failed_sources": failed_sources,
                            "total": len(all_results) + len(failed_sources)})
            return payload
        return self._run_job(fw, work)

    def update_sdk(self, fw):
        def work(job):
            job.stage("sdk", fb.FIRMWARES[fw][1])
            fb.ensure_sdk(job, fw, force=True)
            job.log(f"OK {fb.FIRMWARES[fw][0]} SDK is now {fb.sdk_version(fw)} (API {fb.sdk_api(fw)})")
            return {"sdk_only": True, "sdk": fb.sdk_version(fw), "api": fb.sdk_api(fw), "fw": fw}
        return self._run_job(fw, work)

    def send(self, app_dir, fw):
        if not Path(app_dir).exists():
            return {"error": "That build's source is gone (another build replaced it). Build it again."}

        def work(job):
            job.stage("send", Path(app_dir).name)
            fb.send_to_flipper(job, app_dir, fw)
            job.log("OK Sent. It should be open on your Flipper now.")
            return {"send_only": True}
        return self._run_job(fw, work)

    def install(self, files):
        """files: [[local_path, category], ...] -> copy to the SD card."""
        files = [(p, c) for p, c in files if Path(p).is_file()]
        if not files:
            return {"error": "Those files are gone. Rebuild the app first."}

        def work(job):
            job.stage("send", f"{len(files)} file(s)")
            fb.install_to_flipper(job, files)
            return {"send_only": True, "installed": True}
        return self._run_job("install", work)

    def cancel(self):
        job = self._job
        if job:
            job.cancel()
            self._select_event.set()
        return True

    # ------------------------------------------------ library
    def library(self):
        return {"items": fb.load_library(),
                "sdk": {k: {"version": fb.sdk_version(k), "api": fb.sdk_api(k)} for k in fb.FIRMWARES}}

    def library_remove(self, key, delete_file=True):
        items = fb.load_library()
        gone = [i for i in items if i["key"] == key]
        fb.save_library([i for i in items if i["key"] != key])
        if delete_file:
            for i in gone:
                for f in i.get("files", []):
                    still_used = any(f["path"] == g["file"] for g in fb.load_library())
                    if not still_used:
                        try:
                            Path(f["path"]).unlink(missing_ok=True)
                        except OSError:
                            pass
        return True

    # ------------------------------------------------ discover
    def catalog(self, force=False):
        import catalog
        try:
            return catalog.load(fb.ROOT / ".cache", force=bool(force))
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't load the app catalog: {e}"}

    # ------------------------------------------------ local folders + watch
    def pick_folder(self):
        import webview
        try:
            res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        except AttributeError:
            res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else ""

    def set_watch(self, path):
        """Watch a local folder: emits a 'watch' event when sources change. path '' stops watching."""
        path = (path or "").strip().strip('"')
        if path and not fb.is_local(path):
            return {"error": "Watching only works for a folder on this PC."}
        self._watch = {"path": path or None, "snap": self._snapshot(path) if path else None}
        return {"ok": True, "path": path}

    @staticmethod
    def _snapshot(path):
        snap = {}
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in fb.SKIP_DIRS]
            for f in filenames:
                if f.endswith((".c", ".h", ".cpp", ".hpp", ".fam", ".png", ".txt", ".fmf", ".json")):
                    p = os.path.join(dirpath, f)
                    try:
                        snap[p] = os.stat(p).st_mtime_ns
                    except OSError:
                        pass
        return snap

    def _watcher(self):
        pending_since = None
        while True:
            time.sleep(1)
            w = self._watch
            if not w["path"]:
                pending_since = None
                continue
            try:
                snap = self._snapshot(w["path"])
            except OSError:
                continue
            if snap != w["snap"]:
                changed = [Path(p).name for p in set(snap) ^ set(w["snap"] or {})] + \
                          [Path(p).name for p in snap if (w["snap"] or {}).get(p, snap[p]) != snap[p]]
                w["snap"] = snap
                pending_since = time.time()
                self._pending_changes = changed
            if pending_since and time.time() - pending_since > 0.8 and not self._job:
                pending_since = None
                self._emit("watch", path=w["path"], changed=sorted(set(self._pending_changes))[:6])

    # ------------------------------------------------ Flipper over USB
    def refresh_device(self):
        self._device_refresh.set()
        return self._device

    def _device_monitor(self):
        import device
        known = None
        while True:
            self._device_refresh.wait(2)
            forced = self._device_refresh.is_set()
            self._device_refresh.clear()
            try:
                ports = device.find_ports()
            except Exception:  # noqa: BLE001
                ports = []
            key = tuple(sorted(p["port"] for p in ports))
            if key == known and not forced:
                continue
            if self._job and key == known:
                continue   # don't poke the port while a job may be using it
            known = key
            if not ports:
                self._device = {"connected": False}
            elif len(ports) > 1:
                self._device = {"connected": True, "error": "More than one Flipper is plugged in."}
            else:
                p = ports[0]
                time.sleep(0.6)  # let the USB serial settle after plug-in
                try:
                    info = device.read_info(p["port"], fb.sdk_dirs())
                    self._device = {"connected": True, **device.summary(p["port"], p["name"], info)}
                except Exception as e:  # noqa: BLE001
                    self._device = {"connected": True, "port": p["port"], "name": p["name"] or "Flipper",
                                    "error": str(e)}
            self._emit("device", device=self._device)

    # ------------------------------------------------ what's on the Flipper
    def _port(self):
        import device
        ports = device.find_ports()
        if not ports:
            raise fb.BuildError("No Flipper found on USB. Plug it in, unlock it, and close qFlipper.")
        if len(ports) > 1:
            raise fb.BuildError("More than one Flipper is plugged in. Leave just one connected.")
        return ports[0]["port"]

    _META_CACHE = fb.ROOT / ".cache" / "flipper_meta.json"

    def _meta_cache(self):
        try:
            return json.loads(self._META_CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def flipper_list(self):
        """Installed apps + SD space + any cached manifests (keyed path|size)."""
        import device
        try:
            port = self._port()
            r = device.list_apps(port, fb.sdk_dirs())
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        cache = self._meta_cache()
        for a in r["apps"]:
            a["meta"] = cache.get(f"{a['path']}|{a['size']}")
        return r

    def flipper_check(self, apps):
        """Read the manifest of each app (list of {path,size}) in the background; emits 'fmeta' events."""
        if getattr(self, "_checking", False):
            return {"error": "Already reading apps from the Flipper."}
        self._checking = True

        def work():
            import device
            import fapmeta
            cache = self._meta_cache()
            done = [0]
            todo = []
            for a in apps:   # cached ones first, instantly
                meta = cache.get(f"{a['path']}|{a['size']}")
                if meta is None:
                    todo.append(a)
                else:
                    done[0] += 1
                    self._emit("fmeta", path=a["path"], meta=meta, done=done[0], total=len(apps))

            def on_file(a, data):
                meta = fapmeta.parse(data) or {}
                cache[f"{a['path']}|{a['size']}"] = meta
                done[0] += 1
                if done[0] % 10 == 0:
                    self._save_meta(cache)
                self._emit("fmeta", path=a["path"], meta=meta, done=done[0], total=len(apps))
            try:
                if todo:
                    device.read_many(self._port(), fb.sdk_dirs(), todo, on_file,
                                     should_stop=lambda: getattr(self, "_check_stop", False))
                self._save_meta(cache)
                self._emit("fcheck_done", done=done[0])
            except (fb.BuildError, device.DeviceError) as e:
                self._save_meta(cache)
                self._emit("fcheck_done", error=str(e), done=done[0])
            finally:
                self._checking = False
                self._check_stop = False
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def flipper_check_stop(self):
        self._check_stop = True
        return True

    def _save_meta(self, cache):
        try:
            self._META_CACHE.parent.mkdir(parents=True, exist_ok=True)
            self._META_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass

    def flipper_open(self, path):
        import device
        try:
            device.open_app(self._port(), fb.sdk_dirs(), path)
            return {"ok": True}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def flipper_remove(self, path):
        """Delete one app from the Flipper's SD card (the UI asks the user to confirm first)."""
        import device
        if not (path.startswith("/ext/apps/") and path.lower().endswith(".fap")):
            return {"error": "Only .fap apps under /ext/apps can be removed here."}
        try:
            port = self._port()
            device.remove(port, fb.sdk_dirs(), path)
            gone = not device.file_exists(port, fb.sdk_dirs(), path)
            return {"ok": gone} if gone else {"error": "The Flipper didn't delete it."}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def flipper_update(self, items):
        """Replace installed apps with library builds, each at its existing path. items: [[path, local_file], ...]"""
        pairs = [(local, path) for path, local in items if Path(local).is_file()
                 and path.startswith("/ext/apps/") and path.lower().endswith(".fap")]
        if not pairs:
            return {"error": "Those builds' files are gone. Rebuild them first."}

        def work(job):
            import device
            job.stage("send", f"{len(pairs)} app(s)")
            try:
                device.install_files(self._port(), fb.sdk_dirs(), pairs, log=job.log)
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            return {"send_only": True, "installed": True, "flipper_changed": True, "updated": len(pairs)}
        return self._run_job("install", work)

    # ------------------------------------------------ file manager
    def _fprog(self, label):
        """Progress callback for file jobs, throttled to ~8 updates a second."""
        last = [0.0]

        def cb(done, total, name=""):
            now = time.time()
            if now - last[0] > 0.12 or done >= total:
                last[0] = now
                self._emit("fprog", label=label, done=done, total=total, name=name)
        return cb

    def files_list(self, path):
        import device
        try:
            return {"path": path, "entries": device.list_dir(self._port(), fb.sdk_dirs(), path)}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def files_mkdir(self, path):
        import device
        try:
            device.mkdir(self._port(), fb.sdk_dirs(), path)
            return {"ok": True}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def files_rename(self, old, new):
        import device
        try:
            device.rename(self._port(), fb.sdk_dirs(), old, new)
            return {"ok": True}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def files_delete(self, path):
        """Delete a file or folder on the SD card (the UI confirms first)."""
        def work(job):
            import device
            job.stage("send", Path(path).name)
            try:
                device.remove_tree(self._port(), fb.sdk_dirs(), path,
                                   progress=lambda d, t: self._fprog("Deleting")(d, t))
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Deleted {path}")
            return {"files_changed": True, "send_only": True, "quiet": True}
        return self._run_job("files", work)

    def files_download(self, remote):
        import webview
        try:
            res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        except AttributeError:
            res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return {"cancelled": True}
        dest = res[0]

        def work(job):
            import device
            job.stage("send", Path(remote).name)
            try:
                out = device.download(self._port(), fb.sdk_dirs(), remote, dest, progress=self._fprog("Downloading"))
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Saved to {out}")
            return {"send_only": True, "quiet": True, "saved": str(out)}
        return self._run_job("files", work)

    def files_upload(self, remote_dir):
        import webview
        try:
            res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        except AttributeError:
            res = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        if not res:
            return {"cancelled": True}
        paths = list(res)

        def work(job):
            import device
            job.stage("send", f"{len(paths)} file(s)")
            try:
                n = device.upload(self._port(), fb.sdk_dirs(), paths, remote_dir, progress=self._fprog("Uploading"))
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Uploaded {n} file(s) to {remote_dir}")
            return {"files_changed": True, "send_only": True, "quiet": True}
        return self._run_job("files", work)

    # ------------------------------------------------ backups
    BACKUP_DIR = fb.ROOT / "backups"

    def backup_folders(self):
        import device
        try:
            return {"folders": device.folder_sizes(self._port(), fb.sdk_dirs())}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def backups_list(self):
        import device
        out = []
        for p in sorted(self.BACKUP_DIR.glob("*.zip"), key=lambda p: -p.stat().st_mtime):
            try:
                info = device.backup_info(p)
            except Exception:  # noqa: BLE001
                continue
            out.append({"path": str(p), "name": p.stem, "size": p.stat().st_size, "mtime": int(p.stat().st_mtime), **info})
        return {"backups": out, "dir": str(self.BACKUP_DIR)}

    def backup_start(self, names):
        names = [n for n in names if n and "/" not in n and ".." not in n]
        if not names:
            return {"error": "Pick at least one folder."}
        dev = dict(self._device)
        stamp = time.strftime("%Y-%m-%d_%H%M")
        label = re.sub(r"[^\w\-]+", "", dev.get("name") or "Flipper") or "Flipper"
        path = self.BACKUP_DIR / f"{label}_{stamp}.zip"

        def work(job):
            import device
            job.stage("send", "backup")
            try:
                r = device.backup(self._port(), fb.sdk_dirs(), names, path,
                                  device_info={k: dev.get(k) for k in ("name", "fw_name", "version", "commit", "api")},
                                  progress=self._fprog("Backing up"), should_stop=job.cancelled.is_set)
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Backup saved: {r['path']} ({r['files']} files)")
            return {"send_only": True, "quiet": True, "backup_done": r}
        return self._run_job("backup", work)

    def backup_restore(self, path):
        if not Path(path).is_file() or Path(path).parent != self.BACKUP_DIR:
            return {"error": "That backup file is gone."}

        def work(job):
            import device
            job.stage("send", "restore")
            try:
                r = device.restore(self._port(), fb.sdk_dirs(), path,
                                   progress=self._fprog("Restoring"), should_stop=job.cancelled.is_set)
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Restored {r['files']} files")
            return {"send_only": True, "quiet": True, "restored": r, "files_changed": True}
        return self._run_job("restore", work)

    def backup_delete(self, path):
        p = Path(path)
        if p.parent == self.BACKUP_DIR and p.suffix == ".zip" and p.is_file():
            p.unlink()
            return {"ok": True}
        return {"error": "Can't delete that."}

    # ------------------------------------------------ screen mirror + remote
    def mirror_start(self):
        import device
        if getattr(self, "_mirror", None) and self._mirror.running:
            return {"ok": True}
        try:
            port = self._port()
        except fb.BuildError as e:
            return {"error": str(e)}
        self._frame = None
        self._frame_seq = 0

        def on_frame(data, orient):
            self._frame = (data, orient)
            self._frame_seq += 1

        def on_state(state, detail=""):
            self._emit("mirror", state=state, detail=detail)
        self._mirror = device.Mirror(port, fb.sdk_dirs(), on_frame, on_state)
        self._mirror.start()
        if not getattr(self, "_frame_thread", None):
            self._frame_thread = threading.Thread(target=self._frame_pump, daemon=True)
            self._frame_thread.start()
        return {"ok": True}

    def _frame_pump(self):
        """Push the newest frame straight to the page, up to 25 fps (skips frames if the page is slow)."""
        import base64
        sent = -1
        while True:
            time.sleep(0.04)
            m = getattr(self, "_mirror", None)
            if not m or self._frame is None or self._frame_seq == sent or self._window is None:
                continue
            sent = self._frame_seq
            data, orient = self._frame
            try:
                self._window.evaluate_js(f"fb.onFrame('{base64.b64encode(data).decode()}', {int(orient)})")
            except Exception:  # noqa: BLE001
                pass

    def mirror_stop(self):
        m = getattr(self, "_mirror", None)
        if m:
            threading.Thread(target=m.stop, daemon=True).start()
            self._mirror = None
        return {"ok": True}

    def mirror_press(self, key, kind="short"):
        m = getattr(self, "_mirror", None)
        if m and m.running:
            try:
                m.press(key, kind)
            except KeyError:
                return {"error": "bad key"}
            return {"ok": True}
        return {"error": "not mirroring"}

    def save_screenshot(self, png_b64):
        import base64
        d = fb.ROOT / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        p = d / time.strftime("flipper_%Y-%m-%d_%H%M%S.png")
        p.write_bytes(base64.b64decode(png_b64.split(",", 1)[-1]))
        subprocess.Popen(["explorer", "/select,", str(p)])
        return {"path": str(p)}

    # ------------------------------------------------ app update check
    def _update_check(self):
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/c3rb3ruszx/flipper-workbench/releases/latest",
                headers={"User-Agent": "flipper-workbench", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rel = json.loads(r.read())
            latest = rel.get("tag_name", "")
            if _vtuple(latest) > _vtuple(fb.APP_VERSION):
                self._emit("update", version=latest.lstrip("v"), url=rel.get("html_url") or fb.REPO_URL + "/releases")
        except Exception:  # noqa: BLE001
            pass

    def start_background(self):
        for target in (self._device_monitor, self._watcher, self._update_check):
            threading.Thread(target=target, daemon=True).start()
        return True

    # ------------------------------------------------ misc
    def clear_recent(self):
        fb.save_settings(recent=[])
        return True

    def open_folder(self, path):
        p = Path(path)
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(p)
        return True

    def open_url(self, url):
        if url.startswith("https://"):
            webbrowser.open(url)
        return True

    def copy(self, text):
        return _clip_set(text)


# ---------------------------------------------------------------- window

def _dark_title_bar(window):
    try:
        hwnd = window.native.Handle.ToInt32()
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        color = ctypes.c_int(0x00121212)  # caption colour (Win11), BGR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
    except Exception:  # noqa: BLE001
        pass


def _no_webview2_message(err):
    msg = ("Flipper Workbench needs Microsoft Edge WebView2, which is missing or broken on this PC.\n\n"
           "Install the 'Evergreen Bootstrapper' from\n"
           "https://developer.microsoft.com/microsoft-edge/webview2/\nthen open Flipper Workbench again.\n\n"
           f"Details: {err}")
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, fb.APP_NAME, 0x10)
    except Exception:  # noqa: BLE001
        print(msg)


def main():
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(fb.APP_ID)
        except Exception:  # noqa: BLE001
            pass
    import webview

    api = Api()
    window = webview.create_window(
        fb.APP_NAME, url=str(fb.UI_DIR / "index.html"), js_api=api,
        width=1180, height=860, min_size=(960, 720), background_color="#0d0d0d",
    )
    api._window = window
    window.events.shown += lambda: _dark_title_bar(window)
    try:
        webview.start(gui="edgechromium", http_server=True, debug=bool(os.environ.get("FB_DEBUG")),
                      icon=str(fb.ICON))
    except Exception as e:  # noqa: BLE001
        _no_webview2_message(e)
