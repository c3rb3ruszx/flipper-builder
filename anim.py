"""GIFs and images -> Flipper desktop animations (the firmware's own format).

An animation is a folder with meta.txt and frame_N.bm files. A .bm frame is a 0x00 byte, then the
1-bit picture as XBM rows (LSB = leftmost pixel, 1 = black), or, when that comes out smaller,
01 00 <length, 2 bytes LE> and the rows heatshrink-compressed (window 8, lookahead 4), exactly what
Flipper's asset packer writes. The firmware keeps every frame in RAM at its file size while an
animation plays, so compressing them is what keeps long animations from running it out of memory.
"""

import io
import re
from pathlib import Path

import heatshrink2
from PIL import Image, ImageOps, ImageSequence

W, H = 128, 64
MAX_FRAMES = 60
BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def safe_name(name):
    n = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return (n or "Workbench_Anim")[:40]


def load_frames(data, max_frames=MAX_FRAMES):
    """Image bytes (GIF/APNG/WebP/PNG/JPG) -> ([RGBA frames], [durations ms])."""
    im = Image.open(io.BytesIO(data))
    frames, durs = [], []
    for f in ImageSequence.Iterator(im):
        frames.append(f.convert("RGBA"))
        durs.append(int(f.info.get("duration") or im.info.get("duration") or 100))
    if len(frames) > max_frames:                       # keep the length, drop frames evenly
        step = len(frames) / max_frames
        idx = [int(i * step) for i in range(max_frames)]
        frames = [frames[i] for i in idx]
        durs = [int(sum(durs) / max_frames)] * max_frames
    return frames, durs


def to_1bit(img, fit="contain", dither="floyd", threshold=128, invert=False, contrast=1.0, size=(W, H)):
    """One RGBA frame -> '1' image of this size, 128x64 unless told otherwise (black = ink)."""
    w, h = size
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    g = Image.alpha_composite(bg, img).convert("L")
    if contrast != 1.0:
        g = Image.eval(g, lambda v: max(0, min(255, int((v - 128) * contrast + 128))))
    if fit == "cover":
        g = ImageOps.fit(g, (w, h), Image.LANCZOS)
    else:
        g.thumbnail((w, h), Image.LANCZOS)
        canvas = Image.new("L", (w, h), 255)
        canvas.paste(g, ((w - g.width) // 2, (h - g.height) // 2))
        g = canvas
    if invert:
        g = ImageOps.invert(g)
    if dither == "floyd":
        g = g.point(lambda v: max(0, min(255, v + (128 - threshold))))
        return g.convert("1")                          # Pillow's default is Floyd-Steinberg
    if dither == "bayer":
        px, out = g.load(), Image.new("1", (w, h), 1)
        o = out.load()
        for y in range(h):
            for x in range(w):
                t = (BAYER4[y % 4][x % 4] + 0.5) * 16 + (threshold - 128)
                o[x, y] = 0 if px[x, y] < t else 1
        return out
    return g.point(lambda v: 255 if v >= threshold else 0).convert("1")


def bm_bytes(img1):
    """'1' image (0 = black) -> .bm file bytes (uncompressed)."""
    w, h = img1.size
    px = img1.load()
    rowbytes = (w + 7) // 8
    out = bytearray(b"\x00")
    for y in range(h):
        for bx in range(rowbytes):
            v = 0
            for bit in range(8):
                x = bx * 8 + bit
                if x < w and px[x, y] == 0:
                    v |= 1 << bit
            out.append(v)
    return bytes(out)


def bm_packed(img1):
    """'1' image -> .bm file bytes, compressed when that is smaller (same choice as Flipper's packer)."""
    raw = bm_bytes(img1)[1:]
    enc = heatshrink2.compress(raw, window_sz2=8, lookahead_sz2=4)
    enc = bytes([len(enc) & 0xFF, len(enc) >> 8]) + enc
    return b"\x01\x00" + enc if len(enc) + 2 < len(raw) + 1 else b"\x00" + raw


def bits(img1):
    """'1' image -> list of 0/1 rows packed as strings, for the in-app preview."""
    px = img1.load()
    return ["".join("1" if px[x, y] == 0 else "0" for x in range(W)) for y in range(H)]


def meta_text(n_frames, fps):
    order = " ".join(str(i) for i in range(n_frames))
    return (f"Filetype: Flipper Animation\nVersion: 1\n\nWidth: {W}\nHeight: {H}\nPassive frames: {n_frames}\n"
            f"Active frames: 0\nFrames order: {order}\nActive cycles: 0\nFrame rate: {fps}\nDuration: 3600\n"
            f"Active cooldown: 0\n\nBubble slots: 0\n")


def manifest_entry(name, weight=3):
    return (f"Name: {name}\nMin butthurt: 0\nMax butthurt: 14\nMin level: 1\nMax level: 30\nWeight: {weight}\n")


def add_to_manifest(existing, name, weight=3):
    """Manifest text with this animation added (or its entry replaced). Keeps everything else as is."""
    text = existing or "Filetype: Flipper Animation Manifest\nVersion: 1\n"
    blocks = re.split(r"\n\s*\n", text.strip("\n"))
    head, entries = blocks[0], [b for b in blocks[1:] if not re.match(rf"Name:\s*{re.escape(name)}\s*$", b.splitlines()[0])]
    entries.append(manifest_entry(name, weight).strip("\n"))
    return head + "\n\n" + "\n\n".join(entries) + "\n"


def remove_from_manifest(existing, name):
    blocks = re.split(r"\n\s*\n", (existing or "").strip("\n"))
    if not blocks or not blocks[0]:
        return existing
    keep = [blocks[0]] + [b for b in blocks[1:] if not re.match(rf"Name:\s*{re.escape(name)}\s*$", b.splitlines()[0])]
    return "\n\n".join(keep) + "\n"


def build(data, opts):
    """Image bytes + options -> {"frames": [1-bit images], "fps", "bits": preview rows per frame}."""
    frames, durs = load_frames(data, int(opts.get("max_frames") or MAX_FRAMES))
    ones = [to_1bit(f, opts.get("fit", "contain"), opts.get("dither", "floyd"), int(opts.get("threshold", 128)),
                    bool(opts.get("invert")), float(opts.get("contrast", 1.0))) for f in frames]
    avg = sum(durs) / max(1, len(durs))
    fps = int(opts.get("fps") or max(1, min(10, round(1000 / max(1, avg)))))
    return {"frames": ones, "fps": fps}


def write_folder(result, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(result["frames"]):
        (dest / f"frame_{i}.bm").write_bytes(bm_packed(f))
    (dest / "meta.txt").write_text(meta_text(len(result["frames"]), result["fps"]), encoding="utf-8", newline="\n")
    return dest
