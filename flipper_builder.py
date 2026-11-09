"""Flipper Builder: paste a GitHub link to a Flipper Zero app, get a .fap.

GUI:  pythonw flipper_builder.py            (window: gui.py, UI: ui/)
CLI:  python flipper_builder.py <github-url> [--fw official|official-dev|momentum|momentum-dev|unleashed] [--send]
"""

import base64
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "Flipper Builder"
APP_VERSION = "1.1.0"
APP_ID = "c3rb3ruszx.FlipperBuilder"
REPO_URL = "https://github.com/c3rb3ruszx/flipper-builder"
FROZEN = getattr(sys, "frozen", False)
# Frozen app: data lives next to the .exe. From source: next to this file.
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
ROOT = Path(os.environ.get("FLIPPER_BUILDER_HOME", ROOT))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ICON = BUNDLE / "assets" / "icon.ico"
UI_DIR = BUNDLE / "ui"
# .cache holds re-downloadable stuff (SDKs, temp clones); safe to delete
SDK_DIR = ROOT / ".cache" / "sdk"
WORK_DIR = ROOT / ".cache" / "work"
OUT_DIR = ROOT / "output"
SETTINGS = ROOT / "settings.json"
SDK_MAX_AGE = 24 * 3600  # refresh the SDK at most once a day
MAX_APPS = 25

MOMENTUM_INDEX = "--index-url=https://up.momentum-fw.dev/firmware/directory.json"
# id: (label, short label, ufbt update args)
FIRMWARES = {
    "official": ("Official firmware (release)", "OFW", ["--channel=release"]),
    "official-dev": ("Official firmware (dev)", "OFW DEV", ["--channel=dev"]),
    "momentum": ("Momentum", "MOMENTUM", [MOMENTUM_INDEX, "--channel=release"]),
    "momentum-dev": ("Momentum (dev)", "MNTM DEV", [MOMENTUM_INDEX, "--channel=dev"]),
    "unleashed": ("Unleashed", "UNLEASHED", ["--index-url=https://up.unleashedflip.com/directory.json", "--channel=release"]),
}

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class BuildError(Exception):
    pass


class Cancelled(BuildError):
    pass


class Job:
    """Hooks a build reports through. The CLI uses the defaults; the GUI overrides them."""

    def __init__(self, log=print, stage=None, progress=None, result=None):
        self.log = log
        self.stage = stage or (lambda name, detail="": None)
        self.progress = progress or (lambda value: None)
        self.result = result or (lambda res: None)
        self.cancelled = threading.Event()
        self.proc = None

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled("Cancelled.")

    def cancel(self):
        self.cancelled.set()
        p = self.proc
        if p and p.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                           creationflags=NO_WINDOW)


# ---------------------------------------------------------------- helpers

def _stream(job, popen_args, on_line=None, **kw):
    job.check()
    p = subprocess.Popen(popen_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW, **kw)
    job.proc = p
    lines = []
    try:
        for line in p.stdout:
            line = ANSI.sub("", line.rstrip())
            lines.append(line)
            job.log(line)
            if on_line:
                on_line(line)
        p.wait()
    finally:
        job.proc = None
    job.check()
    return p.returncode, lines


def run(job, cmd, cwd=None, env=None, check=True, quiet=False):
    """Run a command, streaming its output to the job log. Returns (code, lines)."""
    if not quiet:
        job.log("$ " + " ".join(str(c) for c in cmd))
    saved = job.log
    if quiet:
        job.log = lambda _: None
    try:
        code, lines = _stream(job, [str(c) for c in cmd], cwd=cwd, env=env)
    finally:
        job.log = saved
    if check and code != 0:
        raise BuildError(f"Command failed (exit {code}): {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}")
    return code, lines


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


def resolve_ref(job, remote, parts, env):
    """Split path_parts into (ref, subdir, is_sha). ref None = default branch."""
    if not parts:
        return None, "", False
    code, lines = run(job, ["git", "ls-remote", "--heads", "--tags", remote], env=env, check=False, quiet=True)
    if code != 0:
        raise BuildError("Couldn't reach that repo. Is the link right and the repo public?")
    refs = set()
    for line in lines:
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
        m = re.search(r"\b" + key + r"\s*=\s*[\"']([^\"']+)[\"']", text)
        return m.group(1) if m else None
    types = re.findall(r"apptype\s*=\s*FlipperAppType\.(\w+)", text)
    return {
        "appid": grab("appid") or fam_path.parent.name,
        "name": grab("name") or fam_path.parent.name,
        "category": grab("fap_category") or "Misc",
        "icon": grab("fap_icon"),
        "version": grab("fap_version"),
        "author": grab("fap_author"),
        "description": grab("fap_description"),
        "types": types,
        "dir": fam_path.parent,
    }


SKIP_DIRS = {".git", "dist", "build", ".ufbt", "node_modules", ".vscode"}


def find_apps(base):
    """Top-most folders holding an application.fam with an external (.fap) app."""
    apps, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "application.fam" in filenames:
            info = fam_info(Path(dirpath) / "application.fam")
            if any("EXTERNAL" in t for t in info["types"]) or not info["types"]:
                apps.append(info)
                dirnames[:] = []  # don't descend into an app's own subfolders
            else:
                skipped.append(info)
    return apps, skipped


def count_sources(app_dir):
    n = 0
    for dirpath, dirnames, filenames in os.walk(app_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        n += sum(f.endswith((".c", ".cpp", ".cc")) for f in filenames)
    return max(n, 1)


def icon_data_uri(app):
    if not app.get("icon"):
        return None
    p = app["dir"] / app["icon"]
    if not p.is_file() or p.stat().st_size > 64_000:
        return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def ufbt_env(fw):
    env = os.environ.copy()
    home = str(SDK_DIR / fw)
    env["UFBT_HOME"] = env["UFBT_STATE_DIR"] = home
    env["FBT_TOOLCHAIN_PATH"] = str(SDK_DIR / "_toolchain")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def sdk_version(fw):
    try:
        return json.loads((SDK_DIR / fw / "current" / "ufbt_state.json").read_text())["version"]
    except Exception:  # noqa: BLE001
        return None


class _LogForward(logging.Handler):
    def __init__(self, log):
        super().__init__()
        self.log = log
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.log(self.format(record))


def ensure_sdk(job, fw, force=False):
    """Download/refresh the SDK using ufbt's bootstrap module, in-process."""
    env = ufbt_env(fw)
    stamp = SDK_DIR / fw / ".last_update"
    fresh = stamp.exists() and time.time() - stamp.stat().st_mtime < SDK_MAX_AGE
    if fresh and not force and (SDK_DIR / fw / "current").exists():
        job.log(f"SDK {sdk_version(fw) or ''} is up to date (checked in the last 24h).")
        return env
    job.log(f"== Getting the latest {FIRMWARES[fw][0]} SDK")
    from ufbt.bootstrap import bootstrap_cli
    os.environ["FBT_TOOLCHAIN_PATH"] = env["FBT_TOOLCHAIN_PATH"]
    handler = _LogForward(job.log)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    try:
        code = bootstrap_cli(["--ufbt-home", env["UFBT_HOME"], "update", *FIRMWARES[fw][2]])
    except SystemExit as e:
        code = e.code
    finally:
        root_logger.removeHandler(handler)
    job.check()
    if code not in (0, None) or not (SDK_DIR / fw / "current").exists():
        raise BuildError("Couldn't download the SDK. Check your internet connection.")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    return env


def run_ufbt(job, app_dir, env, *targets, on_line=None):
    """What the `ufbt` command does: enter Flipper's toolchain env and run SCons.
    The toolchain ships its own Python, so this works from a frozen .exe too."""
    home = env["UFBT_STATE_DIR"]
    fbtenv = Path(home) / "current" / "scripts" / "toolchain" / "fbtenv.cmd"
    scripts = Path(home) / "current" / "scripts" / "ufbt"
    if not fbtenv.exists() or not scripts.exists():
        raise BuildError("SDK is incomplete. Press 'Update SDK' and try again.")
    args = " ".join(f'"{a}"' for a in ("-m", "SCons", "-Q", "--warn=target-not-built",
                                       "-C", scripts, f"UFBT_APP_DIR={app_dir}", *targets))
    cmdline = f'cmd /d /s /c "call "{fbtenv}" env & python {args}"'
    job.log("$ ufbt " + " ".join(targets))
    code, _ = _stream(job, cmdline, on_line=on_line, cwd=app_dir, env=env)
    return code


def have_git():
    return shutil.which("git") is not None


def download_zip(job, owner, repo, ref, dest):
    """Fallback when git isn't installed: GitHub source zip (no submodules)."""
    url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref or 'HEAD'}"
    job.log(f"$ download {url}")
    tmp = dest.parent / (dest.name + ".zip")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:  # noqa: BLE001
        raise BuildError(f"Couldn't download the repo ({e}). Is the link right and the repo public?")
    unzip = dest.parent / (dest.name + ".unzip")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(unzip)
    tmp.unlink()
    next(unzip.iterdir()).rename(dest)
    rmtree(unzip)


def send_to_flipper(job, app_dir, fw):
    env = ufbt_env(fw)
    job.log("== Sending to the Flipper over USB")
    code = run_ufbt(job, app_dir, env, "launch")
    if code != 0:
        raise BuildError("Couldn't send. Is the Flipper plugged in, unlocked, and qFlipper closed?")


# ---------------------------------------------------------------- main job

def build(url, fw="official", send=False, job=None):
    """Clone, build every app found, copy .faps to output/. Returns (results, out_dir)."""
    job = job or Job()
    log = job.log
    owner, repo, parts = parse_github_url(url)
    remote = f"https://github.com/{owner}/{repo}.git"
    env = ufbt_env(fw)

    job.stage("fetch", f"{owner}/{repo}")
    log(f"== Repo: {owner}/{repo}   Firmware: {FIRMWARES[fw][0]}")
    use_git = have_git()
    if use_git:
        ref, subdir, is_sha = resolve_ref(job, remote, parts, env)
    else:  # no git: assume a branch name without slashes
        ref, subdir, is_sha = (parts[0] if parts else None), "/".join(parts[1:]), False

    src = WORK_DIR / f"{owner}__{repo}"
    rmtree(src)
    src.parent.mkdir(parents=True, exist_ok=True)
    log("== Downloading source")
    git = ["git", "-c", "core.longpaths=true", "-c", "advice.detachedHead=false"]
    if not use_git:
        log("(git isn't installed, downloading a zip instead; submodules won't be included)")
        download_zip(job, owner, repo, ref, src)
    elif is_sha:
        run(job, git + ["clone", "--recurse-submodules", remote, src], env=env)
        run(job, git + ["checkout", ref], cwd=src, env=env)
        run(job, git + ["submodule", "update", "--init", "--recursive"], cwd=src, env=env)
    else:
        cmd = git + ["clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules"]
        if ref:
            cmd += ["--branch", ref]
        run(job, cmd + [remote, src], env=env)

    job.stage("scan")
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
    if len(apps) > MAX_APPS:
        raise BuildError(
            f"That link contains {len(apps)} apps (probably a whole firmware or app collection). "
            "Open the folder of the app you want on GitHub and paste that link instead.")
    log(f"== Found {len(apps)} app(s): " + ", ".join(a["name"] for a in apps))

    job.stage("sdk", FIRMWARES[fw][1])
    env = ensure_sdk(job, fw)
    out = OUT_DIR / f"{repo}__{fw}"
    rmtree(out)
    out.mkdir(parents=True)

    job.stage("build", f"{len(apps)} app(s)")
    total_src = sum(count_sources(a["dir"]) for a in apps)
    compiled = [0]

    def on_line(line):
        if re.match(r"^\s*(CC|CXX)\s", line):
            compiled[0] += 1
            job.progress(min(compiled[0] / total_src, 0.98))

    results = []
    for app in apps:
        job.check()
        rel = app["dir"].relative_to(src).as_posix() if app["dir"] != src else "/"
        log(f"\n== Building {app['name']} ({rel})")
        rmtree(app["dir"] / "dist")
        code = run_ufbt(job, app["dir"], env, on_line=on_line)
        faps = sorted((app["dir"] / "dist").glob("*.fap"))
        res = {
            "name": app["name"], "appid": app["appid"], "category": app["category"],
            "version": app["version"], "author": app["author"], "description": app["description"],
            "icon": icon_data_uri(app), "dir": str(app["dir"]), "fw": fw, "ok": False, "files": [],
        }
        if code != 0 or not faps:
            log(f"!! {app['name']} failed to build.")
            results.append(res)
            job.result(res)
            continue
        for f in faps:
            dest = out / f.name
            shutil.copy2(f, dest)
            res["files"].append({"name": f.name, "path": str(dest), "size": dest.stat().st_size})
        res["ok"] = True
        log(f"OK {app['name']}: " + ", ".join(f["name"] for f in res["files"])
            + f"   (on the SD card it goes in apps/{app['category']}/)")
        if send:
            job.stage("send", app["name"])
            try:
                send_to_flipper(job, app["dir"], fw)
                res["sent"] = True
            except Cancelled:
                raise
            except BuildError as e:
                log("!! " + str(e))
                res["sent"] = False
        results.append(res)
        job.result(res)

    job.progress(1.0)
    ok = sum(r["ok"] for r in results)
    log(f"\n== Done: {ok}/{len(results)} built. Files are in {out}")
    return results, out


# ---------------------------------------------------------------- settings

def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_settings(**kw):
    s = load_settings()
    s.update(kw)
    try:
        SETTINGS.write_text(json.dumps(s, indent=2))
    except Exception:  # noqa: BLE001
        pass


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
    import gui
    gui.main()
