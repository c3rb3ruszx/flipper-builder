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
import sys
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


# ---------------------------------------------------------------- files on this computer

def _reveal(path):
    """Show a file selected in Explorer / Finder, or open a folder."""
    p = Path(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
        elif os.name == "nt":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                os.startfile(p)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(p.parent if p.is_file() else p)])
    except OSError:
        pass


# ---------------------------------------------------------------- self-update (installed Windows app)

SELF_ZIP = "FlipperWorkbench-win64.zip"


def _download_checked(url, sha256, dest, progress=lambda d, t: None):
    import hashlib
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    h, done = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "flipper-workbench"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        while chunk := r.read(65536):
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total or done)
    if h.hexdigest() != sha256.lower():
        dest.unlink(missing_ok=True)
        raise ValueError("the download doesn't match GitHub's checksum")
    return dest


def stage_update(zip_path, dest):
    """Unpack the release zip (safely) and return the folder holding FlipperWorkbench.exe + _internal."""
    import shutil
    import zipfile
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                raise ValueError(f"unsafe path in the update: {n}")
        z.extractall(dest)
    exes = list(dest.rglob("FlipperWorkbench.exe"))
    if len(exes) != 1 or not (exes[0].parent / "_internal").is_dir():
        raise ValueError("the update zip doesn't look like Flipper Workbench")
    return exes[0].parent


def write_update_helper(staged, app_dir, pid, work):
    """A .cmd that waits for this process to end, swaps the program files and starts the new version.
    Only FlipperWorkbench.exe and _internal are replaced; .cache, output, library, settings, backups stay."""
    staged, app_dir, work = Path(staged).resolve(), Path(app_dir).resolve(), Path(work).resolve()
    helper = work / "apply_update.cmd"
    log = work / "apply_update.log"
    helper.write_text(f"""@echo off
setlocal
set "PID={pid}"
set "NEW={staged}"
set "APP={app_dir}"
rem full paths: never pick up other tools of the same name from PATH
set "SYS=%SystemRoot%\\System32"
:wait
"%SYS%\\tasklist.exe" /fi "PID eq %PID%" 2>nul | "%SYS%\\find.exe" "%PID%" >nul && ("%SYS%\\ping.exe" -n 2 127.0.0.1 >nul & goto wait)
"%SYS%\\ping.exe" -n 2 127.0.0.1 >nul
rem keep the current version aside until the new one is fully in place
if exist "%APP%\\_internal.old" rmdir /s /q "%APP%\\_internal.old"
move "%APP%\\_internal" "%APP%\\_internal.old" >"{log}" || goto failed_early
copy /y "%APP%\\FlipperWorkbench.exe" "%APP%\\FlipperWorkbench.exe.old" >>"{log}" || goto rollback
"%SYS%\\robocopy.exe" "%NEW%\\_internal" "%APP%\\_internal" /E /R:10 /W:1 /NFL /NDL /NJH /NJS >>"{log}"
if errorlevel 8 goto rollback
copy /y "%NEW%\\FlipperWorkbench.exe" "%APP%\\FlipperWorkbench.exe" >>"{log}" || goto rollback
rmdir /s /q "%APP%\\_internal.old"
del /q "%APP%\\FlipperWorkbench.exe.old"
echo updated >>"{log}"
start "" "%APP%\\FlipperWorkbench.exe"
exit /b 0
:rollback
echo rolling back >>"{log}"
if exist "%APP%\\_internal" rmdir /s /q "%APP%\\_internal"
move "%APP%\\_internal.old" "%APP%\\_internal" >>"{log}"
if exist "%APP%\\FlipperWorkbench.exe.old" copy /y "%APP%\\FlipperWorkbench.exe.old" "%APP%\\FlipperWorkbench.exe" >>"{log}"
:failed_early
echo update failed, old version kept >>"{log}"
start "" "%APP%\\FlipperWorkbench.exe"
exit /b 1
""", encoding="ascii")
    return helper


GIF_BG, GIF_FG = (255, 140, 41), (0, 0, 0)   # Flipper LCD orange, black pixels


def _frame_image(data, orient, scale=4, pad=4):
    """One 1024-byte Flipper frame (8 pages x 128 columns, bit 0 on top) as a scaled 2-colour image."""
    from PIL import Image
    px = bytearray(128 * 64)
    for y in range(64):
        page, bit = (y >> 3) * 128, y & 7
        row = y * 128
        for x in range(128):
            if (data[page + x] >> bit) & 1:
                px[row + x] = 1
    img = Image.frombytes("P", (128, 64), bytes(px))
    if orient == 1:
        img = img.rotate(180)
    img = img.resize((128 * scale, 64 * scale), Image.NEAREST)
    out = Image.new("P", (128 * scale + pad * 2 * scale, 64 * scale + pad * 2 * scale), 0)
    out.paste(img, (pad * scale, pad * scale))
    out.putpalette(list(GIF_BG) + list(GIF_FG))
    return out


# palette for framed pictures: 0 LCD orange, 1 ink, then the drawn device body
FRAME_PALETTE = [255, 140, 41, 0, 0, 0, 236, 233, 228, 200, 196, 190, 22, 22, 22, 255, 130, 0, 120, 116, 110, 248, 246, 242]
_FRAME_BASE = {}


def _device_base(scale=4):
    """A simple drawn Flipper-style body (our own drawing) with a hole for the 128x64 screen."""
    from PIL import Image, ImageDraw
    if scale in _FRAME_BASE:
        return _FRAME_BASE[scale]
    s = scale
    sw, sh = 128 * s, 64 * s
    W, H = 18 * s + sw + 118 * s, sh + 52 * s
    img = Image.new("P", (W, H), 7)
    img.putpalette(FRAME_PALETTE + [0] * (768 - len(FRAME_PALETTE)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4 * s, 6 * s, W - 4 * s, H - 2 * s), radius=26 * s, fill=3)          # shadow
    d.rounded_rectangle((2 * s, 2 * s, W - 6 * s, H - 6 * s), radius=26 * s, fill=2)          # body
    sx, sy = 18 * s, 22 * s
    d.rounded_rectangle((sx - 7 * s, sy - 7 * s, sx + sw + 7 * s, sy + sh + 7 * s), radius=6 * s, fill=4)  # bezel
    d.ellipse((sx - 4 * s, sy - 15 * s, sx, sy - 11 * s), fill=5)                             # LED
    cx, cy, r = sx + sw + 62 * s, sy + sh // 2 - 2 * s, 30 * s                                  # d-pad
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=3, outline=6, width=s)
    d.ellipse((cx - 10 * s, cy - 10 * s, cx + 10 * s, cy + 10 * s), fill=5)
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        tx, ty = cx + dx * 21 * s, cy + dy * 21 * s
        d.regular_polygon((tx, ty, 3 * s), 3, rotation={(0, -1): 0, (0, 1): 180, (-1, 0): 90, (1, 0): 270}[(dx, dy)], fill=6)
    bx, by = cx + 22 * s, cy + 36 * s                                                           # back button
    d.ellipse((bx - 9 * s, by - 9 * s, bx + 9 * s, by + 9 * s), fill=3, outline=6, width=s)
    _FRAME_BASE[scale] = (img, (sx, sy))
    return _FRAME_BASE[scale]


def _framed(data, orient, scale=4):
    """The Flipper's screen inside the drawn device (palette image, GIF-friendly)."""
    base, (sx, sy) = _device_base(scale)
    lcd = _frame_image(data, orient, scale=scale, pad=0)
    out = base.copy()
    out.paste(lcd, (sx, sy))            # lcd uses indices 0/1, same palette
    return out


def save_gif(frames, t_end, folder, framed=False):
    """frames: [(time, 1024 bytes, orient)], oldest first. Identical frames in a row are merged."""
    if not frames:
        raise ValueError("No frames: the Flipper's screen didn't send anything.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    merged = []                                   # [(data, orient, start time)]
    for t, data, orient in frames:
        if merged and merged[-1][0] == data and merged[-1][1] == orient:
            continue
        merged.append((data, orient, t))
    images, durations = [], []
    for i, (data, orient, t) in enumerate(merged):
        nxt = merged[i + 1][2] if i + 1 < len(merged) else max(t_end, t + 0.5)
        images.append(_framed(data, orient) if framed else _frame_image(data, orient))
        durations.append(max(20, int(round((nxt - t) * 1000 / 10)) * 10))   # GIF timing is in 10 ms steps
    p = folder / time.strftime("flipper_%Y-%m-%d_%H%M%S.gif")
    images[0].save(p, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=False, disposal=1)
    return p


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
            "auto_backup": s.get("auto_backup", True),
            "frame_shots": bool(s.get("frame_shots")),
            "auto_mirror": s.get("auto_mirror", True),
        }

    def clipboard(self):
        return _github_link(_clip_get())

    def set_auto_backup(self, on):
        fb.save_settings(auto_backup=bool(on))
        return {"ok": True}

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

    COMPANION_DIR = "/ext/apps_data/workbench"

    def companion_install(self):
        """Build the bundled companion app for the Flipper's firmware and install it (Apps > Tools)."""
        import shutil
        fw = self._device.get("fw")
        if not self._device.get("connected") or fw not in fb.FIRMWARES:
            return {"error": "Plug in your Flipper first (its firmware decides how the app is built)."}
        src = fb.BUNDLE / "companion" / "workbench_companion"
        if not src.is_dir():
            src = Path(__file__).resolve().parent / "companion" / "workbench_companion"
        work_src = fb.ROOT / ".cache" / "companion" / "workbench_companion"   # builds write next to the source

        def work(job):
            import device
            shutil.rmtree(work_src, ignore_errors=True)
            shutil.copytree(src, work_src)
            results, out = fb.build(str(work_src), fw, job=job, install=True)
            try:   # the folder the app reads from, with a first status
                device.ensure_dir(self._port(), fb.sdk_dirs(), self.COMPANION_DIR)
                self._write_status()
            except Exception:  # noqa: BLE001
                pass
            return self._build_payload(results, out, fw, job, str(work_src), "Workbench companion")
        return self._run_job(fw, work)

    def _write_status(self):
        """If the companion app is set up (its folder exists), tell it what Workbench last did."""
        import device
        dev = self._device
        if not dev.get("connected") or dev.get("error"):
            return
        s = fb.load_settings()
        name = dev.get("name") or "Flipper"
        fmt = lambda t: time.strftime("%d %b %H:%M", time.localtime(t)) if t else "never"  # noqa: E731
        label = re.sub(r"[^\w\-]+", "", name) or "Flipper"      # same naming as backup_start
        backups = sorted(self.BACKUP_DIR.glob(f"{label}_*.zip"),
                         key=lambda p: p.stat().st_mtime) if self.BACKUP_DIR.is_dir() else []
        text = (f"Workbench {fb.APP_VERSION} connected\n{fmt(time.time())}\n"
                f"Last backup: {fmt(backups[-1].stat().st_mtime if backups else 0)}\n"
                f"Last sync: {fmt(s.get('last_sync', 0))}\n"
                f"Firmware: {dev.get('fw_name') or '?'}\n")

        def fn(mod, st):
            if not st.exist_dir(self.COMPANION_DIR):
                return False
            return True
        port = self._port()
        if device.storage_call(port, fb.sdk_dirs(), fn, seconds=10):
            device.write_file(port, fb.sdk_dirs(), f"{self.COMPANION_DIR}/status.txt", text.encode("utf-8"))

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
            key = (tuple(sorted(p["port"] for p in ports)), getattr(self, "_sel_port", None))
            if key == known and not forced:
                continue
            if self._job and key == known:
                continue   # don't poke the port while a job may be using it
            known = key
            if not ports:
                self._device = {"connected": False}
            elif len(ports) > 1 and not any(x["port"] == getattr(self, "_sel_port", None) for x in ports):
                self._device = {"connected": True, "error": "More than one Flipper is plugged in: pick one in the header.",
                                "choices": ports}
            else:
                p = next((x for x in ports if x["port"] == getattr(self, "_sel_port", None)), ports[0])
                time.sleep(0.6)  # let the USB serial settle after plug-in
                try:
                    info = device.read_info(p["port"], fb.sdk_dirs())
                    self._device = {"connected": True, **device.summary(p["port"], p["name"], info)}
                except Exception as e:  # noqa: BLE001
                    self._device = {"connected": True, "port": p["port"], "name": p["name"] or "Flipper",
                                    "error": str(e)}
            self._emit("device", device=self._device)
            if self._device.get("connected") and not self._device.get("error"):
                def status_later():
                    time.sleep(8)               # after the UI's own first reads
                    if not self._job:
                        try:
                            self._write_status()
                        except Exception:  # noqa: BLE001
                            pass
                threading.Thread(target=status_later, daemon=True).start()

    # ------------------------------------------------ what's on the Flipper
    def _port(self):
        import device
        ports = device.find_ports()
        if not ports:
            raise fb.BuildError("No Flipper found on USB. Plug it in, unlock it, and close qFlipper.")
        if len(ports) > 1:
            sel = getattr(self, "_sel_port", None)
            if any(x["port"] == sel for x in ports):
                return sel
            raise fb.BuildError("More than one Flipper is plugged in. Pick one in the header.")
        return ports[0]["port"]

    def flipper_ports(self):
        import device
        return {"ports": device.find_ports(), "selected": getattr(self, "_sel_port", None)}

    def flipper_select(self, port):
        """Use this Flipper when several are plugged in (remembered for this run)."""
        import device
        if not any(x["port"] == port for x in device.find_ports()):
            return {"error": "That Flipper isn't plugged in any more."}
        if getattr(self, "_mirror", None):
            self.mirror_stop()
        self._sel_port = port
        self._device_refresh.set()
        return {"ok": True}

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

    # ------------------------------------------------ tools: file pickers
    def _pick(self, file_types=(), folder=False):
        import webview
        kind_new = webview.FileDialog.FOLDER if folder else webview.FileDialog.OPEN
        try:
            res = self._window.create_file_dialog(kind_new, file_types=file_types) if not folder else \
                self._window.create_file_dialog(kind_new)
        except AttributeError:
            res = self._window.create_file_dialog(webview.FOLDER_DIALOG if folder else webview.OPEN_DIALOG,
                                                  file_types=file_types)
        return (res[0] if isinstance(res, (list, tuple)) else res) if res else None

    # ------------------------------------------------ tools: music converter
    def music_pick(self):
        p = self._pick(("Ringtones and MIDI (*.mid;*.midi;*.txt;*.rtttl;*.rtx)", "All files (*.*)"))
        if not p:
            return {"cancelled": True}
        return self.music_convert(Path(p).name, None, p)

    def music_convert(self, name, text=None, path=None):
        import music
        try:
            data = Path(path).read_bytes() if path else (text or "")
            if path and len(data) > 2 * 1024 * 1024:
                return {"error": "That file is too big for a ringtone or song."}
            r = music.convert(name or "song.txt", data)
            r["name"] = re.sub(r"[^\w\- ]+", "", Path(name or r["title"]).stem or r["title"]).strip()[:40] or "Song"
            return r
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def music_save(self, name, fmf):
        import device
        name = re.sub(r"[^\w\- ]+", "", name or "").strip()[:40] or "Song"
        if not fmf.startswith("Filetype: Flipper Music Format"):
            return {"error": "Nothing to save."}
        try:
            path = f"/ext/music_player/{name}.fmf"
            port = self._port()
            device.ensure_dir(port, fb.sdk_dirs(), "/ext/music_player")
            device.write_file(port, fb.sdk_dirs(), path, fmf.encode("utf-8"))
            return {"ok": True, "path": path}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    # ------------------------------------------------ tools: animation maker
    def anim_pick(self):
        p = self._pick(("Images and GIFs (*.gif;*.png;*.webp;*.apng;*.jpg;*.jpeg;*.bmp)", "All files (*.*)"))
        if not p:
            return {"cancelled": True}
        if Path(p).stat().st_size > 30 * 1024 * 1024:
            return {"error": "That file is too big (30 MB max)."}
        self._anim_src = Path(p)
        return {"ok": True, "name": Path(p).stem}

    def anim_build(self, opts):
        """Convert the picked image with these options; returns preview frames (XBM rows, base64)."""
        import base64
        import anim
        src = getattr(self, "_anim_src", None)
        if not src:
            return {"error": "Pick a GIF or image first."}
        try:
            res = anim.build(src.read_bytes(), opts or {})
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't read that image: {e}"}
        self._anim_res = res
        return {"fps": res["fps"], "count": len(res["frames"]),
                "frames": [base64.b64encode(anim.bm_bytes(f)[1:]).decode() for f in res["frames"]]}

    def anim_install(self, name, weight=3):
        """Momentum: into the 'Workbench' asset pack (pick it on the Flipper). Other firmware: /ext/dolphin
        plus an entry in its manifest (the original manifest is kept as manifest.txt.bak the first time)."""
        import tempfile
        import anim
        import device
        res = getattr(self, "_anim_res", None)
        if not res:
            return {"error": "Make an animation first."}
        name = anim.safe_name(name)
        momentum = (self._device.get("fw") or "").startswith("momentum")
        base = "/ext/asset_packs/Workbench/Anims" if momentum else "/ext/dolphin"
        try:
            port = self._port()
            with tempfile.TemporaryDirectory(prefix="fw_anim_") as td:
                folder = anim.write_folder(res, Path(td) / name)
                device.upload(port, fb.sdk_dirs(), [str(folder)], base)
                man = f"{base}/manifest.txt"
                old = None
                if device.file_exists(port, fb.sdk_dirs(), man):
                    old = device.read_file(port, fb.sdk_dirs(), man).decode("utf-8", "replace")
                    if not momentum and not device.file_exists(port, fb.sdk_dirs(), man + ".bak"):
                        device.write_file(port, fb.sdk_dirs(), man + ".bak", old.encode("utf-8"))
                new = anim.add_to_manifest(old, name, int(weight))
                device.write_file(port, fb.sdk_dirs(), man, new.encode("utf-8"))
            made = fb.load_settings().get("anims_made") or []
            made = [m for m in made if m.get("name") != name] + [{"name": name, "base": base}]
            fb.save_settings(anims_made=made)
            return {"ok": True, "path": f"{base}/{name}", "momentum": momentum}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't install it: {e}"}

    def anim_list(self):
        return {"made": fb.load_settings().get("anims_made") or []}

    def anim_remove(self, name):
        """Remove an animation Workbench made: its folder and its manifest entry."""
        import anim
        import device
        made = fb.load_settings().get("anims_made") or []
        item = next((m for m in made if m.get("name") == name), None)
        if not item or item.get("base") not in ("/ext/asset_packs/Workbench/Anims", "/ext/dolphin"):
            return {"error": "Workbench didn't make that one."}
        try:
            port, base = self._port(), item["base"]
            man = f"{base}/manifest.txt"
            if device.file_exists(port, fb.sdk_dirs(), man):
                text = device.read_file(port, fb.sdk_dirs(), man).decode("utf-8", "replace")
                device.write_file(port, fb.sdk_dirs(), man, anim.remove_from_manifest(text, name).encode("utf-8"))
            device.remove_tree(port, fb.sdk_dirs(), f"{base}/{name}")
            left = [m for m in made if m.get("name") != name]
            fb.save_settings(anims_made=left)
            if base.startswith("/ext/asset_packs/Workbench") and not any(m.get("base") == base for m in left):
                device.remove_tree(port, fb.sdk_dirs(), "/ext/asset_packs/Workbench")   # the pack only held ours
            return {"ok": True}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    # ------------------------------------------------ tools: Momentum asset packs
    def packs_list(self, force=False):
        import assetpacks
        try:
            return assetpacks.load(fb.ROOT / ".cache", force=bool(force))
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't load the asset packs: {e}"}

    def pack_install(self, pack_id):
        import assetpacks
        data = self.packs_list()
        pack = next((p for p in data.get("packs") or [] if p["id"] == pack_id), None)
        if not pack:
            return {"error": "Unknown pack."}

        def work(job):
            import device
            job.stage("fetch", pack["name"])
            try:
                folders = assetpacks.download(pack, fb.ROOT / ".cache" / "packdl")
            except Exception as e:  # noqa: BLE001
                raise fb.BuildError(f"Download failed: {e}")
            job.log(f"OK {pack['name']}: SHA-256 matches Momentum's directory")
            job.stage("send", "copying")
            try:
                device.upload(self._port(), fb.sdk_dirs(), [str(f) for f in folders], "/ext/asset_packs",
                              progress=self._fprog("Copying the pack"))
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Installed to /ext/asset_packs/{', '.join(f.name for f in folders)}")
            return {"send_only": True, "quiet": True, "pack_done": {"name": pack["name"], "folders": [f.name for f in folders]}}
        return self._run_job("files", work)

    # ------------------------------------------------ tools: folder sync
    def sync_pairs(self):
        return {"pairs": fb.load_settings().get("sync_pairs") or []}

    def sync_pick_local(self):
        p = self._pick(folder=True)
        return {"path": p} if p else {"cancelled": True}

    def sync_save_pairs(self, pairs):
        clean = []
        for p in pairs or []:
            if isinstance(p, dict) and p.get("local") and str(p.get("remote", "")).startswith("/ext/"):
                clean.append({"local": str(p["local"]), "remote": str(p["remote"]).rstrip("/"),
                              "mode": p.get("mode") if p.get("mode") in ("to_flipper", "to_pc", "both") else "both",
                              "delete": bool(p.get("delete"))})
        fb.save_settings(sync_pairs=clean[:20])
        return {"ok": True}

    def sync_plan(self, local, remote, mode, delete=False):
        import device
        import sync
        if not Path(local).is_dir():
            return {"error": "That PC folder doesn't exist."}
        try:
            diff = sync.compare(self._port(), fb.sdk_dirs(), local, remote)
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        steps = sync.plan(diff, mode, bool(delete))
        sizes = {**diff["remote_sizes"], **diff["local_sizes"]}
        return {"steps": [[a, r, sizes.get(r, 0)] for a, r in steps], "same": len(diff["same"])}

    def sync_run(self, local, remote, steps):
        import sync
        steps = [(a, r) for a, r, *_ in steps if a in ("upload", "download", "delete_remote", "delete_local")]
        if not steps:
            return {"error": "Nothing to do."}

        def work(job):
            import device
            job.stage("send", f"{len(steps)} change(s)")
            try:
                n = sync.apply(self._port(), fb.sdk_dirs(), local, remote, steps,
                               progress=lambda d, t, name: self._emit("fprog", label="Syncing", done=d, total=t, name=name),
                               should_stop=job.cancelled.is_set)
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK Synced {n} file(s): {local} <-> {remote}")
            fb.save_settings(last_sync=int(time.time()))
            return {"send_only": True, "quiet": True, "files_changed": True, "synced": n}
        return self._run_job("files", work)

    # ------------------------------------------------ IR remote library (Flipper-IRDB)
    def irdb_index(self, force=False):
        import irdb
        try:
            return irdb.load(fb.ROOT / ".cache", force=bool(force))
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't load the IR library: {e}"}

    def irdb_get(self, path):
        import capture
        import irdb
        try:
            text = irdb.fetch(path)
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't download it: {e}"}
        res = capture.parse(path.rsplit("/", 1)[-1], text, path)
        res["size"] = len(text.encode("utf-8"))
        return res

    def irdb_install(self, path):
        """Copy a library remote to /ext/infrared/<file name>; never overwrites (adds _2, _3...)."""
        import device
        import irdb
        try:
            text = irdb.fetch(path)
            port = self._port()
            name = re.sub(r"[^\w.\-]+", "_", path.rsplit("/", 1)[-1])[:60]
            stem, ext = name[:-3], ".ir"
            existing = {e["name"].lower() for e in device.list_dir(port, fb.sdk_dirs(), "/ext/infrared")}
            n, final = 1, name
            while final.lower() in existing:
                n += 1
                final = f"{stem}_{n}{ext}"
            device.write_file(port, fb.sdk_dirs(), f"/ext/infrared/{final}", text.encode("utf-8"))
            return {"ok": True, "path": f"/ext/infrared/{final}"}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't install it: {e}"}

    def ir_files(self):
        """Every .ir file in /ext/infrared (one level of folders), for merging remotes."""
        import device
        try:
            port, out = self._port(), []
            for e in device.list_dir(port, fb.sdk_dirs(), "/ext/infrared"):
                if not e.get("dir") and e["name"].lower().endswith(".ir"):
                    out.append(f"/ext/infrared/{e['name']}")
            return {"files": sorted(out, key=str.lower)}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def files_view(self, path, size=0):
        """Read a file off the SD card for the viewer: parsed captures, plain text, or 'binary'."""
        import capture
        import device
        if size and size > capture.MAX_VIEW:
            return {"too_big": True, "size": size}
        try:
            data = device.read_file(self._port(), fb.sdk_dirs(), path, size)
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001  (storage errors from the SDK client)
            return {"error": f"Couldn't read it: {e}"}
        if not capture.is_text(data):
            return {"binary": True, "size": len(data)}
        res = capture.parse(path.rsplit("/", 1)[-1], data.decode("utf-8", "replace"), path)
        res["size"] = len(data)
        return res

    def files_save(self, path, text):
        """Save edited text back to the SD card (the editor asks first)."""
        import device
        try:
            n = device.write_file(self._port(), fb.sdk_dirs(), path, text.encode("utf-8"))
            return {"ok": True, "size": n}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't save it: {e}"}

    def ir_send(self, command):
        """Fire one IR button from a .ir file (the command line comes from capture.parse, checked here)."""
        if not re.fullmatch(r"ir tx (?:[A-Za-z0-9]+ 0x[0-9a-fA-F]+ 0x[0-9a-fA-F]+|RAW F:\d+ DC:\d+(?: \d+){1,512})", command or ""):
            return {"error": "That isn't an IR button."}
        import device
        out = []
        try:
            device.cli_run(self._port(), fb.sdk_dirs(), command, out.append, limit=15)
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        text = "".join(out).strip()
        return {"error": text} if text and ("error" in text.lower() or "usage" in text.lower()) else {"ok": True}

    # ------------------------------------------------ firmware update
    FW_DIR = fb.ROOT / ".cache" / "firmware"
    BK_DEFAULT = {"subghz", "nfc", "lfrfid", "infrared", "badusb", "ibutton", "apps_data", "music_player",
                  "wav_player", "subghz_remote"}
    BK_SKIP = {"System Volume Information", "update", "u2f", "apps", "apps_manifests"}

    def fw_info(self):
        """Latest packages per firmware, plus what the Flipper runs and its battery."""
        import device
        import firmware
        res = firmware.available()
        for t in res["targets"].values():
            t["age"] = firmware.describe_age(t.get("timestamp"))
        dev = self._device
        res["device"] = dev
        if dev.get("connected") and not dev.get("error") and not self._job:
            try:
                res["power"] = device.device_power(self._port(), fb.sdk_dirs())
            except Exception:  # noqa: BLE001
                res["power"] = {}
        return res

    def _battery_ok(self):
        import device
        p = device.device_power(self._port(), fb.sdk_dirs())
        try:
            level = int(p.get("charge.level", "0"))
        except ValueError:
            level = 0
        charging = p.get("charge.state", "") in ("charging", "charged")
        return level >= 50 or (charging and level >= 25), level

    def fw_prepare(self, fw_id, backup=True):
        """Backup (optional), download + SHA-256 check, unpack, copy to /ext/update and MD5-check every file.
        Installs nothing: fw_install does that after the user confirms again."""
        import device
        import firmware
        dev = dict(self._device)
        if not dev.get("connected") or dev.get("error"):
            return {"error": "Plug in your Flipper (and close qFlipper) first."}
        if getattr(self, "_mirror", None):
            self.mirror_stop()

        def work(job):
            job.stage("fetch", "firmware index")
            avail = firmware.available()["targets"]
            target = avail.get(fw_id)
            if not target:
                raise fb.BuildError("That firmware has no update package right now.")
            ok, level = self._battery_ok()
            if not ok:
                raise fb.BuildError(f"Battery is at {level}%. Charge your Flipper to at least 50% (or plug it into a charger) before updating.")
            job.log(f"== Battery {level}%: OK")
            if backup:
                job.stage("send", "backup first")
                folders = device.folder_sizes(self._port(), fb.sdk_dirs())
                names = [f["name"] for f in folders if not f.get("file") and not f["name"].startswith(".")
                         and f["name"] not in self.BK_SKIP and (f["name"] in self.BK_DEFAULT or f["size"] < 1048576)]
                if names:
                    label = re.sub(r"[^\w\-]+", "", dev.get("name") or "Flipper") or "Flipper"
                    path = self.BACKUP_DIR / f"{label}_{time.strftime('%Y-%m-%d_%H%M')}_before-update.zip"
                    r = device.backup(self._port(), fb.sdk_dirs(), names, path,
                                      device_info={k: dev.get(k) for k in ("name", "fw_name", "version", "commit", "api")},
                                      progress=self._fprog("Backing up"), should_stop=job.cancelled.is_set)
                    job.log(f"OK Backup saved: {r['path']} ({r['files']} files)")
            job.stage("fetch", f"{target['family']} {target['version']}")
            job.log(f"== Downloading {target['url']}")
            try:
                tgz = firmware.download(target, self.FW_DIR, progress=lambda d, t: self._fprog("Downloading")(d, t),
                                        should_stop=job.cancelled.is_set)
            except InterruptedError:
                raise fb.Cancelled("Cancelled.")
            except Exception as e:  # noqa: BLE001
                raise fb.BuildError(f"Download failed: {e}")
            job.log("OK SHA-256 matches the firmware index")
            job.stage("scan", "unpacking")
            try:
                pkg = firmware.extract(tgz, self.FW_DIR / "unpacked")
            except Exception as e:  # noqa: BLE001
                raise fb.BuildError(str(e))
            size = sum(f.stat().st_size for f in pkg.rglob("*") if f.is_file())
            free = device.sd_free_kib(self._port(), fb.sdk_dirs())
            if free is not None and free * 1024 < size * 3:
                raise fb.BuildError(f"Not enough free space on the SD card for the update ({size // 1048576} MB needed, x3 to be safe).")
            job.stage("send", "copying the update")
            remote_name = f"/ext/update/{pkg.name}"
            existed = device.storage_call(self._port(), fb.sdk_dirs(), lambda m, st: st.exist_dir(remote_name))
            try:
                remote = device.upload_update(self._port(), fb.sdk_dirs(), pkg, progress=self._fprog("Copying the update"),
                                              should_stop=job.cancelled.is_set)
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log(f"OK {pkg.name} copied to {remote}, every file checked (MD5)")
            return {"send_only": True, "quiet": True,
                    "fw_ready": {"remote": remote, "fw": fw_id, "family": target["family"], "channel": target["channel"],
                                 "version": target["version"], "existed": bool(existed)}}
        return self._run_job("firmware", work)

    def fw_bundled(self):
        """Apps that ship with the firmware the Flipper runs (they're updated with the firmware, not from
        the catalog). Read from the official package of that exact build, cached per version.
        {"apps": None} when it can't be known."""
        import firmware
        dev = dict(self._device)
        fw = dev.get("fw")
        if not dev.get("connected") or not fw:
            return {"apps": None}
        key = re.sub(r"[^\w.\-]+", "_", f"{fw}_{dev.get('version')}_{dev.get('commit')}")
        cache = self.FW_DIR / f"bundled_{key}.json"
        try:
            return {"apps": json.loads(cache.read_text(encoding="utf-8"))}
        except Exception:  # noqa: BLE001
            pass
        try:
            target = firmware.available()["targets"].get(fw)
            if not target or not firmware.matches(target, dev):
                return {"apps": None}           # an older build: its package isn't in the index any more
            apps = firmware.bundled_apps(firmware.download(target, self.FW_DIR))
        except Exception:  # noqa: BLE001
            return {"apps": None}
        self.FW_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(apps), encoding="utf-8")
        return {"apps": apps}

    def fw_discard(self, remote, existed=False):
        """Remove a prepared package the user chose not to install. A package that was already on the
        SD card before (e.g. from qFlipper) is left where it was."""
        import device
        if not re.fullmatch(r"/ext/update/[\w.\-]+", remote or ""):
            return {"error": "Unexpected folder."}
        if existed:
            return {"ok": True, "kept": True}
        try:
            device.remove_tree(self._port(), fb.sdk_dirs(), remote)
            return {"ok": True}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def fw_install(self, remote, version):
        """Start the Flipper's own updater, then wait for it to come back on the new version."""
        import device
        if not re.fullmatch(r"/ext/update/[\w.\-]+", remote or ""):
            return {"error": "Unexpected folder."}

        def work(job):
            ok, level = self._battery_ok()
            if not ok:
                raise fb.BuildError(f"Battery is at {level}%. Charge it to at least 50% first.")
            job.stage("build", "installing")
            job.log(f"== update install {remote}/update.fuf")
            try:
                device.start_update(self._port(), fb.sdk_dirs(), remote)
            except device.DeviceError as e:
                raise fb.BuildError(str(e))
            job.log("OK The Flipper accepted the package and is restarting into its updater. Don't unplug it.")
            self._emit("fw_state", state="installing")
            t0, gone = time.time(), False
            while time.time() - t0 < 600:
                time.sleep(2)
                self._device_refresh.set()
                d = self._device
                if not d.get("connected"):
                    gone = True
                elif gone and not d.get("error") and d.get("version"):
                    v = str(version)
                    if v in (d.get("version"), d.get("commit")) or (d.get("commit") or "").startswith(v) or v.startswith(d.get("commit") or "~"):
                        job.log(f"OK Back on {d.get('fw_name')} {d.get('version')} ({d.get('commit')})")
                        return {"send_only": True, "quiet": True, "fw_done": {"version": d.get("version"), "fw_name": d.get("fw_name")}}
                    if time.time() - t0 > 240:
                        raise fb.BuildError(f"The Flipper is back but reports {d.get('version')} ({d.get('commit')}), not {version}. "
                                            "Check its screen; you can run the update again.")
            raise fb.BuildError("The Flipper hasn't come back after 10 minutes. Leave it plugged in and look at its screen. "
                                "If it's stuck, hold Left + Back to restart it; qFlipper's Repair can always restore it.")
        return self._run_job("firmware", work)

    # ------------------------------------------------ Bluetooth (experimental): mirror over BLE
    def ble_scan(self):
        import ble
        try:
            return {"flippers": ble.scan(15)}
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "not powered on" in msg.lower() or "powered_off" in msg.lower():
                return {"error": "Bluetooth is off on this PC. Turn it on in Windows settings, then scan again."}
            if "not available" in msg.lower() or "no bluetooth" in msg.lower():
                return {"error": "This PC has no Bluetooth adapter Windows can use."}
            return {"error": f"Bluetooth scan failed: {msg}"}

    def _ask_ble_pin(self):
        """Called during pairing: ask the page for the PIN on the Flipper's screen, wait up to 2 minutes."""
        self._ble_pin_evt = threading.Event()
        self._ble_pin_val = None
        self._emit("ble_pin")
        self._ble_pin_evt.wait(120)
        return self._ble_pin_val

    def ble_pin(self, pin):
        pin = re.sub(r"\D", "", str(pin or ""))
        self._ble_pin_val = pin or None
        ev = getattr(self, "_ble_pin_evt", None)
        if ev:
            ev.set()
        return {"ok": True}

    def ble_last(self):
        """The Flipper last mirrored over Bluetooth, for one-click reconnects."""
        return fb.load_settings().get("ble_last") or {}

    def ble_mirror_start(self, address, name=None):
        import ble
        if getattr(self, "_mirror", None):
            self.mirror_stop()
        self._frame = None
        self._frame_seq = 0

        def on_frame(data, orient):
            self._frame = (data, orient)
            self._frame_seq += 1
            rec = getattr(self, "_rec", None)
            if rec is not None and len(rec) < 3000:
                rec.append((time.time(), bytes(data), orient))

        def on_state(state, detail=""):
            self._emit("mirror", state=state, detail=detail)
        def on_state_remember(state, detail=""):
            if state == "live":
                fb.save_settings(ble_last={"address": address, "name": name or self._device.get("name") or "your Flipper"})
            on_state(state, detail)
        self._mirror = ble.BleMirror(address, on_frame, on_state_remember, ask_pin=self._ask_ble_pin)
        self._mirror.start()
        if not getattr(self, "_frame_thread", None):
            self._frame_thread = threading.Thread(target=self._frame_pump, daemon=True)
            self._frame_thread.start()
        return {"ok": True}

    # ------------------------------------------------ Wi-Fi dev board (ESP32-S2) flasher
    def devboard_info(self):
        import devboard
        try:
            off = devboard.official()
        except Exception as e:  # noqa: BLE001
            off, err = [], str(e)
        else:
            err = None
        return {"official": off, "ports": devboard.find_ports(), "error": err}

    def devboard_ports(self):
        import devboard
        return {"ports": devboard.find_ports()}

    def _plan_summary(self, plan, source):
        self._board_plan = (plan, source)
        return {"source": source, "parts": [[hex(a), label, len(b)] for a, label, b in plan]}

    def devboard_use_official(self, channel):
        import devboard
        t = next((x for x in devboard.official() if x["channel"] == channel), None)
        if not t:
            return {"error": "That firmware isn't on Flipper's server right now."}
        try:
            return self._plan_summary(devboard.plan_official(t, fb.ROOT / ".cache" / "devboard"), f"Black Magic {t['version']} ({t['channel']})")
        except devboard.BoardError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Download failed: {e}"}

    def devboard_use_file(self, name, b64=None, path=None):
        """A firmware file the user dropped (bytes from the page) or picked (path)."""
        import base64
        import devboard
        try:
            data = Path(path).read_bytes() if path else base64.b64decode(b64 or "")
            if len(data) > 16 * 1024 * 1024:
                return {"error": "That file is too big for the board's 4 MB flash."}
            return self._plan_summary(devboard.plan_from_bytes(name, data), name)
        except devboard.BoardError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't read that file: {e}"}

    def devboard_pick(self):
        p = self._pick(("Firmware (*.bin;*.zip;*.tgz;*.gz)", "All files (*.*)"))
        if not p:
            return {"cancelled": True}
        return self.devboard_use_file(Path(p).name, path=p)

    def devboard_flash(self, port):
        import devboard
        plan = getattr(self, "_board_plan", None)
        if not plan:
            return {"error": "Choose a firmware first."}
        if not any(x["port"] == port for x in devboard.find_ports()):
            return {"error": "The board isn't on that port any more. Plug it in with BOOT held and look again."}
        parts, source = plan

        def work(job):
            job.stage("send", source)
            job.log(f"== Flashing {source} to the Wi-Fi dev board on {port}")
            try:
                devboard.flash(port, parts, fb.ROOT / ".cache" / "devboard" / "flash", log=job.log)
            except devboard.BoardError as e:
                raise fb.BuildError(str(e))
            job.log("OK Flashed. The board restarts by itself.")
            return {"send_only": True, "quiet": True, "board_done": source}
        return self._run_job("files", work)

    # ------------------------------------------------ phone remote (home network only, off by default)
    def remote_start(self):
        import base64
        import io
        import remote
        if getattr(self, "_remote", None):
            r = self._remote
        else:
            def get_frame():
                f = self._frame
                m = getattr(self, "_mirror", None)
                return (getattr(self, "_frame_seq", 0), f[0] if f else None, f[1] if f else 0, bool(m and m.running))

            def press(key, kind):
                self.mirror_press(key, kind)

            def ensure_mirror():
                m = getattr(self, "_mirror", None)
                if not (m and m.running) and self._device.get("connected"):
                    self.mirror_start()
            r = remote.Remote(get_frame, press, ensure_mirror)
            try:
                r.start()
            except OSError as e:
                return {"error": str(e)}
            self._remote = r
        url = r.url()
        qr = ""
        try:
            import qrcode
            img = qrcode.make(url, box_size=6, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001
            pass
        return {"url": url, "qr": qr, "clients": sorted(r.clients)}

    def remote_stop(self):
        r = getattr(self, "_remote", None)
        if r:
            r.stop()
            self._remote = None
        return {"ok": True}

    def remote_status(self):
        r = getattr(self, "_remote", None)
        return {"on": bool(r), "clients": sorted(r.clients) if r else []}

    # ------------------------------------------------ app dev kit
    def devkit_templates(self):
        import devkit
        return {"templates": [[k, v[0], v[1]] for k, v in devkit.TEMPLATES.items()], "categories": devkit.CATEGORIES,
                "default_dir": str(fb.ROOT / "projects"), "vscode": bool(__import__("shutil").which("code"))}

    def devkit_pick_dir(self):
        p = self._pick(folder=True)
        return {"path": p} if p else {"cancelled": True}

    def devkit_create(self, name, template, category, parent=None):
        import devkit
        try:
            r = devkit.create(parent or (fb.ROOT / "projects"), name, template, category)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        return r

    def devkit_open_code(self, folder):
        import shutil
        code = shutil.which("code")
        if not code or not Path(folder).is_dir():
            return {"error": "VS Code isn't installed (or not on PATH)."}
        subprocess.Popen([code, folder], creationflags=0x08000000 if os.name == "nt" else 0)
        return {"ok": True}

    def devkit_send_js(self, local_js):
        """Copy a JS app to /ext/apps/Scripts/ and return its path there."""
        import device
        p = Path(local_js)
        if p.suffix.lower() != ".js" or not p.is_file():
            return {"error": "Not a .js file."}
        remote = f"/ext/apps/Scripts/{p.name}"
        try:
            device.write_file(self._port(), fb.sdk_dirs(), remote, p.read_bytes())
            return {"ok": True, "path": remote}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    # ------------------------------------------------ NFC: user MIFARE Classic key dictionary
    MF_DICT = "/ext/nfc/assets/mf_classic_dict_user.nfc"

    def mf_dict_load(self):
        import device
        try:
            port = self._port()
            if not device.file_exists(port, fb.sdk_dirs(), self.MF_DICT):
                return {"keys": [], "exists": False}
            text = device.read_file(port, fb.sdk_dirs(), self.MF_DICT).decode("utf-8", "replace")
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        keys = []
        for line in text.splitlines():
            k = line.strip().upper()
            if re.fullmatch(r"[0-9A-F]{12}", k) and k not in keys:
                keys.append(k)
        return {"keys": keys, "exists": True}

    def mf_dict_save(self, keys):
        import device
        clean = []
        for k in keys or []:
            k = re.sub(r"\s+", "", str(k)).upper()
            if not re.fullmatch(r"[0-9A-F]{12}", k):
                return {"error": f"'{k}' isn't a MIFARE key (12 hex characters)."}
            if k not in clean:
                clean.append(k)
        text = "# MIFARE Classic user keys, managed with Flipper Workbench\n" + "".join(f"{k}\n" for k in clean)
        try:
            device.write_file(self._port(), fb.sdk_dirs(), self.MF_DICT, text.encode("ascii"))
            return {"ok": True, "count": len(clean)}
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def sd_map(self):
        import device
        try:
            port = self._port()
            r = device.sd_tree(port, fb.sdk_dirs())
            try:
                r["free_kib"] = device.sd_free_kib(port, fb.sdk_dirs())
            except Exception:  # noqa: BLE001
                r["free_kib"] = None
            return r
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}

    def cleanup_scan(self):
        import device
        try:
            return device.cleanup_scan(self._port(), fb.sdk_dirs())
        except (fb.BuildError, device.DeviceError) as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't scan the SD card: {e}"}

    def cleanup_delete(self, paths):
        """Delete what the user ticked in Clean up (the UI confirms first). Only SD card paths, never system folders."""
        keep = ("/ext", "/ext/apps", "/ext/update", "/ext/apps_data", "/int")
        paths = [p for p in paths if isinstance(p, str) and p.startswith("/ext/") and ".." not in p
                 and p.rstrip("/") not in keep]
        if not paths:
            return {"error": "Nothing to delete."}

        def work(job):
            import device
            job.stage("send", f"{len(paths)} item(s)")
            for i, p in enumerate(paths, 1):
                if job.cancelled.is_set():
                    break
                try:
                    device.remove_tree(self._port(), fb.sdk_dirs(), p)
                except device.DeviceError as e:
                    raise fb.BuildError(f"{p}: {e}")
                job.log(f"OK Deleted {p}")
            return {"files_changed": True, "flipper_changed": True, "send_only": True, "quiet": True, "cleaned": len(paths)}
        return self._run_job("files", work)

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

    AUTO_KEEP = 5          # automatic backups kept per Flipper; manual ones are never pruned
    AUTO_EVERY = 20 * 3600  # at most one automatic backup per Flipper a day

    def auto_backup_due(self):
        """Should plugging in this Flipper start an automatic backup now?"""
        s = fb.load_settings()
        dev = self._device
        if not s.get("auto_backup", True) or not dev.get("connected") or dev.get("error") or self._job:
            return {"due": False}
        last = (s.get("auto_backup_last") or {}).get(dev.get("name") or "Flipper", 0)
        return {"due": time.time() - last > self.AUTO_EVERY, "last": last}

    def auto_backup_plan(self):
        """What a due automatic backup would save, or why it's skipped. Skips when nothing changed
        (same files and sizes) since this Flipper's last automatic backup."""
        import device
        due = self.auto_backup_due()
        if not due.get("due"):
            return {"due": False}
        try:
            folders = device.folder_sizes(self._port(), fb.sdk_dirs())
            names = [f["name"] for f in folders if not f.get("file") and not f["name"].startswith(".")
                     and f["name"] not in self.BK_SKIP and (f["name"] in self.BK_DEFAULT or f["size"] < 1048576)]
            if not names:
                return {"due": False}
            label = re.sub(r"[^\w\-]+", "", self._device.get("name") or "Flipper") or "Flipper"
            autos = sorted(self.BACKUP_DIR.glob(f"{label}_*_auto.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
            if autos:
                last = device.backup_index(autos[0])
                if last is not None and last == [tuple(x) for x in device.list_files(self._port(), fb.sdk_dirs(), names)]:
                    stamp = fb.load_settings().get("auto_backup_last") or {}
                    stamp[self._device.get("name") or "Flipper"] = int(time.time())
                    fb.save_settings(auto_backup_last=stamp)
                    return {"due": False, "unchanged": True}
            return {"due": True, "names": names}
        except Exception as e:  # noqa: BLE001
            return {"due": False, "error": str(e)}

    def _prune_auto(self, label):
        autos = sorted(self.BACKUP_DIR.glob(f"{label}_*_auto.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in autos[self.AUTO_KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass

    def backup_start(self, names, auto=False):
        names = [n for n in names if n and "/" not in n and ".." not in n]
        if not names:
            return {"error": "Pick at least one folder."}
        dev = dict(self._device)
        stamp = time.strftime("%Y-%m-%d_%H%M")
        label = re.sub(r"[^\w\-]+", "", dev.get("name") or "Flipper") or "Flipper"
        path = self.BACKUP_DIR / f"{label}_{stamp}{'_auto' if auto else ''}.zip"
        if auto:   # mark it now, so a failed or cancelled run doesn't retry on every plug-in
            last = fb.load_settings().get("auto_backup_last") or {}
            last[dev.get("name") or "Flipper"] = int(time.time())
            fb.save_settings(auto_backup_last=last)

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
            if auto:
                self._prune_auto(label)
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
            rec = getattr(self, "_rec", None)
            if rec is not None and len(rec) < 3000:
                rec.append((time.time(), bytes(data), orient))

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

    # ------------------------------------------------ GIF recording of the mirrored screen
    REC_MAX = 60   # seconds

    def record_start(self):
        m = getattr(self, "_mirror", None)
        if not m or not m.running:
            return {"error": "Start mirroring first."}
        self._rec_t0 = time.time()
        self._rec = []
        if self._frame is not None:                 # the screen as it is now, even if nothing moves
            self._rec.append((self._rec_t0, bytes(self._frame[0]), self._frame[1]))
        return {"ok": True, "max": self.REC_MAX}

    def record_stop(self, save=True):
        rec, self._rec = getattr(self, "_rec", None), None
        if not rec or not save:
            return {"cancelled": True}
        t_end = min(time.time(), self._rec_t0 + self.REC_MAX)
        rec = [r for r in rec if r[0] <= t_end]
        try:
            p = save_gif(rec, t_end, fb.ROOT / "recordings", framed=bool(fb.load_settings().get("frame_shots")))
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't save the GIF: {e}"}
        _reveal(p)
        return {"path": str(p), "frames": len(rec), "seconds": round(t_end - self._rec_t0, 1)}

    def set_fullscreen(self, on):
        """Real full screen for the mirrored Flipper screen (the page shows only the screen meanwhile)."""
        on = bool(on)
        if self._window is not None and on != getattr(self, "_fullscreen", False):
            self._fullscreen = on
            try:
                self._window.toggle_fullscreen()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True}

    # ------------------------------------------------ Flipper CLI terminal
    def cli_run(self, command):
        """Run one command in the Flipper's CLI; output arrives as `cli` events, then one with done=True."""
        import device
        if getattr(self, "_cli_busy", False):
            return {"error": "A command is still running. Press Stop first."}
        try:
            port = self._port()
        except fb.BuildError as e:
            return {"error": str(e)}
        self._cli_busy = True
        self._cli_stop = threading.Event()

        def work():
            ended, error = "done", None
            try:
                ended = device.cli_run(port, fb.sdk_dirs(), command,
                                       lambda text: self._emit("cli", text=text),
                                       should_stop=self._cli_stop.is_set)
            except device.DeviceError as e:
                error = str(e)
            except Exception as e:  # noqa: BLE001
                error = f"Something went wrong: {e}"
            finally:
                self._cli_busy = False
                self._emit("cli", done=True, ended=ended, error=error)
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def log_follow_start(self, level="debug"):
        """Follow the Flipper's log; other USB jobs still work (the follower steps aside for them)."""
        import device
        if getattr(self, "_logf", None) and self._logf.running:
            return {"ok": True}
        try:
            port = self._port()
        except fb.BuildError as e:
            return {"error": str(e)}
        self._logf = device.LogFollower(port, fb.sdk_dirs(), level,
                                        lambda text: self._emit("cli", text=text, log=True),
                                        lambda reason: self._emit("cli", log=True, done=True, ended="stopped" if reason == "stopped" else "error",
                                                                  error=None if reason == "stopped" else reason))
        self._logf.start()
        return {"ok": True}

    def log_follow_stop(self):
        lf = getattr(self, "_logf", None)
        if lf:
            lf.stop()
        return {"ok": True}

    def cli_stop(self):
        ev = getattr(self, "_cli_stop", None)
        if ev:
            ev.set()
        return {"ok": True}

    def set_auto_mirror(self, on):
        fb.save_settings(auto_mirror=bool(on))
        return {"ok": True}

    def set_framed(self, on):
        fb.save_settings(frame_shots=bool(on))
        return {"ok": True}

    def save_screenshot_framed(self):
        """The current mirrored screen inside a drawn Flipper, as PNG."""
        if self._frame is None:
            return {"error": "No screen yet."}
        d = fb.ROOT / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        p = d / time.strftime("flipper_%Y-%m-%d_%H%M%S.png")
        _framed(*self._frame).convert("RGB").save(p)
        _reveal(p)
        return {"path": str(p)}

    def save_screenshot(self, png_b64):
        import base64
        d = fb.ROOT / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        p = d / time.strftime("flipper_%Y-%m-%d_%H%M%S.png")
        p.write_bytes(base64.b64decode(png_b64.split(",", 1)[-1]))
        _reveal(p)
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
            if _vtuple(latest) > _vtuple(os.environ.get("FW_PRETEND_VERSION") or fb.APP_VERSION):
                asset = next((a for a in rel.get("assets") or [] if a.get("name") == SELF_ZIP), None)
                digest = (asset or {}).get("digest") or ""
                self._latest = {"version": latest.lstrip("v"), "url": (asset or {}).get("browser_download_url"),
                                "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None}
                can = bool(fb.FROZEN and os.name == "nt" and self._latest["url"] and self._latest["sha256"])
                self._emit("update", version=latest.lstrip("v"), url=rel.get("html_url") or fb.REPO_URL + "/releases",
                           can_install=can)
        except Exception:  # noqa: BLE001
            pass

    def self_update(self):
        """Download the new release, check its SHA-256 (GitHub's digest), unpack it next to this app,
        then quit; a helper swaps in the new program files (data stays) and restarts the app."""
        info = getattr(self, "_latest", None)
        if not info or not info.get("url") or not info.get("sha256"):
            return {"error": "No update to install."}
        if not (fb.FROZEN and os.name == "nt"):
            return {"error": "Self-update works in the installed Windows app; download the release instead."}
        if self._job:
            return {"error": "Wait for the current job to finish first."}
        work = fb.ROOT / ".cache" / "selfupdate"
        try:
            zip_path = _download_checked(info["url"], info["sha256"], work / SELF_ZIP,
                                         lambda d, t: self._emit("fprog", label="Downloading the update", done=d, total=t))
            staged = stage_update(zip_path, work / "new")
            helper = write_update_helper(staged, Path(sys.executable).parent, os.getpid(), work)
        except Exception as e:  # noqa: BLE001
            return {"error": f"Update failed, nothing was changed: {e}"}
        subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"), "/d", "/c", str(helper)],
                         creationflags=0x08000000 | 0x00000200,  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: survives our exit
                         close_fds=True)
        threading.Timer(0.8, lambda: self._window and self._window.destroy()).start()
        return {"ok": True, "version": info["version"]}

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
        if not p.is_file():
            p.mkdir(parents=True, exist_ok=True)
        _reveal(p)
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
        webview.start(gui="edgechromium" if os.name == "nt" else None, http_server=True, debug=bool(os.environ.get("FB_DEBUG")),
                      icon=str(fb.ICON))
    except Exception as e:  # noqa: BLE001
        _no_webview2_message(e)
