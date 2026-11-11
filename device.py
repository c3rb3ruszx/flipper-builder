"""Talking to a Flipper Zero over USB: find it, read its firmware info, copy files to its SD card.

The storage protocol comes straight from Flipper's own SDK (scripts/flipper/storage.py, the code
fbt and ufbt use), loaded at runtime from whichever SDK is downloaded, so it stays in step with
the firmware. Only one program can hold the Flipper's serial port: close qFlipper first.
"""

import contextlib
import importlib
import io
import json
import os
import re
import zipfile
import sys
import threading
import time
from pathlib import Path

import serial
import serial.tools.list_ports as list_ports

FLIPPER_VID, FLIPPER_PID = 0x0483, 0x5740
_port_lock = threading.RLock()  # one conversation with the Flipper at a time
_mirror = None                  # the running screen mirror, if any (it holds the port while it runs)
_streams = []                   # other long-running port users that step aside for CLI jobs (log follower)


class DeviceError(Exception):
    pass


@contextlib.contextmanager
def _exclusive():
    """Take the port for a CLI conversation. A running mirror is paused first and resumed after.

    The mirror can be (re)starting when we get here: its thread already waits for or holds the
    port but isn't streaming yet. So keep pausing whatever mirror is active until the port is ours,
    instead of checking once (that left jobs waiting forever right after another job ended)."""
    me = threading.current_thread()
    paused = [x for x in list(_streams) if x.running and x._thread is not me]
    for x in paused:
        x.pause()
    m = None
    try:
        while True:
            cur = _mirror
            if cur is not None and cur._thread is not me and cur.active():
                cur.pause()
                m = cur
            if _port_lock.acquire(timeout=0.25):
                break
        try:
            yield
        finally:
            _port_lock.release()
    finally:
        for x in paused:
            if x in _streams:
                x.resume()
        if m is not None and _mirror is m:
            m.resume()


def find_ports():
    """Serial ports that belong to a Flipper (same rule as the SDK: serial number 'flip_<name>')."""
    out = []
    for p in list_ports.comports():
        sn = p.serial_number or ""
        if sn.startswith("flip_") or (p.vid == FLIPPER_VID and p.pid == FLIPPER_PID):
            out.append({"port": p.device, "name": sn[5:] if sn.startswith("flip_") else ""})
    return out


def _storage_module(sdk_dirs):
    """flipper.storage from the first downloaded SDK, else the vendored copy (flipper_storage.py)."""
    for d in sdk_dirs:
        scripts = Path(d) / "current" / "scripts"
        if (scripts / "flipper" / "storage.py").exists():
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            try:
                return importlib.import_module("flipper.storage")
            except Exception:  # noqa: BLE001
                break
    return importlib.import_module("flipper_storage")


def parse_info(text):
    info = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([\w.]+)\s*:\s*(.*?)\s*$", line)
        if m:
            info[m.group(1)] = m.group(2)
    return info


class _Watchdog:
    """The SDK's reader waits forever for the prompt; close the port after `seconds` so it raises."""

    def __init__(self, st, seconds):
        self.fired = False
        self.timer = threading.Timer(seconds, self._fire, args=(st,))
        self.timer.daemon = True

    def _fire(self, st):
        self.fired = True
        try:
            st.port.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        self.timer.start()
        return self

    def __exit__(self, *exc):
        self.timer.cancel()
        if self.fired:
            raise DeviceError("The Flipper stopped answering. Unlock it, leave any open app, and try again.")
        return False


def _open_guarded(port, sdk_dirs, seconds=8):
    """_open with a watchdog around the SDK's start() handshake."""
    mod = _storage_module(sdk_dirs)
    st = mod.FlipperStorage(port)
    wd = _Watchdog(st, seconds)
    try:
        with wd:
            try:
                st.start()
            except (serial.SerialException, OSError, TypeError) as e:
                if wd.fired:
                    raise
                raise DeviceError("Couldn't open the Flipper's USB port. Close qFlipper (or anything else "
                                  f"using it) and make sure the Flipper is unlocked. ({e})")
    except DeviceError:
        try:
            st.stop()
        except Exception:  # noqa: BLE001
            pass
        raise
    return mod, st


def read_info(port, sdk_dirs):
    """Firmware details as reported by the Flipper's `device_info` command."""
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            with _Watchdog(st, 6):
                try:
                    st.send("device_info\r")
                    raw = st.read.until(st.CLI_PROMPT).decode("utf-8", "replace")
                except (serial.SerialException, OSError, TypeError):
                    raw = ""
        finally:
            try:
                st.stop()
            except Exception:  # noqa: BLE001
                pass
    info = parse_info(raw)
    if not info:
        raise DeviceError("The Flipper didn't answer device_info.")
    return info


def classify(info):
    """Map device info to (firmware id, nice name). Firmware id matches flipper_workbench.FIRMWARES."""
    fork = (info.get("firmware_origin_fork") or "").lower()
    git = (info.get("firmware_origin_git") or "").lower()
    version = (info.get("firmware_version") or "").lower()
    branch = (info.get("firmware_branch") or "").lower()
    if "momentum" in fork or version.startswith("mntm") or "momentum" in git or "next-flip" in git:
        base, nice = "momentum", "Momentum"
    elif "unleashed" in fork or version.startswith("unlshd") or "darkflippers" in git:
        base, nice = "unleashed", "Unleashed"
    elif fork in ("official", "") and ("flipperdevices" in git or not git):
        base, nice = "official", "Official"
    else:
        return None, info.get("firmware_origin_fork") or "Unknown firmware"
    dev = branch == "dev" or "dev" in version
    return (base + "-dev" if dev else base), nice + (" dev" if dev else "")


def summary(port, name, info):
    fw, nice = classify(info)
    try:
        api = f"{int(info.get('firmware_api_major'))}.{int(info.get('firmware_api_minor'))}"
    except (TypeError, ValueError):
        api = None
    return {
        "port": port, "name": info.get("hardware_name") or name or "Flipper",
        "fw": fw, "fw_name": nice, "version": info.get("firmware_version"),
        "commit": info.get("firmware_commit"), "branch": info.get("firmware_branch"),
        "api": api, "target": info.get("firmware_target") or info.get("hardware_target"),
    }


def install_files(port, sdk_dirs, files, log=print):
    """Copy local files to the SD card. files: list of (local_path, flipper_path)."""
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            ops = mod.FlipperStorageOperations(st)
            for local, remote in files:
                log(f"$ copy {Path(local).name} -> {remote}")
                # generous: ~4 KB/s worst case plus handshake time
                with _Watchdog(st, 20 + Path(local).stat().st_size / 4096):
                    try:
                        ops.mkpath(remote.rsplit("/", 1)[0])
                        ops.send_file_to_storage(remote, str(local), force=True)
                    except (serial.SerialException, OSError, TypeError) as e:
                        raise DeviceError(f"USB transfer failed: {e}")
                log(f"OK on the SD card: {remote}")
        except mod.FlipperStorageException as e:
            raise DeviceError(str(e))
        finally:
            try:
                st.stop()
            except Exception:  # noqa: BLE001
                pass


def storage_call(port, sdk_dirs, fn, seconds=15):
    """Run fn(storage_module, FlipperStorage) with the port open and a watchdog. Returns fn's result."""
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            with _Watchdog(st, seconds):
                return fn(mod, st)
        finally:
            try:
                st.stop()
            except Exception:  # noqa: BLE001
                pass


def file_exists(port, sdk_dirs, path):
    return storage_call(port, sdk_dirs, lambda mod, st: st.exist_file(path))


def file_hash(port, sdk_dirs, path):
    return storage_call(port, sdk_dirs, lambda mod, st: st.hash_flipper(path))


def remove(port, sdk_dirs, path):
    """Delete a file (or empty folder) on the Flipper. Missing paths are fine."""
    def fn(mod, st):
        if st.exist_file(path) or st.exist_dir(path):
            st.remove(path)
            return True
        return False
    return storage_call(port, sdk_dirs, fn)


def _list(st, path):
    """[(kind 'D'|'F', name, size)] for one folder, parsed like the SDK's list_tree."""
    st.send_and_wait_eol(f'storage list "{path}"\r')
    data = st.read.until(st.CLI_PROMPT)
    out = []
    for raw in data.split(b"\r\n"):
        line = raw.decode("utf-8", "replace").strip()
        if not line or line == "Empty" or "Storage error" in line:
            continue
        kind, _, info = line.partition(" ")
        if kind == "[D]":
            out.append(("D", info, 0))
        elif kind == "[F]":
            name, _, size = info.rpartition(" ")
            out.append(("F", name, int(size.rstrip("b")) if size.rstrip("b").isdigit() else 0))
    return out


def list_apps(port, sdk_dirs):
    """Installed .fap apps under /ext/apps/<Category>/ plus SD card space."""
    def fn(mod, st):
        apps = []
        for kind, cat, _ in _list(st, "/ext/apps"):
            if kind != "D":
                continue
            for k2, name, size in _list(st, f"/ext/apps/{cat}"):
                if k2 == "F" and name.lower().endswith(".fap"):
                    apps.append({"path": f"/ext/apps/{cat}/{name}", "category": cat, "file": name, "size": size})
        st.send_and_wait_eol('storage info "/ext"\r')
        info = st.read.until(st.CLI_PROMPT).decode("utf-8", "replace")
        total = re.search(r"(\d+)\s*KiB total", info)
        free = re.search(r"(\d+)\s*KiB free", info)
        sd = {"total_kib": int(total.group(1)) if total else None, "free_kib": int(free.group(1)) if free else None}
        return {"apps": apps, "sd": sd}
    return storage_call(port, sdk_dirs, fn, seconds=30)


def read_file(port, sdk_dirs, path, size_hint=0):
    """Copy a file off the Flipper (used to read a .fap's manifest)."""
    return storage_call(port, sdk_dirs, lambda mod, st: bytes(st.read_file(path)),
                        seconds=15 + size_hint / 8192)


def write_file(port, sdk_dirs, path, data):
    """Save bytes to a file on the SD card: written to <path>.fwtmp first, then swapped in, so an
    interrupted save never leaves a half-written file in place."""
    import tempfile
    path = _norm(path)
    if not path.startswith("/ext/"):
        raise DeviceError("Only files on the SD card can be saved.")
    tmp_remote = path + ".fwtmp"
    fd, tmp_local = tempfile.mkstemp(prefix="fw_", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

        def fn(mod, st):
            mod.FlipperStorageOperations(st).mkpath(path.rsplit("/", 1)[0])   # the folder may be new
            st.send_file(tmp_local, tmp_remote)
            if st.size(tmp_remote) != len(data):
                st.remove(tmp_remote)
                raise DeviceError("The file didn't arrive complete; nothing was changed.")
            if st.exist_file(path):
                st.remove(path)
            st.send_and_wait_eol(f'storage rename "{tmp_remote}" "{path}"\r')
            answer = st.read.until(st.CLI_PROMPT)
            if st.has_error(answer):
                raise DeviceError(f"Couldn't save {path}: {answer.decode('utf-8', 'replace').strip()}")
        storage_call(port, sdk_dirs, fn, seconds=20 + len(data) / 4096)
    finally:
        try:
            os.remove(tmp_local)
        except OSError:
            pass
    return len(data)


def read_many(port, sdk_dirs, items, on_file, should_stop=lambda: False):
    """Read several files in ONE session (opening the port costs ~0.5 s per session).
    items: [{path, size}]; on_file(item, bytes) for each. Stops early if should_stop()."""
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            for it in items:
                if should_stop():
                    break
                with _Watchdog(st, 10 + it.get("size", 0) / 8192):
                    try:
                        data = bytes(st.read_file(it["path"]))
                    except mod.FlipperStorageException:
                        data = b""
                    except (serial.SerialException, OSError, TypeError) as e:
                        raise DeviceError(f"USB read failed: {e}")
                on_file(it, data)
        finally:
            try:
                st.stop()
            except Exception:  # noqa: BLE001
                pass


def running_app(port, sdk_dirs):
    """Name of the app open on the Flipper (from `loader info`, which changes nothing), or None."""
    def fn(mod, st):
        st.send_and_wait_eol("loader info\r")
        return st.read.until(st.CLI_PROMPT).decode("utf-8", "replace").strip()
    text = storage_call(port, sdk_dirs, fn)
    if not text or "No application" in text:
        return None
    m = re.search(r'"([^"]+)"', text)
    return m.group(1) if m else text.splitlines()[0][:40]


def open_app(port, sdk_dirs, path, args=None):
    """Open an app on the Flipper, the way the SDK's runfap.py does: close the current app, then open.
    `path` is a .fap path or a built-in app name (e.g. "NFC"); `args` is passed to it (e.g. a file)."""
    def fn(mod, st):
        for _ in range(10):
            st.send_and_wait_eol("loader close\r")
            result = st.read.until(st.CLI_EOL)
            if b"was closed" in result:
                st.read.until(st.CLI_EOL)
                time.sleep(0.5)
            elif result.startswith(b"No application"):
                st.read.until(st.CLI_EOL)
                break
            else:
                raise DeviceError("An app on your Flipper can't be closed from here (the NFC app is one). "
                                  "Press Back on the Flipper until you're on the home screen, then try again.")
        # args go unquoted: the loader passes the rest of the line through as-is, quotes included,
        # and the NFC app then can't find "/ext/nfc/x.nfc" (tested on Momentum dev, 10 Nov)
        st.send_and_wait_eol(f'loader open "{path}"' + (f" {args}" if args else "") + "\r")
        result = st.read.until(st.CLI_EOL)
        if result.strip():
            raise DeviceError("The Flipper couldn't open it: " + result.decode("utf-8", "replace").strip())
        return True
    return storage_call(port, sdk_dirs, fn, seconds=20)


def api_compatible(app_api, device_api):
    """An app runs if the API major matches and the Flipper's minor is at least the app's."""
    try:
        am, an = (int(x) for x in app_api.split("."))
        dm, dn = (int(x) for x in device_api.split("."))
    except (AttributeError, ValueError):
        return None
    return am == dm and dn >= an


# ====================================================================== file manager

def _norm(path):
    path = "/" + path.strip().strip("/")
    if not (path == "/ext" or path.startswith("/ext/")):
        raise DeviceError("Only the SD card (/ext) can be browsed here.")
    if ".." in path.split("/"):
        raise DeviceError("Invalid path.")
    return path


def list_dir(port, sdk_dirs, path):
    """[{name, dir, size}] for one folder on the SD card, folders first."""
    path = _norm(path)
    entries = storage_call(port, sdk_dirs, lambda mod, st: _list(st, path), seconds=20)
    out = [{"name": n, "dir": k == "D", "size": s} for k, n, s in entries]
    out.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return out


def _walk(st, path):
    """Every file under path: [(remote_path, size)], depth first."""
    files = []
    for kind, name, size in _list(st, path):
        p = f"{path}/{name}"
        if kind == "D":
            files += _walk(st, p)
        else:
            files.append((p, size))
    return files


# ====================================================================== firmware update

def device_power(port, sdk_dirs):
    """Battery facts from `info power` (charge.level, charge.state, battery.voltage...)."""
    out = []
    cli_run(port, sdk_dirs, "info power", out.append, limit=10)
    return parse_info("".join(out))


def sd_free_kib(port, sdk_dirs):
    def fn(mod, st):
        st.send_and_wait_eol('storage info /ext\r')
        text = st.read.until(st.CLI_PROMPT).decode("utf-8", "replace")
        m = re.search(r"(\d+)KiB free", text)
        return int(m.group(1)) if m else None
    return storage_call(port, sdk_dirs, fn, seconds=15)


def upload_update(port, sdk_dirs, pkg_dir, progress=lambda done, total, name: None, should_stop=lambda: False):
    """Copy an unpacked update package to /ext/update/<name>/ and check every file's MD5 on the Flipper.
    Returns the remote folder. Nothing is installed here."""
    pkg_dir = Path(pkg_dir)
    name = pkg_dir.name
    if not re.fullmatch(r"[\w.\-]+", name):
        raise DeviceError("Unexpected update package name.")
    remote_dir = f"/ext/update/{name}"
    files = sorted(f for f in pkg_dir.rglob("*") if f.is_file())
    total = sum(f.stat().st_size for f in files) or 1
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            ops = mod.FlipperStorageOperations(st)
            done = 0
            for f in files:
                if should_stop():
                    raise DeviceError("Cancelled.")
                remote = f"{remote_dir}/{f.relative_to(pkg_dir).as_posix()}"
                with _Watchdog(st, 30 + f.stat().st_size / 4096):
                    ops.mkpath(remote.rsplit("/", 1)[0])
                    st.send_file(str(f), remote)
                    if st.hash_flipper(remote).strip().lower() != st.hash_local(str(f)).strip().lower():
                        raise DeviceError(f"{f.name} didn't arrive intact on the SD card. Nothing was installed.")
                done += f.stat().st_size
                progress(done, total, remote)
        except mod.FlipperStorageException as e:
            raise DeviceError(str(e))
        finally:
            _safe_stop(st)
    return remote_dir


def start_update(port, sdk_dirs, remote_dir):
    """Close any open app, then `update install` (the Flipper verifies the package and restarts into
    its updater). Same steps as the SDK's scripts/selfupdate.py."""
    if not re.fullmatch(r"/ext/update/[\w.\-]+", remote_dir):
        raise DeviceError("Unexpected update folder.")
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            with _Watchdog(st, 40):
                for _ in range(10):
                    st.send_and_wait_eol("loader close\r")
                    result = st.read.until(st.CLI_EOL)
                    if b"was closed" in result:
                        st.read.until(st.CLI_EOL)
                        time.sleep(0.3)
                    elif result.startswith(b"No application"):
                        st.read.until(st.CLI_EOL)
                        break
                    else:
                        raise DeviceError(f"Couldn't close the open app: {result.decode('utf-8', 'replace').strip()}")
                st.send_and_wait_eol(f"update install {remote_dir}/update.fuf\r")
                result = st.read.until(st.CLI_EOL)
                if b"Verifying" not in result:
                    raise DeviceError(f"The Flipper didn't accept the update: {result.decode('utf-8', 'replace').strip()}")
                result = st.read.until(st.CLI_EOL)
                if not result.startswith(b"OK"):
                    raise DeviceError(f"The Flipper rejected the package: {result.decode('utf-8', 'replace').strip()}. Nothing was changed.")
        finally:
            _safe_stop(st)
    return True


def sd_tree(port, sdk_dirs, should_stop=lambda: False):
    """The whole SD card as a size tree: {"name", "path", "size", "files", "children": [...]} (folders only;
    a folder's own loose files are summed into it), plus totals by file type."""
    by_type = {}

    def walk(st, path, name):
        node = {"name": name, "path": path, "size": 0, "files": 0, "children": []}
        for kind, n, size in _list(st, path):
            if should_stop():
                raise DeviceError("Cancelled.")
            if kind == "D":
                if path == "/ext" and n == "System Volume Information":
                    continue
                child = walk(st, f"{path}/{n}", n)
                node["children"].append(child)
                node["size"] += child["size"]
                node["files"] += child["files"]
            else:
                node["size"] += size
                node["files"] += 1
                ext = n.rsplit(".", 1)[-1].lower() if "." in n else "(none)"
                t = by_type.setdefault(ext, [0, 0])
                t[0] += size
                t[1] += 1
        node["children"].sort(key=lambda c: -c["size"])
        return node
    tree = storage_call(port, sdk_dirs, lambda mod, st: walk(st, "/ext", "SD card"), seconds=300)
    types = sorted(([k, v[0], v[1]] for k, v in by_type.items()), key=lambda x: -x[1])
    return {"tree": tree, "types": types}


class LogFollower:
    """Streams `log <level>` for as long as wanted, but steps aside (Ctrl+C, port released) whenever
    another USB job needs the Flipper, then picks up again. on_text gets the text, on_end the reason."""

    LEVELS = ("error", "warn", "info", "debug", "trace")

    def __init__(self, port, sdk_dirs, level, on_text, on_end=lambda reason: None):
        self.port, self.sdk_dirs = port, sdk_dirs
        self.level = level if level in self.LEVELS else "debug"
        self.on_text, self.on_end = on_text, on_end
        self.running = False
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._idle = threading.Event()
        self._thread = None

    def start(self):
        self.running = True
        _streams.append(self)
        self._thread = threading.Thread(target=self._run, daemon=True, name="log-follow")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def pause(self):
        self._paused.set()
        self._idle.wait(10)

    def resume(self):
        self._idle.clear()
        self._paused.clear()

    def _session(self):
        with _port_lock:
            self._idle.clear()
            mod, st = _open_guarded(self.port, self.sdk_dirs)
            ser = st.port
            try:
                ser.timeout = 0.15
                st.read.buffer = bytearray()
                ser.reset_input_buffer()
                ser.write(f"log {self.level}\r".encode())
                buf, echo = b"", False
                while not (self._stop.is_set() or self._paused.is_set()):
                    chunk = ser.read(4096)
                    if not chunk:
                        continue
                    buf += chunk
                    if not echo:
                        nl = buf.find(b"\n")
                        if nl < 0:
                            continue
                        buf, echo = buf[nl + 1:], True
                    cut = buf.rfind(b"\n") + 1           # pass on whole lines only
                    if cut:
                        text, buf = buf[:cut], buf[cut:]
                        self.on_text(_ANSI.sub("", text.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", ""))
                ser.write(b"\x03")                       # end the stream, wait for the prompt
                t0 = time.time()
                tail = b""
                while time.time() - t0 < 3 and not tail.rstrip().endswith(b">:"):
                    tail += ser.read(256)
            finally:
                _safe_stop(st)
                self._idle.set()

    def _run(self):
        reason = "stopped"
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._idle.set()
                    time.sleep(0.1)
                    continue
                self._session()
        except Exception as e:  # noqa: BLE001
            reason = f"error: {e}"
        finally:
            self.running = False
            self._idle.set()
            if self in _streams:
                _streams.remove(self)
            self.on_end(reason)


TEMP_SUFFIXES = (".fwtmp", ".tmp", ".temp", ".part", ".bak~")


def cleanup_scan(port, sdk_dirs):
    """Things that are safe to delete: leftover temp files (in /ext and /ext/apps) and firmware update
    packages already unpacked in /ext/update. Apps (duplicates, wrong API) are judged by the UI."""
    def fn(mod, st):
        temp, updates = [], []
        for kind, name, size in _list(st, "/ext"):
            if kind == "F" and name.lower().endswith(TEMP_SUFFIXES):
                temp.append({"path": f"/ext/{name}", "size": size})
        for p, size in _walk(st, "/ext/apps"):
            if p.lower().endswith(TEMP_SUFFIXES):
                temp.append({"path": p, "size": size})
        for kind, name, size in _list(st, "/ext/update"):
            if kind == "D":
                files = _walk(st, f"/ext/update/{name}")
                updates.append({"path": f"/ext/update/{name}", "size": sum(s for _, s in files), "files": len(files)})
            else:
                updates.append({"path": f"/ext/update/{name}", "size": size, "files": 1})
        return {"temp": temp, "updates": updates}
    return storage_call(port, sdk_dirs, fn, seconds=90)


def _check(st, response, path):
    if b"Storage error" in response:
        raise DeviceError(f"{path}: " + response.decode("utf-8", "replace").split(":", 1)[-1].strip())


def ensure_dir(port, sdk_dirs, path):
    """Create a folder (and its parents) if it isn't there yet."""
    path = _norm(path)

    def fn(mod, st):
        mod.FlipperStorageOperations(st).mkpath(path)
    storage_call(port, sdk_dirs, fn)


def mkdir(port, sdk_dirs, path):
    path = _norm(path)

    def fn(mod, st):
        st.send_and_wait_eol(f'storage mkdir "{path}"\r')
        r = st.read.until(st.CLI_EOL)
        st.read.until(st.CLI_PROMPT)
        _check(st, r, path)
    storage_call(port, sdk_dirs, fn)


def rename(port, sdk_dirs, old, new):
    old, new = _norm(old), _norm(new)

    def fn(mod, st):
        st.send_and_wait_eol(f'storage rename "{old}" "{new}"\r')
        r = st.read.until(st.CLI_EOL)
        st.read.until(st.CLI_PROMPT)
        _check(st, r, old)
    storage_call(port, sdk_dirs, fn)


def remove_tree(port, sdk_dirs, path, progress=lambda done, total: None):
    """Delete a file, or a folder and everything in it."""
    path = _norm(path)
    if path in ("/ext", "/ext/apps", "/ext/apps_data", "/ext/update"):
        raise DeviceError(f"{path} is a system folder; delete the things inside it instead.")

    def fn(mod, st):
        if st.exist_file(path):
            st.remove(path)
            return
        files = _walk(st, path)
        dirs = set()
        for i, (f, _) in enumerate(files, 1):
            st.remove(f)
            parent = f.rsplit("/", 1)[0]
            while parent != path and parent.startswith(path):
                dirs.add(parent)
                parent = parent.rsplit("/", 1)[0]
            progress(i, len(files))
        for d in sorted(dirs, key=lambda d: -d.count("/")):   # deepest folders first
            st.remove(d)
        st.remove(path)
    storage_call(port, sdk_dirs, fn, seconds=60 + 600)


def download(port, sdk_dirs, remote, local_dir, progress=lambda done, total, name: None):
    """Copy a file or a whole folder from the SD card into local_dir. Returns the local path."""
    remote = _norm(remote)
    local_dir = Path(local_dir)

    def fn(mod, st):
        if st.exist_file(remote):
            files, base = [(remote, 0)], remote.rsplit("/", 1)[0]
        else:
            files, base = _walk(st, remote), remote.rsplit("/", 1)[0]
        total = sum(s for _, s in files) or 1
        done = 0
        for f, size in files:
            dest = local_dir / f[len(base) + 1:]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with _Watchdog(st, 10 + size / 8192):
                dest.write_bytes(bytes(st.read_file(f)))
            done += size
            progress(done, total, f)
        return local_dir / remote.rsplit("/", 1)[1]
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            return fn(mod, st)
        finally:
            _safe_stop(st)


def upload(port, sdk_dirs, local_paths, remote_dir, progress=lambda done, total, name: None):
    """Copy local files (or folders) into remote_dir on the SD card."""
    remote_dir = _norm(remote_dir)
    pairs = []
    for lp in map(Path, local_paths):
        if lp.is_dir():
            for f in sorted(lp.rglob("*")):
                if f.is_file():
                    pairs.append((f, f"{remote_dir}/{lp.name}/{f.relative_to(lp).as_posix()}"))
        elif lp.is_file():
            pairs.append((lp, f"{remote_dir}/{lp.name}"))
    total = sum(p.stat().st_size for p, _ in pairs) or 1
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            ops = mod.FlipperStorageOperations(st)
            done = 0
            for local, remote in pairs:
                with _Watchdog(st, 20 + local.stat().st_size / 4096):
                    ops.mkpath(remote.rsplit("/", 1)[0])
                    ops.send_file_to_storage(remote, str(local), force=True)
                done += local.stat().st_size
                progress(done, total, remote)
        except mod.FlipperStorageException as e:
            raise DeviceError(str(e))
        finally:
            _safe_stop(st)
    return len(pairs)


def _safe_stop(st):
    try:
        st.stop()
    except Exception:  # noqa: BLE001
        pass


# ====================================================================== CLI terminal

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x07")


def cli_run(port, sdk_dirs, command, on_output, should_stop=lambda: False, limit=300):
    """Run one command in the Flipper's own CLI and stream what it prints to on_output(text).

    Ends when the prompt comes back. should_stop() (the Stop button) or `limit` seconds send Ctrl+C,
    which ends streaming commands like `log` or `subghz rx`. Commands that wait for more typed input
    can only be stopped. Returns how it ended: "done", "stopped" or "timeout".
    """
    command = command.replace("\r", " ").replace("\n", " ").strip()
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        ser = st.port
        prompt = st.CLI_PROMPT.encode() if isinstance(st.CLI_PROMPT, str) else st.CLI_PROMPT
        try:
            ser.timeout = 0.15
            st.read.buffer = bytearray()
            ser.reset_input_buffer()
            ser.write(command.encode("utf-8") + b"\r")
            buf, echo_done, sent_break, second_break, ended = b"", False, None, False, "done"
            t0 = time.time()
            while True:
                try:
                    chunk = ser.read(4096)
                except (serial.SerialException, OSError, TypeError) as e:
                    raise DeviceError(f"The Flipper's USB connection dropped. ({e})")
                buf += chunk
                if not echo_done:                        # the CLI echoes the command line first
                    nl = buf.find(b"\n")
                    if nl < 0:
                        if time.time() - t0 > 5:
                            raise DeviceError("The Flipper didn't answer. Unlock it, leave any open app, and try again.")
                        continue
                    buf, echo_done = buf[nl + 1:], True
                done = buf.rstrip(b" ").endswith(prompt.rstrip(b" "))
                if done:
                    buf = buf[:buf.rstrip(b" ").rfind(prompt.rstrip(b" "))]
                # hold back a few bytes so half a prompt (or half a UTF-8 character) never reaches the screen
                keep = 0 if done else min(len(buf), len(prompt) + 3)
                out, buf = buf[:len(buf) - keep], buf[len(buf) - keep:]
                if out:
                    on_output(_ANSI.sub("", out.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", ""))
                if done:
                    return ended
                if sent_break is None and (should_stop() or time.time() - t0 > limit):
                    ended = "stopped" if should_stop() else "timeout"
                    ser.write(b"\x03")                   # Ctrl+C: ends streaming commands
                    sent_break = time.time()
                elif sent_break is not None and time.time() - sent_break > 4 and not second_break:
                    ser.write(b"\x03")                   # some commands need a second one
                    second_break = True
                elif sent_break is not None and time.time() - sent_break > 8:
                    raise DeviceError("The command didn't stop. Unplug the Flipper and plug it back in.")
        finally:
            _safe_stop(st)


# ====================================================================== backups

def folder_sizes(port, sdk_dirs):
    """Top-level SD folders with their total size and file count, for the backup picker."""
    def fn(mod, st):
        out = []
        for kind, name, size in _list(st, "/ext"):
            if kind == "D":
                files = _walk(st, f"/ext/{name}")
                out.append({"name": name, "size": sum(s for _, s in files), "files": len(files)})
            else:
                out.append({"name": name, "size": size, "files": 1, "file": True})
        return out
    return storage_call(port, sdk_dirs, fn, seconds=120)


def backup(port, sdk_dirs, names, zip_path, device_info=None, progress=lambda done, total, name: None,
           should_stop=lambda: False):
    """Copy the chosen top-level SD folders/files into a zip (paths relative to /ext)."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(".part")
    with _exclusive():
        mod, st = _open_guarded(port, sdk_dirs)
        try:
            files = []
            for n in names:
                p = f"/ext/{n}"
                files += [(p, 0)] if st.exist_file(p) else _walk(st, p)
            total = sum(s for _, s in files) or 1
            done = 0
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for f, size in files:
                    if should_stop():
                        raise DeviceError("Backup cancelled.")
                    with _Watchdog(st, 10 + size / 8192):
                        data = bytes(st.read_file(f))
                    z.writestr(f[len("/ext/"):], data)
                    done += size
                    progress(done, total, f)
                z.writestr(BACKUP_META, json.dumps({
                    "created": int(time.time()), "folders": names, "files": len(files), "bytes": done,
                    "device": device_info or {},
                    "index": [[f, size] for f, size in files]}, indent=1))
        finally:
            _safe_stop(st)
    os.replace(tmp, zip_path)
    return {"path": str(zip_path), "files": len(files), "bytes": done}


BACKUP_META = "_flipper_workbench_backup.json"
_META_NAMES = (BACKUP_META, "_flipper_builder_backup.json")   # backups made before the rename


def list_files(port, sdk_dirs, names):
    """[(path, size)] of everything a backup of these top-level names would contain (sorted)."""
    def fn(mod, st):
        files = []
        for n in names:
            p = f"/ext/{n}"
            files += [(p, st.size(p))] if st.exist_file(p) else _walk(st, p)
        return sorted(files)
    return storage_call(port, sdk_dirs, fn, seconds=120)


def backup_index(zip_path):
    """The (path, size) list a backup recorded, or None for backups made before 1.7.0."""
    with zipfile.ZipFile(zip_path) as z:
        for n in _META_NAMES:
            try:
                idx = json.loads(z.read(n)).get("index")
                return sorted((p, s) for p, s in idx) if idx is not None else None
            except KeyError:
                pass
    return None


def backup_info(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        meta = {}
        for n in _META_NAMES:
            try:
                meta = json.loads(z.read(n))
                break
            except KeyError:
                pass
        names = [i for i in z.infolist() if not i.is_dir() and i.filename not in _META_NAMES]
        meta["files"] = len(names)
        meta["bytes"] = sum(i.file_size for i in names)
    return meta


def restore(port, sdk_dirs, zip_path, progress=lambda done, total, name: None, should_stop=lambda: False):
    """Copy every file in a backup zip back to /ext, replacing files with the same path. Deletes nothing."""
    with zipfile.ZipFile(zip_path) as z:
        items = [i for i in z.infolist() if not i.is_dir() and i.filename not in _META_NAMES]
        for i in items:   # never let a crafted zip write outside /ext
            if i.filename.startswith(("/", "\\")) or ".." in i.filename.replace("\\", "/").split("/"):
                raise DeviceError(f"Unsafe path in backup: {i.filename}")
        total = sum(i.file_size for i in items) or 1
        with _exclusive():
            mod, st = _open_guarded(port, sdk_dirs)
            try:
                ops = mod.FlipperStorageOperations(st)
                done, made = 0, set()
                for i in items:
                    if should_stop():
                        raise DeviceError("Restore cancelled.")
                    remote = "/ext/" + i.filename.replace("\\", "/")
                    parent = remote.rsplit("/", 1)[0]
                    with _Watchdog(st, 20 + i.file_size / 4096):
                        if parent not in made:
                            ops.mkpath(parent)
                            made.add(parent)
                        data = z.read(i)
                        if st.exist_file(remote):
                            st.remove(remote)
                        # same chunked write as the SDK's send_file, from memory instead of a file
                        for off in range(0, len(data), st.chunk_size):
                            chunk = data[off:off + st.chunk_size]
                            st.send_and_wait_eol(f'storage write_chunk "{remote}" {len(chunk)}\r')
                            answer = st.read.until(st.CLI_EOL)
                            if st.has_error(answer):
                                st.read.until(st.CLI_PROMPT)
                                raise DeviceError(f"{remote}: " + answer.decode("utf-8", "replace"))
                            st.port.write(chunk)
                            st.read.until(st.CLI_PROMPT)
                        if not data:   # empty file: create it
                            st.send_and_wait_eol(f'storage write_chunk "{remote}" 0\r')
                            st.read.until(st.CLI_PROMPT)
                    done += i.file_size
                    progress(done, total, remote)
            finally:
                _safe_stop(st)
    return {"files": len(items), "bytes": total}


# ====================================================================== screen mirror + remote (RPC)
#
# The CLI command `start_rpc_session` switches the serial port to Flipper's protobuf RPC
# (github.com/flipperdevices/flipperzero-protobuf), the protocol qFlipper uses. Messages are
# PB_Main, each prefixed with its length as a varint. We only need four of them, so they are
# encoded by hand: start/stop screen stream (fields 20/21), screen frame (22), input event (23),
# and stop_session (19) to hand the port back to the CLI.

KEYS = {"up": 0, "down": 1, "right": 2, "left": 3, "ok": 4, "back": 5}
TYPES = {"press": 0, "release": 1, "short": 2, "long": 3, "repeat": 4}


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field_varint(num, value):
    return _varint(num << 3) + _varint(value)


def _field_bytes(num, data):
    return _varint((num << 3) | 2) + _varint(len(data)) + data


def _main(command_id, field, payload=b""):
    msg = _field_varint(1, command_id) + _field_bytes(field, payload)
    return _varint(len(msg)) + msg


def _read_varint(read):
    shift = result = 0
    while True:
        b = read(1)
        if not b:
            raise EOFError
        result |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return result
        shift += 7


def _parse(buf):
    """Top-level fields of a protobuf message: {field: [values]} (varints as int, length-delimited as bytes)."""
    out, i = {}, 0
    while i < len(buf):
        key, n = 0, 0
        while True:
            b = buf[i]; i += 1
            key |= (b & 0x7F) << n; n += 7
            if not b & 0x80:
                break
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, n = 0, 0
            while True:
                b = buf[i]; i += 1
                v |= (b & 0x7F) << n; n += 7
                if not b & 0x80:
                    break
        elif wt == 2:
            ln, n = 0, 0
            while True:
                b = buf[i]; i += 1
                ln |= (b & 0x7F) << n; n += 7
                if not b & 0x80:
                    break
            v = buf[i:i + ln]; i += ln
        elif wt == 5:
            v = buf[i:i + 4]; i += 4
        elif wt == 1:
            v = buf[i:i + 8]; i += 8
        else:
            raise ValueError("bad wire type")
        out.setdefault(field, []).append(v)
    return out


class LcdPlayer:
    """Plays frames on the Flipper's own screen through a running Mirror, until stopped.

    get() returns (list of 1024-byte frames, fps) each time, so edits show up live. If the mirror steps
    aside for a USB job, the virtual display ends with its session and is started again afterwards."""

    def __init__(self, mirror, get, on_state=lambda state: None):
        self.mirror, self.get, self.on_state = mirror, get, on_state
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="lcd-player")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(3)

    def _run(self):
        m, session, i = self.mirror, None, 0
        try:
            while not self._stop.is_set():
                if not m.running:
                    session = None
                    self._stop.wait(.1)
                    continue
                if session != m.session:
                    if not m.virtual_start():
                        self._stop.wait(.3)
                        continue
                    session = m.session
                    self.on_state("live")
                frames, fps = self.get()
                if not frames:
                    self._stop.wait(.1)
                    continue
                t = time.monotonic()
                m.virtual_frame(frames[i % len(frames)])
                i += 1
                self._stop.wait(max(0.0, 1 / max(1, fps) - (time.monotonic() - t)))
        finally:
            if session is not None and session == m.session and m.running:
                m.virtual_stop()
            self.on_state("stopped")


class Mirror:
    """Streams the Flipper's screen and sends button presses. Holds the port while running."""

    def __init__(self, port, sdk_dirs, on_frame, on_state=lambda state, detail="": None):
        self.port, self.sdk_dirs = port, sdk_dirs
        self.on_frame, self.on_state = on_frame, on_state
        self.running = False
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._ser = None
        self._send_lock = threading.Lock()
        self._cmd = 1
        self._ending = False      # set when we asked the Flipper to end the RPC session
        self._stop_sent = False   # stop_session already sent for this run
        self.session = 0          # goes up each time the RPC session (re)starts
        self._ack = threading.Event()
        self._wait_cmd = 0

    # -- control
    def start(self):
        global _mirror
        if _mirror is not None and _mirror is not self:
            _mirror.stop()
        _mirror = self
        self._stop.clear()
        self._start_thread()

    def active(self):
        """Streaming, or about to (its thread is alive and wasn't told to stop)."""
        return not self._stop.is_set() and self._thread is not None and self._thread.is_alive() and not self._ending

    def _start_thread(self):
        self._ending = False
        self._stop_sent = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="mirror")
        self._thread.start()

    def stop(self):
        global _mirror
        self._stop.set()
        self._end_session()
        if self._thread:
            self._thread.join(5)
        self.running = False
        if _mirror is self:
            _mirror = None
        self.on_state("stopped")

    def pause(self):
        """Hand the port back for a CLI job; resume() continues streaming."""
        self._paused.set()
        self._end_session()
        if self._thread:
            self._thread.join(5)
        self.on_state("paused")

    def resume(self):
        if self._stop.is_set():
            return
        self._paused.clear()
        self._start_thread()

    def press(self, key, kind="short"):
        """kind: 'short' (tap), 'long', or raw 'press'/'release'/'repeat'."""
        k = KEYS[key]
        if kind == "short":
            seq = ["press", "short", "release"]
        elif kind == "long":
            seq = ["press", "long", "release"]
        else:
            seq = [kind]
        for t in seq:
            self._send(23, _field_varint(1, k) + _field_varint(2, TYPES[t]))

    # -- our own pictures on the Flipper's screen (RPC virtual display; the Flipper drops it when the session ends)
    def virtual_start(self):
        return self._request(26)

    def virtual_stop(self):
        return self._request(27)

    def virtual_frame(self, rows):
        """rows: 1024 bytes of XBM rows, the layout the firmware draws straight to the screen.
        The firmware never answers a frame, so there's nothing to wait for; the caller paces them."""
        self._send(22, _field_bytes(1, rows))

    def _request(self, field, payload=b"", timeout=1.0):
        """Send and wait for the Flipper's reply, so frames never pile up faster than it takes them."""
        ser = self._ser
        if ser is None or not self.running:
            return False
        with self._send_lock:
            self._cmd += 1
            self._wait_cmd = self._cmd
            self._ack.clear()
            try:
                ser.write(_main(self._cmd, field, payload))
            except (serial.SerialException, OSError):
                return False
        return self._ack.wait(timeout)

    # -- internals
    def _send(self, field, payload=b""):
        ser = self._ser
        if ser is None or not self.running:
            return
        with self._send_lock:
            self._cmd += 1
            try:
                ser.write(_main(self._cmd, field, payload))
            except (serial.SerialException, OSError):
                pass

    def _end_session(self):
        """Ask the Flipper to stop streaming and leave RPC mode; the reader thread then exits
        and closes the port itself (callers join the thread)."""
        if self._ser is not None and self.running:
            self._send(21)          # stop screen stream
            self._send(19)          # stop_session -> back to CLI
            self._stop_sent = True
        self._ending = True

    def _run(self):
        with _port_lock:
            if self._ending:                    # paused or stopped while waiting for the port
                return
            try:
                mod, st = _open_guarded(self.port, self.sdk_dirs)
            except DeviceError as e:
                self.on_state("error", str(e))
                return
            ser = st.port
            try:
                ser.timeout = 0.5
                st.send("start_rpc_session\r")
                st.read.until("\n")             # the echoed command line
                ser.reset_input_buffer()
                self._ser = ser
                self.running = True
                self.session += 1
                self._send(20)                  # start screen stream
                self.on_state("live")
                pending = bytes(st.read.buffer)  # anything the CLI reader already buffered
                st.read.buffer = bytearray()
                buf = io.BytesIO(pending)

                def read(n):
                    data = buf.read(n)
                    while len(data) < n:
                        if self._ending:
                            return b""
                        try:
                            more = ser.read(n - len(data))   # times out every 0.5 s to re-check _ending
                        except (serial.SerialException, OSError, TypeError):
                            return b""
                        data += more
                    return data

                while not self._ending:
                    try:
                        ln = _read_varint(read)
                    except EOFError:
                        break
                    msg = read(ln)
                    if len(msg) < ln:
                        break
                    fields = _parse(msg)
                    if self._wait_cmd and fields.get(1, [0])[0] == self._wait_cmd:
                        self._ack.set()
                    if 22 in fields:                          # gui_screen_frame
                        frame = _parse(fields[22][0])
                        data = frame.get(1, [b""])[0]
                        orient = frame.get(2, [0])[0]
                        if len(data) == 1024:
                            self.on_frame(data, orient)
                    elif 19 in fields or (2 in fields and fields[2][0] != 0 and 22 not in fields):
                        pass                                  # session stop ack / status replies
                if self._ending and not self._stop_sent:      # told to end before we were streaming
                    self._send(21)
                    self._send(19)
                    self._stop_sent = True
            finally:
                dropped = not self._ending
                self.running = False
                self._ser = None
                time.sleep(0.2)          # let the Flipper finish switching back to the CLI
                _safe_stop(st)
                if dropped and not self._stop.is_set() and not self._paused.is_set():
                    self.on_state("error", "The connection to the Flipper dropped.")
