"""Flipper Builder window: a pywebview (WebView2) shell around ui/index.html.

Python -> JS: batched events via window.fb.onEvents([...]).
JS -> Python: the public methods of Api (pywebview's js_api bridge).
"""

import ctypes
import json
import os
import queue
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import flipper_builder as fb

MAX_RECENT = 8


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


# ---------------------------------------------------------------- API exposed to JS

class Api:
    def __init__(self):
        self._window = None
        self._events = queue.Queue()
        self._job = None
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    # -- plumbing
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

    def _busy(self):
        return self._job is not None

    def _run_job(self, label, fn):
        job = fb.Job(
            log=lambda line: self._emit("log", line=line),
            stage=lambda name, detail="": self._emit("stage", stage=name, detail=detail),
            progress=lambda v: self._emit("progress", value=v),
            result=lambda res: self._emit("result", result=res),
        )
        with self._lock:
            if self._job:
                return {"error": "Already working on something."}
            self._job = job

        def worker():
            try:
                payload = fn(job) or {}
                self._emit("finished", ok=True, **payload)
            except fb.Cancelled:
                self._emit("log", line="!! Cancelled.")
                self._emit("finished", ok=False, cancelled=True, error="Cancelled.")
            except fb.BuildError as e:
                self._emit("log", line="!! " + str(e))
                self._emit("finished", ok=False, error=str(e))
            except Exception as e:  # noqa: BLE001
                self._emit("log", line=f"!! Unexpected error: {e!r}")
                self._emit("finished", ok=False, error="Unexpected error (see log).")
            finally:
                self._job = None
        threading.Thread(target=worker, daemon=True, name=label).start()
        return {"ok": True}

    # -- called from JS
    def init(self):
        s = fb.load_settings()
        return {
            "version": fb.APP_VERSION,
            "repo": fb.REPO_URL,
            "firmwares": [{"id": k, "label": v[0], "short": v[1], "sdk": fb.sdk_version(k)}
                          for k, v in fb.FIRMWARES.items()],
            "fw": s.get("fw", "official") if s.get("fw") in fb.FIRMWARES else "official",
            "send": bool(s.get("send", False)),
            "recent": s.get("recent", []),
            "clipboard": _github_link(_clip_get()),
            "out_dir": str(fb.OUT_DIR),
        }

    def clipboard(self):
        return _github_link(_clip_get())

    def set_prefs(self, fw, send):
        if fw in fb.FIRMWARES:
            fb.save_settings(fw=fw, send=bool(send))
        return True

    def build(self, url, fw, send):
        try:
            owner, repo, _ = fb.parse_github_url(url)
        except fb.BuildError as e:
            return {"error": str(e)}
        if fw not in fb.FIRMWARES:
            return {"error": "Unknown firmware."}
        fb.save_settings(fw=fw, send=bool(send))

        def work(job):
            results, out = fb.build(url, fw, bool(send), job)
            ok = sum(r["ok"] for r in results)
            recent = [r for r in fb.load_settings().get("recent", []) if r.get("url") != url]
            recent.insert(0, {"url": url, "fw": fw, "name": ", ".join(r["name"] for r in results)[:60] or repo,
                              "repo": f"{owner}/{repo}", "ok": ok == len(results), "t": int(time.time())})
            fb.save_settings(recent=recent[:MAX_RECENT])
            return {"built": ok, "total": len(results), "out": str(out), "recent": recent[:MAX_RECENT],
                    "sdk": fb.sdk_version(fw), "fw": fw}
        return self._run_job("build", work)

    def update_sdk(self, fw):
        def work(job):
            job.stage("sdk", fb.FIRMWARES[fw][1])
            fb.ensure_sdk(job, fw, force=True)
            job.log(f"OK {fb.FIRMWARES[fw][0]} SDK is now {fb.sdk_version(fw)}")
            return {"sdk_only": True, "sdk": fb.sdk_version(fw), "fw": fw}
        return self._run_job("sdk", work)

    def send(self, app_dir, fw):
        if not Path(app_dir).exists():
            return {"error": "That build's source is gone (another build replaced it). Build it again."}

        def work(job):
            job.stage("send", Path(app_dir).name)
            fb.send_to_flipper(job, app_dir, fw)
            job.log("OK Sent. It should be open on your Flipper now.")
            return {"send_only": True}
        return self._run_job("send", work)

    def cancel(self):
        job = self._job
        if job:
            job.cancel()
        return True

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
    msg = ("Flipper Builder needs Microsoft Edge WebView2, which is missing or broken on this PC.\n\n"
           "Install the 'Evergreen Bootstrapper' from\n"
           "https://developer.microsoft.com/microsoft-edge/webview2/\nthen open Flipper Builder again.\n\n"
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
        width=1120, height=760, min_size=(900, 640), background_color="#0d0d0d",
    )
    api._window = window
    window.events.shown += lambda: _dark_title_bar(window)
    try:
        webview.start(gui="edgechromium", http_server=True, debug=bool(os.environ.get("FB_DEBUG")),
                      icon=str(fb.ICON))
    except Exception as e:  # noqa: BLE001
        _no_webview2_message(e)
