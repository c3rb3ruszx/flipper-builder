"""Build and import Momentum asset packs on the PC.

A pack lives in the library folder in exactly the shape it has on the SD card, so installing is a
plain copy to /ext/asset_packs/<name>:

    <name>/Anims/manifest.txt, <anim>/meta.txt, <anim>/frame_N.bm
    <name>/Icons/<group>/<icon>.bmx, <group>/<animated icon>/meta + frame_NN.bm
    <name>/Fonts/<slot>.u8f

Importing takes a zip or a folder holding ready packs, source packs (PNGs, as Momentum's own
asset_packer.py expects) or a plain dolphin folder (manifest.txt next to the animations). Only files
the firmware reads are kept, and each one is checked the way the firmware will read it.
"""

import base64
import io
import re
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageSequence

import anim
import fapmeta
from iconslots import SLOTS

MAX_NAME = 31                       # the firmware keeps the pack name in a 32-byte field
MAX_ICON_FRAMES = 30
MAX_IMPORT_BYTES = 200 * 1024 * 1024
MAX_IMPORT_FILES = 20000
FONT_SLOTS = ("Primary", "Secondary", "Keyboard", "BigNumbers", "BatteryPercent")
FONT_HEADER = 23                    # u8g2 font header; the firmware ignores anything this small
MANIFEST_HEAD = "Filetype: Flipper Animation Manifest\nVersion: 1\n"
MANIFEST_KEYS = ("Min butthurt", "Max butthurt", "Min level", "Max level", "Weight")
SKIP_DIRS = {"__MACOSX"}
RESERVED = {"con", "prn", "aux", "nul"} | {f"{d}{i}" for d in ("com", "lpt") for i in range(1, 10)}
# Rough guide for what a pack may ask of ~120 KB of free memory on an idle Flipper.
MEM_OK, MEM_HIGH = 40 * 1024, 70 * 1024


class PackError(ValueError):
    pass


def safe_pack_name(name):
    n = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 _.\-]+", "", name or "")).strip(" .")[:MAX_NAME].strip(" .")
    if n.lower() in RESERVED:
        n = f"{n} pack"
    return n


def _natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def _files(d):
    return sorted((p for p in d.iterdir() if p.is_file() and not p.is_symlink()), key=_natural)


def _dirs(d):
    return sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink()
                  and not p.name.startswith(".") and p.name not in SKIP_DIRS)


def _child(d, name):
    """Subfolder or file called name, ignoring case (the SD card ignores it too)."""
    for p in d.iterdir():
        if p.name.lower() == name.lower() and not p.is_symlink():
            return p
    return None


def _row(w):
    return (w + 7) // 8


def bm_ok(data, w, h):
    """Would the firmware accept these .bm bytes for a w x h picture?"""
    raw = _row(w) * h
    if not data or len(data) > raw + 1:
        return False
    if data[0] == 0:
        return len(data) == raw + 1
    if data[0] == 1 and len(data) >= 4:
        return 4 + struct.unpack_from("<H", data, 2)[0] == len(data)
    return False


def bm_raw(data, w, h):
    """.bm bytes -> the picture as XBM rows, for previews."""
    size = _row(w) * h
    if data[0] == 0:
        return data[1:1 + size]
    return fapmeta._heatshrink_decode(data[4:], out_len=size)


def _png_bm(path, w=None, h=None):
    """A source PNG -> .bm bytes, converted the way Momentum's packer does. None if it's the wrong size."""
    with Image.open(path) as im:
        if w is not None and im.size != (w, h):
            return None
        return anim.bm_packed(im.convert("1"))


def bmx_bytes(img1):
    return struct.pack("<II", *img1.size) + anim.bm_packed(img1)


# ---------------------------------------------------------------------- manifest + meta

def parse_kv(text):
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        k, sep, v = line.partition(":")
        if sep and k.strip() not in out:
            out[k.strip()] = v.strip()
    return out


def parse_manifest(text):
    """Manifest text -> list of {key: value}, one per animation."""
    entries, cur = [], None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        if k == "Name":
            cur = {"Name": v}
            entries.append(cur)
        elif cur is not None:
            cur[k] = v
    return entries


def manifest_text(entries):
    blocks = ["\n".join(f"{k}: {v}" for k, v in e.items()) for e in entries]
    return MANIFEST_HEAD + "\n" + "\n\n".join(blocks) + "\n"


def _new_entry(name, weight=3):
    return {"Name": name, "Min butthurt": "0", "Max butthurt": "14", "Min level": "1", "Max level": "30",
            "Weight": str(int(weight))}


def _safe_rel(name):
    parts = name.replace("\\", "/").split("/")
    return bool(name) and all(p and p not in (".", "..") and ":" not in p for p in parts)


def _meta_info(text):
    """meta.txt -> (width, height, fps, highest frame index), or a string saying what's wrong."""
    m = parse_kv(text)
    try:
        w, h, fps = int(m["Width"]), int(m["Height"]), int(m["Frame rate"])
        passive, active = int(m["Passive frames"]), int(m["Active frames"])
        order = [int(x) for x in m["Frames order"].split()]
    except KeyError as e:
        return f"meta.txt has no {e.args[0]}"
    except ValueError:
        return "meta.txt has a value that isn't a number"
    if not (0 < w <= anim.W and 0 < h <= anim.H):
        return f"it is {w}x{h}; the screen is {anim.W}x{anim.H}"
    if fps <= 0:
        return "its frame rate is 0"
    if not order or len(order) != passive + active or len(order) > 255 or min(order) < 0:
        return "its frame list doesn't match its frame counts"
    return w, h, fps, max(order)


# ---------------------------------------------------------------------- compile (import)

def _compile_anims(src, dst, warn):
    man = _child(src, "manifest.txt")
    if man and man.is_file():
        entries = parse_manifest(man.read_text("utf-8", "replace"))
    else:
        entries = [_new_entry(d.name) for d in _dirs(src) if _child(d, "meta.txt")]
    kept, seen = [], set()
    for e in entries:
        name = e["Name"].replace("\\", "/")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        if not _safe_rel(name):
            warn(f"Skipped an animation with an unsafe name: {name}")
            continue
        missing = [k for k in MANIFEST_KEYS if k not in e]
        if missing:
            warn(f"Skipped {name}: its manifest entry has no {missing[0]}")
            continue
        a = src / name
        meta = a / "meta.txt"
        if not meta.is_file() or meta.is_symlink():
            warn(f"Skipped {name}: the manifest lists it but its folder or meta.txt is missing")
            continue
        text = meta.read_text("utf-8", "replace").replace("\r\n", "\n")
        info = _meta_info(text)
        if isinstance(info, str):
            warn(f"Skipped {name}: {info}")
            continue
        w, h, _fps, top = info
        frames, bad = [], None
        for i in range(top + 1):
            png, bm = a / f"frame_{i}.png", a / f"frame_{i}.bm"
            data = None
            if png.is_file():
                data = _png_bm(png, w, h)
                if data is None:
                    bad = f"frame_{i}.png isn't {w}x{h}"
            elif bm.is_file():
                data = bm.read_bytes()
                if not bm_ok(data, w, h):
                    bad = f"frame_{i}.bm isn't a valid {w}x{h} frame"
            else:
                bad = f"frame_{i} is missing"
            if bad:
                break
            frames.append(data)
        if bad:
            warn(f"Skipped {name}: {bad}")
            continue
        out = dst / name
        out.mkdir(parents=True, exist_ok=True)
        (out / "meta.txt").write_text(text, encoding="utf-8", newline="\n")
        for i, data in enumerate(frames):
            (out / f"frame_{i}.bm").write_bytes(data)
        kept.append({**e, "Name": name})
    if kept:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "manifest.txt").write_text(manifest_text(kept), encoding="utf-8", newline="\n")
    return len(kept)


def _compile_icon_animated(src, dst, label, warn):
    files = [f for f in _files(src) if f.suffix.lower() in (".png", ".bm")]
    pngs = {f.stem.lower() for f in files if f.suffix.lower() == ".png"}
    files = [f for f in files if f.suffix.lower() == ".png" or f.stem.lower() not in pngs]
    if not files or len(files) > 255:
        warn(f"Skipped icon {label}: it needs between 1 and 255 frames")
        return 0
    rate, meta = _child(src, "frame_rate"), _child(src, "meta")
    if any(f.suffix.lower() == ".png" for f in files):
        if not rate:
            warn(f"Skipped icon {label}: PNG frames need a frame_rate file")
            return 0
        try:
            fps = int(rate.read_text("utf-8", "replace").strip())
            first = next(f for f in files if f.suffix.lower() == ".png")
            with Image.open(first) as im:
                w, h = im.size
        except (ValueError, OSError):
            warn(f"Skipped icon {label}: couldn't read its frame_rate or first frame")
            return 0
    else:
        if not meta or meta.stat().st_size != 16:
            warn(f"Skipped icon {label}: .bm frames need a meta file")
            return 0
        w, h, fps, count = struct.unpack("<IIII", meta.read_bytes())
        if not 0 < count <= len(files):
            warn(f"Skipped icon {label}: meta says {count} frames, found {len(files)}")
            return 0
        files = files[:count]            # the firmware only reads as many as meta says
    if not (0 < w <= anim.W and 0 < h <= anim.H) or fps <= 0:
        warn(f"Skipped icon {label}: bad size or frame rate")
        return 0
    frames = []
    for f in files:
        data = _png_bm(f, w, h) if f.suffix.lower() == ".png" else f.read_bytes()
        if data is None or not bm_ok(data, w, h):
            warn(f"Skipped icon {label}: {f.name} isn't a valid {w}x{h} frame")
            return 0
        frames.append(data)
    dst.mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(frames):
        (dst / f"frame_{i:02}.bm").write_bytes(data)
    (dst / "meta").write_bytes(struct.pack("<IIII", w, h, fps, len(frames)))
    return 1


def _compile_icon_static(src, dst, label, warn):
    if src.suffix.lower() == ".png":
        with Image.open(src) as im:
            if im.width > anim.W or im.height > anim.H:
                warn(f"Skipped icon {label}: it is bigger than the screen")
                return 0
            data = bmx_bytes(im.convert("1"))
    else:
        data = src.read_bytes()
        if len(data) < 9:
            warn(f"Skipped icon {label}: the file is too short")
            return 0
        w, h = struct.unpack_from("<II", data)
        if not (0 < w <= anim.W and 0 < h <= anim.H) or not bm_ok(data[8:], w, h):
            warn(f"Skipped icon {label}: not a valid .bmx file")
            return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.with_suffix(".bmx").write_bytes(data)
    return 1


def _compile_icons(src, dst, warn):
    n = 0
    for group in _dirs(src):
        for d in _dirs(group):
            n += _compile_icon_animated(d, dst / group.name / d.name, f"{group.name}/{d.name}", warn)
        for f in _files(group):
            if f.suffix.lower() in (".png", ".bmx") and not f.name.startswith("."):
                n += _compile_icon_static(f, dst / group.name / f.name, f"{group.name}/{f.stem}", warn)
    return n


def font_bytes(path):
    """A u8g2 font (.u8f, or the .c source) -> .u8f bytes."""
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == ".c":
        try:
            code = data.split(b' U8G2_FONT_SECTION("')[1].split(b'") =')[1].strip()
        except IndexError:
            raise PackError(f"{path.name} doesn't look like a u8g2 font source.")
        out = b""
        for line in code.splitlines():
            if line.count(b'"') == 2:
                out += line[line.find(b'"') + 1:line.rfind(b'"')].decode("unicode_escape").encode("latin_1")
        data = out + b"\0"
    if len(data) <= FONT_HEADER:
        raise PackError(f"{path.name} is too small to be a font.")
    return data


def _compile_fonts(src, dst, warn):
    n = 0
    for f in _files(src):
        if f.suffix.lower() not in (".c", ".u8f") or f.name.startswith("."):
            continue
        try:
            data = font_bytes(f)
        except (PackError, ValueError, UnicodeError) as e:
            warn(f"Skipped font {f.name}: {e}")
            continue
        dst.mkdir(parents=True, exist_ok=True)
        (dst / f"{f.stem}.u8f").write_bytes(data)
        n += 1
    return n


def _is_dolphin(d):
    m = _child(d, "manifest.txt")
    return bool(m and m.is_file() and "Name:" in m.read_text("utf-8", "replace"))


def compile_pack(src, dst, warn):
    """Source or ready pack folder -> checked pack in dst. Returns how many assets made it."""
    n = 0
    anims = _child(src, "Anims")
    if anims and anims.is_dir():
        n += _compile_anims(anims, dst / "Anims", warn)
    elif _is_dolphin(src):
        n += _compile_anims(src, dst / "Anims", warn)
    icons, fonts = _child(src, "Icons"), _child(src, "Fonts")
    if icons and icons.is_dir():
        n += _compile_icons(icons, dst / "Icons", warn)
    if fonts and fonts.is_dir():
        n += _compile_fonts(fonts, dst / "Fonts", warn)
    return n


def _is_root(d):
    for n in ("Anims", "Icons", "Fonts"):
        c = _child(d, n)
        if c and c.is_dir():
            return True
    return _is_dolphin(d)


def find_roots(base, depth=4):
    """Pack folders under base, shallowest first. A pack's own subfolders aren't searched."""
    found, level = [], [base]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            if _is_root(d):
                found.append(d)
            else:
                nxt += _dirs(d)
        level = nxt
    return found


def _unzip(src, out):
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        if len(infos) > MAX_IMPORT_FILES or sum(i.file_size for i in infos) > MAX_IMPORT_BYTES:
            raise PackError("That zip is too big to be an asset pack.")
        for i in infos:
            parts = i.filename.replace("\\", "/").split("/")
            if i.filename.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                raise PackError(f"Unsafe path in the zip: {i.filename}")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise PackError(f"The zip contains a link: {i.filename}")
        z.extractall(out)


def _free_name(root, name):
    taken = {p.name.lower() for p in Path(root).iterdir()} if Path(root).exists() else set()
    if name.lower() not in taken:
        return name
    for i in range(2, 100):
        n = f"{name[:MAX_NAME - 3].rstrip(' .')} {i}"
        if n.lower() not in taken:
            return n
    raise PackError(f"You already have too many packs called {name}.")


def import_path(src, root):
    """Zip or folder -> packs in the library. Returns {"imported": [names], "warnings": [...]}."""
    src, root = Path(src), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    warnings, imported = [], []
    with tempfile.TemporaryDirectory(prefix="fw_pack_", dir=root.parent) as td:
        td = Path(td)
        if src.is_file():
            base = td / "in"
            try:
                _unzip(src, base)
            except zipfile.BadZipFile:
                raise PackError(f"{src.name} isn't a valid zip file.")
        else:
            base = src
        roots = find_roots(base)
        if not roots:
            raise PackError("No asset pack found there. A pack is a folder with an Anims, Icons or Fonts folder inside.")
        seen = set()
        for i, r in enumerate(roots):
            raw = src.stem if (r == base and src.is_file()) else r.name
            name = safe_pack_name(raw)
            if not name:
                warnings.append(f"Skipped a folder with no usable name: {raw}")
                continue
            if name.lower() in seen:
                warnings.append(f"Skipped a second copy of {name}")
                continue
            seen.add(name.lower())
            stage = td / f"out{i}"
            stage.mkdir()
            try:
                count = compile_pack(r, stage, lambda m, n=name: warnings.append(f"{n}: {m}"))
            except (OSError, ValueError) as e:
                warnings.append(f"{name}: couldn't read it ({e})")
                continue
            if not count:
                warnings.append(f"{name}: nothing usable inside")
                continue
            final = _free_name(root, name)
            shutil.move(str(stage), str(root / final))
            imported.append(final)
    if not imported:
        raise PackError("Nothing could be imported. " + (warnings[0] if warnings else ""))
    return {"imported": imported, "warnings": warnings}


# ---------------------------------------------------------------------- library

def pack_dir(root, name):
    if not name or safe_pack_name(name) != name:
        raise PackError("That isn't a valid pack name.")
    d = Path(root) / name
    if not d.is_dir():
        raise PackError(f"There is no pack called {name}.")
    return d


def create(root, name):
    clean = safe_pack_name(name)
    if not clean:
        raise PackError("Give the pack a name (letters, numbers, spaces, - _ .).")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(p.name.lower() == clean.lower() for p in root.iterdir()):
        raise PackError(f"You already have a pack called {clean}.")
    (root / clean).mkdir()
    return clean


def delete(root, name):
    shutil.rmtree(pack_dir(root, name))


def _read_manifest(d):
    m = d / "Anims" / "manifest.txt"
    return parse_manifest(m.read_text("utf-8", "replace")) if m.is_file() else []


def _write_manifest(d, entries):
    a = d / "Anims"
    if entries:
        a.mkdir(parents=True, exist_ok=True)
        (a / "manifest.txt").write_text(manifest_text(entries), encoding="utf-8", newline="\n")
    elif a.exists():
        shutil.rmtree(a)                # no manifest = the Flipper falls back to its normal animations


def add_anim(root, pack, name, result, weight=3):
    d = pack_dir(root, pack)
    name = anim.safe_name(name)
    target = d / "Anims" / name
    if target.exists():
        shutil.rmtree(target)
    anim.write_folder(result, target)
    entries = [e for e in _read_manifest(d) if e["Name"] != name] + [_new_entry(name, weight)]
    _write_manifest(d, entries)
    return name


def _inside(base, rel):
    if not _safe_rel(rel):
        raise PackError("That isn't a valid path.")
    return base / rel


def remove_anim(root, pack, name):
    d = pack_dir(root, pack)
    target = _inside(d / "Anims", name)
    if target.exists():
        shutil.rmtree(target)
    _write_manifest(d, [e for e in _read_manifest(d) if e["Name"] != name])


def set_weight(root, pack, name, weight):
    d = pack_dir(root, pack)
    entries = _read_manifest(d)
    for e in entries:
        if e["Name"] == name:
            e["Weight"] = str(int(weight))
    _write_manifest(d, entries)


def slot(path):
    s = next((s for s in SLOTS if s[0] == path), None)
    if not s:
        raise PackError(f"Flipper has no icon called {path}.")
    return s


def icon_frames(data, path, opts):
    """Image bytes -> (1-bit frames for this icon slot, fps)."""
    _, w, h, animated = slot(path)
    opts = opts or {}
    with Image.open(io.BytesIO(data)) as im:
        src = [f.convert("RGBA") for f in ImageSequence.Iterator(im)]
        dur = int(im.info.get("duration") or 100)
    if not animated:
        src = src[:1]
    elif len(src) > MAX_ICON_FRAMES:
        step = len(src) / MAX_ICON_FRAMES
        src = [src[int(i * step)] for i in range(MAX_ICON_FRAMES)]
    style = opts.get("dither") or ("threshold" if max(w, h) <= 32 else "floyd")
    frames = [anim.to_1bit(f, opts.get("fit", "contain"), style, int(opts.get("threshold", 128)),
                           bool(opts.get("invert")), 1.0, (w, h)) for f in src]
    return frames, max(1, min(10, round(1000 / max(1, dur))))


def add_icon(root, pack, path, frames, fps):
    d = pack_dir(root, pack)
    _, w, h, animated = slot(path)
    base = d / "Icons" / path
    if base.is_dir():
        shutil.rmtree(base)
    if animated:
        base.mkdir(parents=True)
        for i, f in enumerate(frames):
            (base / f"frame_{i:02}.bm").write_bytes(anim.bm_packed(f))
        (base / "meta").write_bytes(struct.pack("<IIII", w, h, fps, len(frames)))
    else:
        base.parent.mkdir(parents=True, exist_ok=True)
        (base.parent / f"{base.name}.bmx").write_bytes(bmx_bytes(frames[0]))


def _prune(path, stop):
    while path != stop and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        path = path.parent


def remove_icon(root, pack, path):
    d = pack_dir(root, pack)
    base = _inside(d / "Icons", path)
    if base.is_dir():
        shutil.rmtree(base)
    bmx = base.parent / f"{base.name}.bmx"
    if bmx.is_file():
        bmx.unlink()
    _prune(base.parent, d)


def add_font(root, pack, font_slot, src):
    d = pack_dir(root, pack)
    if font_slot not in FONT_SLOTS:
        raise PackError("Unknown font slot.")
    data = font_bytes(src)
    (d / "Fonts").mkdir(exist_ok=True)
    (d / "Fonts" / f"{font_slot}.u8f").write_bytes(data)


def remove_font(root, pack, font_slot):
    d = pack_dir(root, pack)
    if font_slot not in FONT_SLOTS:
        raise PackError("Unknown font slot.")
    f = d / "Fonts" / f"{font_slot}.u8f"
    if f.is_file():
        f.unlink()
    _prune(d / "Fonts", d)


def _thumb(data, w, h):
    return {"w": w, "h": h, "bits": base64.b64encode(bm_raw(data, w, h)).decode()}


def _read_anims(d):
    out = []
    for e in _read_manifest(d):
        a = d / "Anims" / e["Name"]
        info = _meta_info((a / "meta.txt").read_text("utf-8", "replace")) if (a / "meta.txt").is_file() else "missing"
        if isinstance(info, str):
            continue
        w, h, fps, top = info
        mem = sum((a / f"frame_{i}.bm").stat().st_size for i in range(top + 1))
        out.append({"name": e["Name"], "w": w, "h": h, "fps": fps, "frames": top + 1, "mem": mem,
                    "weight": int(e.get("Weight") or 3), "dir": a})
    return out


def _read_icons(d):
    out, icons = [], d / "Icons"
    if not icons.is_dir():
        return out
    for group in _dirs(icons):
        for a in _dirs(group):
            meta = a / "meta"
            if meta.is_file():
                w, h, _fps, count = struct.unpack("<IIII", meta.read_bytes())
                frames = sorted(a.glob("frame_*.bm"))
                out.append({"path": f"{group.name}/{a.name}", "w": w, "h": h, "frames": count,
                            "mem": sum(f.stat().st_size for f in frames), "first": frames[0] if frames else None})
        for f in group.glob("*.bmx"):
            w, h = struct.unpack("<II", f.read_bytes()[:8])
            out.append({"path": f"{group.name}/{f.stem}", "w": w, "h": h, "frames": 1,
                        "mem": f.stat().st_size - 8, "first": f})
    return sorted(out, key=lambda i: i["path"].lower())


def _read_fonts(d):
    f = d / "Fonts"
    return [{"name": p.stem, "mem": p.stat().st_size} for p in sorted(f.glob("*.u8f"))] if f.is_dir() else []


def memory(anims, icons, fonts):
    """Icons and fonts stay in RAM all the time; one animation at a time joins them."""
    resident = sum(i["mem"] for i in icons) + sum(f["mem"] for f in fonts)
    peak = max((a["mem"] for a in anims), default=0)
    total = resident + peak
    return {"resident": resident, "peak": peak, "total": total,
            "level": "ok" if total <= MEM_OK else "warn" if total <= MEM_HIGH else "high"}


def _size(d):
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


def summary(d):
    a, i, f = _read_anims(d), _read_icons(d), _read_fonts(d)
    return {"name": d.name, "anims": len(a), "icons": len(i), "fonts": len(f), "size": _size(d),
            "memory": memory(a, i, f)}


def list_packs(root):
    root = Path(root)
    return [summary(d) for d in _dirs(root)] if root.is_dir() else []


def detail(root, name):
    d = pack_dir(root, name)
    anims, icons = [], []
    for a in _read_anims(d):
        first = (a.pop("dir") / "frame_0.bm").read_bytes()
        anims.append({**a, "thumb": _thumb(first, a["w"], a["h"])})
    for i in _read_icons(d):
        first = i.pop("first")
        thumb = None
        if first:
            data = first.read_bytes()
            thumb = _thumb(data[8:] if first.suffix == ".bmx" else data, i["w"], i["h"])
        icons.append({**i, "thumb": thumb})
    return {**summary(d), "animations": anims, "iconList": icons, "fontList": _read_fonts(d)}


def export_zip(root, name, out):
    d = pack_dir(root, name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                z.write(p, f"{d.name}/{p.relative_to(d).as_posix()}")
    return out
