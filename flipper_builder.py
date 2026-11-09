"""Flipper Builder: paste a GitHub link to a Flipper Zero app, get a .fap.

GUI:  pythonw flipper_builder.py
CLI:  python flipper_builder.py <github-url> [--fw official|official-dev|momentum|unleashed] [--send]
"""

import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import logging
import urllib.request
import zipfile

APP_NAME = "Flipper Builder"
APP_ID = "c3rb3ruszx.FlipperBuilder"
FROZEN = getattr(sys, "frozen", False)
# Frozen app: data lives next to the .exe. From source: next to this file.
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
ROOT = Path(os.environ.get("FLIPPER_BUILDER_HOME", ROOT))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ICON = BUNDLE / "assets" / "icon.ico"
# .cache holds re-downloadable stuff (SDKs, temp clones); safe to delete
SDK_DIR = ROOT / ".cache" / "sdk"
WORK_DIR = ROOT / ".cache" / "work"
OUT_DIR = ROOT / "output"
SETTINGS = ROOT / "settings.json"
SDK_MAX_AGE = 24 * 3600  # refresh the SDK at most once a day

FIRMWARES = {
    "official": ("Official firmware (release)", ["--channel=release"]),
    "official-dev": ("Official firmware (dev)", ["--channel=dev"]),
    "momentum": ("Momentum", ["--index-url=https://up.momentum-fw.dev/firmware/directory.json", "--channel=release"]),
    "momentum-dev": ("Momentum (dev)", ["--index-url=https://up.momentum-fw.dev/firmware/directory.json", "--channel=dev"]),
    "unleashed": ("Unleashed", ["--index-url=https://up.unleashedflip.com/directory.json", "--channel=release"]),
}

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def run(cmd, cwd=None, env=None, log=print, check=True):
    """Run a command, streaming its output to log. Returns (code, output)."""
    log("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.Popen(
        [str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    lines = []
    for line in p.stdout:
        line = ANSI.sub("", line.rstrip())
        lines.append(line)
        log(line)
    p.wait()
    if check and p.returncode != 0:
        raise BuildError(f"Command failed (exit {p.returncode}): {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    return p.returncode, "\n".join(lines)


def rmtree(path):
    def onerror(func, p, _):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if Path(path).exists():
        shutil.rmtree(path, onerror=onerror)


def parse_github_url(url):
    """Return (owner, repo, path_parts) where path_parts is what follows /tree/ or /blob/."""
    url = url.strip().strip('"').strip()
    m = re.match(r"^git@github\.com:([^/]+)/(.+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2), []
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^(www\.)?github\.com/", "", url)
    url = url.split("?")[0].split("#")[0].rstrip("/")
    parts = [p for p in url.split("/") if p]
    if len(parts) < 2 or "." in parts[0]:  # GitHub usernames never contain dots
        raise BuildError("That doesn't look like a GitHub link. Expected github.com/<owner>/<repo>...")
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    rest = parts[2:]
    if rest and rest[0] in ("tree", "blob"):
        rest = rest[1:]
    elif rest:
        rest = []  # e.g. /releases, /issues: just use the repo
    return owner, repo, rest


def resolve_ref(remote, parts, env, log):
    """Split path_parts into (ref, subdir). ref None = default branch."""
    if not parts:
        return None, "", False
    _, out = run(["git", "ls-remote", "--heads", "--tags", remote], env=env, log=lambda _: None)
    refs = set()
    for line in out.splitlines():
        if "\trefs/" in line:
            name = line.split("\t", 1)[1]
            name = re.sub(r"^refs/(heads|tags)/", "", name).removesuffix("^{}")
            refs.add(name)
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i])
        if cand in refs:
            return cand, "/".join(parts[i:]), False
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", parts[0]):
        return parts[0], "/".join(parts[1:]), True
    raise BuildError(f"Couldn't find branch or tag '{parts[0]}' in that repo.")


def fam_info(fam_path):
    text = fam_path.read_text(encoding="utf-8", errors="replace")
    def grab(key):
        m = re.search(key + r"\s*=\s*[\"']([^\"']+)[\"']", text)
        return m.group(1) if m else None
    types = re.findall(r"apptype\s*=\s*FlipperAppType\.(\w+)", text)
    return {
        "appid": grab("appid") or fam_path.parent.name,
        "name": grab("name") or fam_path.parent.name,
        "category": grab("fap_category") or "Misc",
        "types": types,
        "dir": fam_path.parent,
    }


def find_apps(base):
    """Top-most folders holding an application.fam with an external (.fap) app."""
    apps, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in (".git", "dist", "build", ".ufbt", "node_modules", ".vscode")]
        if "application.fam" in filenames:
            info = fam_info(Path(dirpath) / "application.fam")
            if any("EXTERNAL" in t for t in info["types"]) or not info["types"]:
                apps.append(info)
                dirnames[:] = []  # don't descend into an app's own subfolders
            else:
                skipped.append(info)
    return apps, skipped


def ufbt_env(fw):
    env = os.environ.copy()
    home = str(SDK_DIR / fw)
    env["UFBT_HOME"] = env["UFBT_STATE_DIR"] = home
    env["FBT_TOOLCHAIN_PATH"] = str(SDK_DIR / "_toolchain")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


class _LogForward(logging.Handler):
    def __init__(self, log):
        super().__init__()
        self.log = log
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.log(self.format(record))


def ensure_sdk(fw, log, force=False):
    """Download/refresh the SDK using ufbt's bootstrap module, in-process."""
    env = ufbt_env(fw)
    stamp = SDK_DIR / fw / ".last_update"
    fresh = stamp.exists() and time.time() - stamp.stat().st_mtime < SDK_MAX_AGE
    if fresh and not force and (SDK_DIR / fw / "current").exists():
        return env
    log(f"== Getting the latest {FIRMWARES[fw][0]} SDK")
    from ufbt.bootstrap import bootstrap_cli
    os.environ["FBT_TOOLCHAIN_PATH"] = env["FBT_TOOLCHAIN_PATH"]
    handler = _LogForward(log)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    try:
        code = bootstrap_cli(["--ufbt-home", env["UFBT_HOME"], "update", *FIRMWARES[fw][1]])
    except SystemExit as e:
        code = e.code
    finally:
        root_logger.removeHandler(handler)
    if code not in (0, None) or not (SDK_DIR / fw / "current").exists():
        raise BuildError("Couldn't download the SDK. Check your internet connection.")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    return env


def run_ufbt(app_dir, env, log, *targets):
    """What the `ufbt` command does: enter Flipper's toolchain env and run SCons.
    The toolchain ships its own Python, so this works from a frozen .exe too."""
    home = env["UFBT_STATE_DIR"]
    fbtenv = Path(home) / "current" / "scripts" / "toolchain" / "fbtenv.cmd"
    scripts = Path(home) / "current" / "scripts" / "ufbt"
    if not fbtenv.exists() or not scripts.exists():
        raise BuildError("SDK is incomplete. Press 'Update SDK now' and try again.")
    args = " ".join(f'"{a}"' for a in ("-m", "SCons", "-Q", "--warn=target-not-built",
                                       "-C", scripts, f"UFBT_APP_DIR={app_dir}", *targets))
    cmdline = f'cmd /d /s /c "call "{fbtenv}" env & python {args}"'
    log("$ ufbt " + " ".join(targets))
    p = subprocess.Popen(cmdline, cwd=app_dir, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    for line in p.stdout:
        log(ANSI.sub("", line.rstrip()))
    return p.wait()


def have_git():
    return shutil.which("git") is not None


def download_zip(owner, repo, ref, dest, log):
    """Fallback when git isn't installed: GitHub source zip (no submodules)."""
    url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref or 'HEAD'}"
    log(f"$ download {url}")
    tmp = dest.parent / (dest.name + ".zip")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:  # noqa: BLE001
        raise BuildError(f"Couldn't download the repo ({e}). Is the link right and the repo public?")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(dest.parent / (dest.name + ".unzip"))
    tmp.unlink()
    inner = next((dest.parent / (dest.name + ".unzip")).iterdir())
    inner.rename(dest)
    rmtree(dest.parent / (dest.name + ".unzip"))


# ---------------------------------------------------------------- main job

def build(url, fw="official", send=False, log=print):
    """Clone, build every app found, copy .faps to output/. Returns list of results."""
    owner, repo, parts = parse_github_url(url)
    remote = f"https://github.com/{owner}/{repo}.git"
    env = ufbt_env(fw)

    log(f"== Repo: {owner}/{repo}   Firmware: {FIRMWARES[fw][0]}")
    use_git = have_git()
    if use_git:
        ref, subdir, is_sha = resolve_ref(remote, parts, env, log)
    else:  # no git: assume a branch name without slashes
        ref, subdir, is_sha = (parts[0] if parts else None), "/".join(parts[1:]), False

    src = WORK_DIR / f"{owner}__{repo}"
    rmtree(src)
    src.parent.mkdir(parents=True, exist_ok=True)
    log("== Downloading source")
    git = ["git", "-c", "core.longpaths=true", "-c", "advice.detachedHead=false"]
    if not use_git:
        log("(git isn't installed, downloading a zip instead; submodules won't be included)")
        download_zip(owner, repo, ref, src, log)
    elif is_sha:
        run(git + ["clone", "--recurse-submodules", remote, src], env=env, log=log)
        run(git + ["checkout", ref], cwd=src, env=env, log=log)
        run(git + ["submodule", "update", "--init", "--recursive"], cwd=src, env=env, log=log)
    else:
        cmd = git + ["clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules"]
        if ref:
            cmd += ["--branch", ref]
        run(cmd + [remote, src], env=env, log=log)

    base = src / subdir if subdir else src
    if base.is_file():
        base = base.parent
    if not base.exists():
        raise BuildError(f"Folder '{subdir}' isn't in that repo.")

    apps, skipped = find_apps(base)
    if not apps:
        msg = "No Flipper app (application.fam) found in that link."
        if skipped:
            msg += " Found only firmware-internal apps, which can't be built as .fap."
        raise BuildError(msg)
    if len(apps) > 25:
        raise BuildError(
            f"That link contains {len(apps)} apps (probably a whole firmware or app collection). "
            "Open the folder of the app you want on GitHub and paste that link instead.")
    log(f"== Found {len(apps)} app(s): " + ", ".join(a["name"] for a in apps))

    env = ensure_sdk(fw, log)
    out = OUT_DIR / f"{repo}__{fw}"
    rmtree(out)
    out.mkdir(parents=True)

    results = []
    for app in apps:
        log(f"\n== Building {app['name']} ({app['dir'].relative_to(src) if app['dir'] != src else '/'})")
        rmtree(app["dir"] / "dist")
        code = run_ufbt(app["dir"], env, log)
        faps = sorted((app["dir"] / "dist").glob("*.fap"))
        if code != 0 or not faps:
            log(f"!! {app['name']} failed to build.")
            results.append({"app": app, "ok": False, "faps": []})
            continue
        copied = []
        for f in faps:
            dest = out / f.name
            shutil.copy2(f, dest)
            copied.append(dest)
        log(f"OK {app['name']}: " + ", ".join(f.name for f in copied)
            + f"   (on the SD card it goes in apps/{app['category']}/)")
        if send:
            log(f"== Sending {app['name']} to the Flipper over USB")
            code = run_ufbt(app["dir"], env, log, "launch")
            if code != 0:
                log("!! Couldn't send. Is the Flipper plugged in, unlocked, and qFlipper closed?")
        results.append({"app": app, "ok": True, "faps": copied})

    ok = sum(r["ok"] for r in results)
    log(f"\n== Done: {ok}/{len(results)} built. Files are in {out}")
    return results, out


# ---------------------------------------------------------------- settings

def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except Exception:
        return {}


def save_settings(**kw):
    s = load_settings()
    s.update(kw)
    try:
        SETTINGS.write_text(json.dumps(s, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------- GUI

def gui():
    import tkinter as tk
    from tkinter import ttk

    BG, PANEL, FG, MUTED, ACCENT, OK, ERR = "#16181d", "#1f2229", "#e8e8ea", "#9aa0a6", "#ff8200", "#4cc38a", "#ff6b6b"

    if os.name == "nt":  # own taskbar identity, so Windows shows our icon, not Python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    root.title(APP_NAME)
    if ICON.exists():
        try:
            root.iconbitmap(default=str(ICON))
        except tk.TclError:
            pass
    root.geometry("860x600")
    root.minsize(640, 420)
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=("Segoe UI", 10))
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TEntry", padding=6, foreground=FG, insertcolor=FG)
    style.configure("TCombobox", padding=4, foreground=FG, arrowcolor=FG)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    style.configure("Accent.TButton", background=ACCENT, foreground="#111", font=("Segoe UI Semibold", 10), padding=(16, 6), borderwidth=0)
    style.map("Accent.TButton", background=[("disabled", "#5a4a3a"), ("active", "#ff9a33")])
    style.configure("TButton", background=PANEL, foreground=FG, padding=(12, 6), borderwidth=0)
    style.map("TButton", background=[("active", "#2b2f38"), ("disabled", PANEL)], foreground=[("disabled", MUTED)])

    settings = load_settings()
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    frm.configure(style="TFrame")
    style.configure("TFrame", background=BG)

    ttk.Label(frm, text="Flipper Builder", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
    ttk.Label(frm, text="Paste a GitHub link to a Flipper Zero app (repo, branch or folder) and press Build.",
              style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

    url_var = tk.StringVar()
    entry = ttk.Entry(frm, textvariable=url_var)
    entry.grid(row=2, column=0, columnspan=3, sticky="ew", padx=(0, 8))
    build_btn = ttk.Button(frm, text="Build", style="Accent.TButton")
    build_btn.grid(row=2, column=3, sticky="ew")

    opts = ttk.Frame(frm)
    opts.grid(row=3, column=0, columnspan=4, sticky="w", pady=10)
    ttk.Label(opts, text="My Flipper runs:").pack(side="left")
    labels = {v[0]: k for k, v in FIRMWARES.items()}
    fw_var = tk.StringVar(value=FIRMWARES.get(settings.get("fw", "official"), FIRMWARES["official"])[0])
    ttk.Combobox(opts, textvariable=fw_var, values=list(labels), state="readonly", width=28).pack(side="left", padx=(6, 18))
    send_var = tk.BooleanVar(value=settings.get("send", False))
    ttk.Checkbutton(opts, text="Send to Flipper over USB after building", variable=send_var).pack(side="left")

    log_frame = tk.Frame(frm, bg=PANEL)
    log_frame.grid(row=4, column=0, columnspan=4, sticky="nsew")
    style.configure("Dark.Vertical.TScrollbar", background="#2b2f38", troughcolor=PANEL,
                    bordercolor=PANEL, arrowcolor=MUTED, lightcolor="#2b2f38", darkcolor="#2b2f38")
    style.map("Dark.Vertical.TScrollbar", background=[("active", "#3a3f4a")])
    scroll = ttk.Scrollbar(log_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
    scroll.pack(side="right", fill="y")
    log_box = tk.Text(log_frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", yscrollcommand=scroll.set,
                      font=("Cascadia Mono", 9), wrap="word", borderwidth=0, padx=10, pady=8,
                      highlightthickness=0)
    log_box.pack(side="left", fill="both", expand=True)
    scroll.configure(command=log_box.yview)
    for tag, color in (("head", ACCENT), ("ok", OK), ("err", ERR), ("cmd", MUTED)):
        log_box.tag_configure(tag, foreground=color)
    log_box.insert("end", "The build log shows up here.\n\n"
                          "Tip: copy a GitHub link before opening the app and it's pasted in for you.\n"
                          "The .fap opens in a folder when it's done.", "cmd")
    log_box.configure(state="disabled")

    bottom = ttk.Frame(frm)
    bottom.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(10, 0))
    status = ttk.Label(bottom, text="Ready.", style="Muted.TLabel")
    status.pack(side="left")
    open_btn = ttk.Button(bottom, text="Open output folder")
    open_btn.pack(side="right")
    sdk_btn = ttk.Button(bottom, text="Update SDK now")
    sdk_btn.pack(side="right", padx=8)

    frm.columnconfigure(0, weight=1)
    frm.rowconfigure(4, weight=1)

    q = queue.Queue()
    state = {"out": OUT_DIR, "busy": False}

    def log(line):
        q.put(("log", line))

    def pump():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "log":
                    tag = None
                    if val.startswith("=="):
                        tag = "head"
                    elif val.startswith("OK "):
                        tag = "ok"
                    elif val.startswith("!!") or "error:" in val.lower():
                        tag = "err"
                    elif val.startswith("$ "):
                        tag = "cmd"
                    log_box.configure(state="normal")
                    log_box.insert("end", val + "\n", tag)
                    log_box.see("end")
                    log_box.configure(state="disabled")
                elif kind == "done":
                    state["busy"] = False
                    build_btn.configure(state="normal")
                    sdk_btn.configure(state="normal")
                    status.configure(text=val)
        except queue.Empty:
            pass
        root.after(80, pump)

    def start(job, label):
        if state["busy"]:
            return
        state["busy"] = True
        build_btn.configure(state="disabled")
        sdk_btn.configure(state="disabled")
        status.configure(text=label)
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        threading.Thread(target=job, daemon=True).start()

    def do_build(*_):
        url = url_var.get().strip()
        if not url:
            status.configure(text="Paste a GitHub link first.")
            return
        fw = labels[fw_var.get()]
        send = send_var.get()
        save_settings(fw=fw, send=send)

        def job():
            try:
                results, out = build(url, fw, send, log)
                state["out"] = out
                ok = sum(r["ok"] for r in results)
                msg = f"Built {ok} of {len(results)} app(s)."
                if ok:
                    os.startfile(out)
            except BuildError as e:
                log("!! " + str(e))
                msg = "Failed: " + str(e)
            except Exception as e:  # noqa: BLE001
                log(f"!! Unexpected error: {e!r}")
                msg = "Failed (see log)."
            q.put(("done", msg))
        start(job, "Building... (first build downloads the toolchain, a few minutes)")

    def do_sdk():
        fw = labels[fw_var.get()]
        def job():
            try:
                ensure_sdk(fw, log, force=True)
                msg = "SDK updated."
            except Exception as e:  # noqa: BLE001
                log("!! " + str(e))
                msg = "SDK update failed."
            q.put(("done", msg))
        start(job, "Updating SDK...")

    def do_open():
        target = state["out"] if Path(state["out"]).exists() else OUT_DIR
        Path(target).mkdir(parents=True, exist_ok=True)
        os.startfile(target)

    build_btn.configure(command=do_build)
    sdk_btn.configure(command=do_sdk)
    open_btn.configure(command=do_open)
    entry.bind("<Return>", do_build)

    try:  # prefill from clipboard if it holds a GitHub link
        clip = root.clipboard_get().strip()
        if "github.com/" in clip and "\n" not in clip:
            url_var.set(clip)
    except tk.TclError:
        pass
    entry.focus_set()
    entry.icursor("end")

    def dark_title_bar():
        try:
            import ctypes
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
            root.withdraw(); root.deiconify()  # repaint the frame with the new colour
        except Exception:  # noqa: BLE001
            pass
    if os.name == "nt":
        dark_title_bar()
    pump()
    root.mainloop()


def cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Build a Flipper Zero app from a GitHub link.")
    ap.add_argument("url")
    ap.add_argument("--fw", choices=list(FIRMWARES), default="official")
    ap.add_argument("--send", action="store_true", help="send to the Flipper over USB after building")
    a = ap.parse_args(argv)
    try:
        results, _ = build(a.url, a.fw, a.send)
    except BuildError as e:
        print("!! " + str(e))
        return 1
    return 0 if results and all(r["ok"] for r in results) else 2


if __name__ == "__main__":
    # a windowed .exe has no stdout/stderr; give logging and print somewhere to write
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    gui()
