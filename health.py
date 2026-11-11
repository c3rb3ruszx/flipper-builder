"""Flipper health: memory, battery, SD card, and how much memory each set of animations needs.

The firmware keeps every frame of the playing animation in RAM at its file size, and every icon and
font of the selected asset pack from boot on. A frame file of exactly ceil(w/8)*h + 1 bytes is stored
uncompressed (a compressed one is always smaller), which is what old tools and early Workbench wrote.
"""

import re

COMMANDS = ("free", "info power", "storage info /ext", "uptime", "loader info")
MEM_OK, MEM_HIGH = 40 * 1024, 70 * 1024


def _num(pattern, text):
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else None


def _info(text):
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([\w.]+)\s*:\s*(.*?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def parse(raw):
    """{command: output} -> {heap, power, sd, uptime, app}."""
    free = raw.get("free", "")
    heap = {"free": _num(r"Free heap size:\s*(\d+)", free), "total": _num(r"Total heap size:\s*(\d+)", free),
            "min": _num(r"Minimum heap size:\s*(\d+)", free), "block": _num(r"Maximum heap block:\s*(\d+)", free)}
    p = _info(raw.get("info power", ""))
    level = p.get("charge.level", "")
    power = {"level": int(level) if level.isdigit() else None, "state": p.get("charge.state"),
             "voltage": p.get("battery.voltage"), "temp": p.get("battery.temp"), "health": p.get("battery.health")}
    sd_text = raw.get("storage info /ext", "")
    sd = {"total_kib": _num(r"(\d+)\s*KiB total", sd_text), "free_kib": _num(r"(\d+)\s*KiB free", sd_text),
          "type": (re.search(r"Type:\s*(\S+)", sd_text) or [None, None])[1]}
    up = re.search(r"Uptime:\s*(\S+)", raw.get("uptime", ""))
    app = re.search(r'"([^"]+)"', raw.get("loader info", ""))
    return {"heap": heap, "power": power, "sd": sd, "uptime": up.group(1) if up else None,
            "app": app.group(1) if app else None}


def _meta(text):
    m = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        k, sep, v = line.partition(":")
        if sep and k.strip() not in m:
            m[k.strip()] = v.strip()
    try:
        w, h = int(m["Width"]), int(m["Height"])
        order = [int(x) for x in m["Frames order"].split()]
        return w, h, max(order) if order else 0
    except (KeyError, ValueError):
        return None


def raw_size(w, h):
    return (w + 7) // 8 * h + 1


def pack_report(name, root, files, metas):
    """Memory use of one asset pack (or the dolphin folder) from its file list.

    files: [(remote path, size)]; metas: {animation folder: meta.txt text}."""
    sizes = dict(files)
    anims, uncompressed = [], []
    for folder, text in sorted(metas.items()):
        info = _meta(text)
        if not info:
            continue
        w, h, top = info
        frames = [(f"{folder}/frame_{i}.bm", sizes.get(f"{folder}/frame_{i}.bm")) for i in range(top + 1)]
        present = [(p, s) for p, s in frames if s is not None]
        raw = [(p, w, h) for p, s in present if s == raw_size(w, h)]
        uncompressed += raw
        anims.append({"name": folder.rsplit("/", 1)[-1], "frames": len(present), "mem": sum(s for _, s in present),
                      "raw": len(raw)})
    resident = 0
    icons = fonts = 0
    for p, s in files:
        rel = p[len(root) + 1:]
        if rel.startswith("Icons/"):
            if p.endswith(".bmx"):
                resident += max(0, s - 8)
                icons += 1
            elif re.search(r"/frame_\d+\.bm$", p):
                resident += s
            elif p.endswith("/meta"):
                icons += 1
        elif rel.startswith("Fonts/") and p.endswith(".u8f"):
            resident += s
            fonts += 1
    peak = max((a["mem"] for a in anims), default=0)
    total = resident + peak
    anims.sort(key=lambda a: -a["mem"])
    return {"name": name, "path": root, "anims": len(anims), "icons": icons, "fonts": fonts, "resident": resident,
            "peak": peak, "total": total, "level": "ok" if total <= MEM_OK else "warn" if total <= MEM_HIGH else "high",
            "heaviest": anims[:3], "uncompressed": uncompressed}
