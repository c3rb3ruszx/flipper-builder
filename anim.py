"""GIFs and images -> Flipper desktop animations (the firmware's own format).

An animation is a folder with meta.txt and frame_N.bm files. A .bm frame is a 0x00 byte, then the
1-bit picture as XBM rows (LSB = leftmost pixel, 1 = black), or, when that comes out smaller,
01 00 <length, 2 bytes LE> and the rows heatshrink-compressed (window 8, lookahead 4), exactly what
Flipper's asset packer writes. The firmware keeps every frame in RAM at its file size while an
animation plays, so compressing them is what keeps long animations from running it out of memory.
"""

import bisect
import io
import math
import os
import re
import subprocess
from pathlib import Path

import heatshrink2
from PIL import Image, ImageOps, ImageSequence

W, H = 128, 64
MAX_FRAMES = 60
HARD_MAX_FRAMES = 255              # the firmware counts frames in one byte
MAX_FPS = 30
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi"}
VIDEO_WIDTH = 384                  # decode size: plenty for a 128x64 screen, even zoomed in
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


def to_1bit(img, fit="contain", dither="floyd", threshold=128, invert=False, contrast=1.0, size=(W, H),
            background=None, zoom=1.0, move_x=0.0, move_y=0.0):
    """One frame -> '1' image of this size, 128x64 unless told otherwise (black = ink).

    background: None leaves the picture alone. "white"/"black" fill transparent parts and empty margins
    with that colour and snap nearly-solid background to it, so dithering leaves no speckles. "flip" is
    for dark footage: treat it as black, then invert, so the background comes out empty."""
    w, h = size
    dark_fill = background in ("black", "flip")
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255) if dark_fill else (255, 255, 255, 255))
    g = Image.alpha_composite(bg, rgba).convert("L")
    if contrast != 1.0:
        g = Image.eval(g, lambda v: max(0, min(255, int((v - 128) * contrast + 128))))
    if zoom > 1.0:                                     # framing: a smaller window, moved around the picture
        cw, ch = g.width / zoom, g.height / zoom
        left, top = (g.width - cw) * (move_x + 1) / 2, (g.height - ch) * (move_y + 1) / 2
        g = g.crop((round(left), round(top), round(left + cw), round(top + ch)))
    if fit == "cover":
        g = ImageOps.fit(g, (w, h), Image.LANCZOS, centering=((move_x + 1) / 2, (move_y + 1) / 2))
    elif fit == "stretch":
        g = g.resize((w, h), Image.LANCZOS)
    else:
        g.thumbnail((w, h), Image.LANCZOS)
        canvas = Image.new("L", (w, h), 0 if dark_fill else 255)
        canvas.paste(g, ((w - g.width) // 2, (h - g.height) // 2))
        g = canvas
    flip = bool(invert) != (background == "flip")
    if flip:
        g = ImageOps.invert(g)

    def snap(pic):
        if not background:
            return pic
        if (background != "black") != bool(invert):    # the background ended up on the light side
            return pic.point(lambda v: 255 if v >= 226 else v)
        return pic.point(lambda v: 0 if v <= 29 else v)
    if dither == "floyd":
        g = snap(g.point(lambda v: max(0, min(255, v + (128 - threshold)))))
        return g.convert("1")                          # Pillow's default is Floyd-Steinberg
    g = snap(g)
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


def from_rows(rows):
    """1024 bytes of XBM rows (1 = ink) -> '1' image, for frames edited by hand."""
    return Image.frombytes("1", (W, H), bytes(b ^ 0xFF for b in rows), "raw", "1;R")


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


class AnimError(ValueError):
    pass


class Source:
    """A picked picture, GIF or video, ready to be cut into frames."""

    def __init__(self, kind, duration, fps, size, frames=None, durs=None, path=None):
        self.kind, self.duration, self.fps, self.size = kind, duration, fps, size
        self.frames, self.durs, self.path = frames, durs, path
        self._decoded = {}


def from_bytes(data):
    frames, durs = load_frames(data, 1000)
    if len(frames) == 1:
        return Source("image", 0.0, 1, frames[0].size, frames, [1000])
    total = sum(durs) / 1000
    return Source("gif", total, max(1, round(len(frames) / max(total, .001))), frames[0].size, frames, durs)


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise AnimError(f"The video converter is missing: {e}")


_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _open_video(path):
    try:
        r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, timeout=30, **_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AnimError(f"Couldn't read that video: {e}")
    text = r.stderr.decode("utf-8", "replace")
    video = re.search(r"Stream #.*Video:.*?(\d{2,5})x(\d{2,5})", text)
    if not video:
        raise AnimError("Couldn't find any video in that file.")
    d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else 60.0
    f = re.search(r"([\d.]+) (?:fps|tbr)", video.string[video.start():])
    return Source("video", duration, max(1, round(float(f[1]))) if f else 30, (int(video[1]), int(video[2])), path=Path(path))


def open_source(path):
    """A file on the PC -> Source. Raises AnimError if it can't be used."""
    path = Path(path)
    if path.suffix.lower() in VIDEO_EXT:
        return _open_video(path)
    try:
        return from_bytes(path.read_bytes())
    except (OSError, ValueError) as e:
        raise AnimError(f"Couldn't read that image: {e}")


def _decode(src, start, count, fps):
    """count frames from a video starting at start seconds, fps per second, as grayscale pictures."""
    key = (round(start, 3), count, fps)
    if key in src._decoded:
        return src._decoded[key]
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src.path), "-an",
           "-vf", f"fps={fps},scale='min({VIDEO_WIDTH},iw)':-2", "-frames:v", str(count),
           "-f", "image2pipe", "-c:v", "bmp", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_NO_WINDOW)
    frames = []
    try:
        while len(frames) < count:
            head = proc.stdout.read(6)
            if len(head) < 6:
                break
            size = int.from_bytes(head[2:6], "little")
            body = proc.stdout.read(size - 6)
            if len(body) < size - 6:
                break
            with Image.open(io.BytesIO(head + body)) as im:
                frames.append(im.convert("L"))
    finally:
        proc.stdout.close()
        try:
            err = proc.stderr.read().decode("utf-8", "replace")
            proc.stderr.close()
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            err = ""
    if not frames:
        raise AnimError("Couldn't decode that video. " + err.strip()[:160])
    src._decoded = {key: frames}                       # keep just the latest cut
    return frames


def _pick(src, opts):
    """Cut the source down to frames. Returns (pictures, fps, seconds used, was it cut short)."""
    limit = max(1, min(HARD_MAX_FRAMES, int(opts.get("max_frames") or MAX_FRAMES)))
    if src.kind == "image":
        return src.frames[:1], int(opts.get("fps") or 1), 0.0, False
    start = max(0.0, min(float(opts.get("start") or 0), src.duration))
    end = float(opts.get("end") or src.duration)
    end = max(start + .05, min(end, src.duration))
    span = end - start
    want = int(opts.get("fps") or 0)
    if src.kind == "gif" and not want:                 # keep the GIF's own frames when they all fit
        times = [0.0]
        for d in src.durs:
            times.append(times[-1] + d / 1000)
        picked = [f for f, t in zip(src.frames, times) if start - 1e-6 <= t < end]
        if picked and len(picked) <= limit:
            return picked, max(1, min(MAX_FPS, round(len(picked) / span))), span, False
    fps = max(1, min(MAX_FPS, want or min(src.fps, int(limit / span) or 1)))
    count = max(1, min(limit, math.ceil(span * fps - 1e-6)))
    cut = span * fps > limit + .5
    if src.kind == "video":
        return _decode(src, start, count, fps), fps, count / fps, cut
    times, acc = [], 0.0
    for d in src.durs:
        times.append(acc)
        acc += d / 1000
    idx = [max(0, bisect.bisect_right(times, start + k / fps) - 1) for k in range(count)]
    return [src.frames[i] for i in idx], fps, count / fps, cut


def _is_dark(frames):
    """Is the footage mostly dark around its edges? Decided once for the clip, so frames don't flash."""
    sample = frames[:: max(1, len(frames) // 12)]
    vals = []
    for f in sample:
        g = Image.alpha_composite(Image.new("RGBA", f.size, (255, 255, 255, 255)), f.convert("RGBA")).convert("L")
        g = g.resize((32, 16))
        px = g.load()
        edge = [px[x, y] for x in range(32) for y in (0, 15)] + [px[x, y] for y in range(16) for x in (0, 31)]
        vals.append(sum(edge) / len(edge))
    return sorted(vals)[len(vals) // 2] < 96


def build(src, opts):
    """Source (or image bytes) + options -> {"frames": [1-bit images], "fps", "used", "cut", "flipped"}."""
    if isinstance(src, (bytes, bytearray)):
        src = from_bytes(bytes(src))
    opts = opts or {}
    frames, fps, used, cut = _pick(src, opts)
    background = opts.get("background") or None
    flipped = False
    if background == "auto":
        flipped = _is_dark(frames)
        background = "flip" if flipped else "white"
    ones = [to_1bit(f, opts.get("fit", "contain"), opts.get("dither", "floyd"), int(opts.get("threshold", 128)),
                    bool(opts.get("invert")), float(opts.get("contrast", 1.0)), (W, H), background,
                    max(1.0, float(opts.get("zoom", 1.0))), float(opts.get("move_x", 0)), float(opts.get("move_y", 0)))
            for f in frames]
    return {"frames": ones, "fps": fps, "used": used, "cut": cut, "flipped": flipped}


def write_folder(result, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(result["frames"]):
        (dest / f"frame_{i}.bm").write_bytes(bm_packed(f))
    (dest / "meta.txt").write_text(meta_text(len(result["frames"]), result["fps"]), encoding="utf-8", newline="\n")
    return dest
