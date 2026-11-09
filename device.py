"""Talking to a Flipper Zero over USB: find it, read its firmware info, copy files to its SD card.

The storage protocol comes straight from Flipper's own SDK (scripts/flipper/storage.py, the code
fbt and ufbt use), loaded at runtime from whichever SDK is downloaded, so it stays in step with
the firmware. Only one program can hold the Flipper's serial port: close qFlipper first.
"""

import importlib
import re
import sys
import threading
import time
from pathlib import Path

import serial
import serial.tools.list_ports as list_ports

FLIPPER_VID, FLIPPER_PID = 0x0483, 0x5740
_port_lock = threading.Lock()  # one conversation with the Flipper at a time


class DeviceError(Exception):
    pass


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
    with _port_lock:
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
    """Map device info to (firmware id, nice name). Firmware id matches flipper_builder.FIRMWARES."""
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
    with _port_lock:
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
    with _port_lock:
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


def read_many(port, sdk_dirs, items, on_file, should_stop=lambda: False):
    """Read several files in ONE session (opening the port costs ~0.5 s per session).
    items: [{path, size}]; on_file(item, bytes) for each. Stops early if should_stop()."""
    with _port_lock:
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


def open_app(port, sdk_dirs, path):
    """Open an app on the Flipper, the way the SDK's runfap.py does: close the current app, then open."""
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
                raise DeviceError("The Flipper is busy with an app that won't close. Exit it on the Flipper.")
        st.send_and_wait_eol(f'loader open "{path}"\r')
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
