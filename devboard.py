"""Flipper Wi-Fi dev board (ESP32-S2): flash the official Black Magic debugger firmware, or a compatible
firmware file the user drops in.

Before anything is written, every image is checked: ESP image magic 0xE9 with the ESP32-S2 chip id (2),
or a partition table (magic 0xAA50), at addresses that make sense for an ESP32-S2. Flashing is esptool,
the same tool the official flash.command uses. The board recovers from a bad flash by holding BOOT
while plugging it in, so the Flipper itself is never involved.
"""

import hashlib
import io
import json
import re
import tarfile
import urllib.request
import zipfile
from pathlib import Path

DIRECTORY = "https://update.flipperzero.one/blackmagic-firmware/directory.json"
UA = {"User-Agent": "flipper-workbench"}
ESP_VID = 0x303A                      # Espressif USB (the board's own USB port)
CHIP_ID_S2 = 2
FLASH_SIZE = 4 * 1024 * 1024


class BoardError(Exception):
    pass


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def official():
    d = json.loads(_get(DIRECTORY))
    out = []
    for ch in d.get("channels") or []:
        for v in (ch.get("versions") or [])[:1]:
            f = next((f for f in v.get("files") or [] if f.get("type") == "full_tgz" and f.get("target") == "s2"), None)
            if f and f.get("sha256"):
                out.append({"channel": ch.get("id"), "version": str(v.get("version")), "url": f["url"], "sha256": f["sha256"].lower()})
    return out


def find_ports():
    import serial.tools.list_ports as lp
    return [{"port": p.device, "desc": p.description, "pid": p.pid} for p in lp.comports() if p.vid == ESP_VID]


def _check_image(name, data, addr):
    if data[:2] == b"\xaa\x50":
        if addr != 0x8000:
            raise BoardError(f"{name} is a partition table, which belongs at 0x8000, not {hex(addr)}.")
        return "partition table"
    if len(data) < 24 or data[0] != 0xE9:
        raise BoardError(f"{name} isn't an ESP firmware image.")
    chip = int.from_bytes(data[12:14], "little")
    if chip != CHIP_ID_S2:
        raise BoardError(f"{name} is built for a different ESP chip (id {chip}); the Flipper Wi-Fi dev board is an ESP32-S2.")
    return "bootloader" if addr == 0x1000 else "app"


def plan_from_bytes(name, data):
    """A user file -> [(address, label, bytes)]. Accepts:
    - a merged image (bootloader at 0x1000 inside) -> flashed at 0x0
    - a single app image -> flashed at 0x10000 (keeps the board's bootloader)
    - a .zip / .tgz with flasher_args.json (ESP-IDF) or flash.command (Flipper's format)"""
    low = name.lower()
    if low.endswith((".zip", ".tgz", ".tar.gz")):
        files = {}
        if low.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    if not n.endswith("/"):
                        files[n.replace("\\", "/").lstrip("./")] = z.read(n)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
                for m in t.getmembers():
                    if m.isfile():
                        files[m.name.lstrip("./")] = t.extractfile(m).read()
        layout = None
        fa = next((k for k in files if k.endswith("flasher_args.json")), None)
        if fa:
            base = fa.rsplit("/", 1)[0] + "/" if "/" in fa else ""
            ff = json.loads(files[fa]).get("flash_files") or {}
            layout = [(int(a, 16), base + f) for a, f in ff.items()]
        fc = next((k for k in files if k.endswith("flash.command")), None)
        if not layout and fc:
            base = fc.rsplit("/", 1)[0] + "/" if "/" in fc else ""
            layout = [(int(a, 16), base + f) for a, f in re.findall(r"(0x[0-9a-fA-F]+)\s+(\S+\.bin)", files[fc].decode())]
        if not layout:
            raise BoardError("That package has no flasher_args.json or flash.command, so I can't tell where each part goes.")
        plan = []
        for addr, f in sorted(layout):
            if f not in files:
                raise BoardError(f"The package lists {f} but doesn't contain it.")
            plan.append((addr, f.rsplit("/", 1)[-1], files[f]))
    elif low.endswith(".bin"):
        if len(data) > 0x1010 and data[0x1000] == 0xE9:
            plan = [(0x0, name, data)]
            _check_image(name + " (bootloader part)", data[0x1000:], 0x1000)
        else:
            plan = [(0x10000, name, data)]
    else:
        raise BoardError("Drop a .bin firmware image, or a .zip/.tgz firmware package.")
    for addr, label, blob in plan:
        if addr + len(blob) > FLASH_SIZE:
            raise BoardError(f"{label} doesn't fit in the board's 4 MB flash at {hex(addr)}.")
        if addr != 0x0:
            _check_image(label, blob, addr)
    return plan


def plan_official(target, cache_dir):
    data = _get(target["url"], timeout=120)
    if hashlib.sha256(data).hexdigest() != target["sha256"]:
        raise BoardError("The download doesn't match the checksum on Flipper's update server.")
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return plan_from_bytes("blackmagic.tgz", data)


def flash(port, plan, work_dir, log=print):
    """Write the plan with esptool (ESP32-S2, the official flash settings). Returns esptool's output."""
    import contextlib
    import esptool
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    args = ["--chip", "esp32s2", "-p", port, "-b", "460800", "--before", "default-reset", "--after", "hard-reset",
            "write-flash", "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "4MB"]
    for i, (addr, label, blob) in enumerate(plan):
        f = work / f"part{i}_{re.sub(r'[^A-Za-z0-9_.-]', '_', label)}"
        f.write_bytes(blob)
        args += [hex(addr), str(f)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            esptool.main(args)
    except SystemExit as e:
        if e.code not in (0, None):
            raise BoardError(_hint(buf.getvalue()))
    except Exception as e:  # noqa: BLE001
        raise BoardError(_hint(buf.getvalue() + str(e)))
    out = buf.getvalue()
    for line in out.splitlines():
        log(line)
    return out


def _hint(text):
    t = text.lower()
    if "failed to connect" in t or "no serial data" in t or "wrong boot mode" in t:
        return ("Couldn't talk to the board. Unplug it, hold its BOOT button while plugging it into this PC "
                "(its own USB port, not through the Flipper), then try again.")
    if "could not open port" in t or "permission" in t:
        return "The board's port is busy. Close anything else using it (Arduino IDE, a serial monitor) and try again."
    return "Flashing failed: " + (text.strip().splitlines() or ["unknown error"])[-1]
