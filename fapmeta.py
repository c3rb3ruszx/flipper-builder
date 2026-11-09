"""Read the manifest every .fap carries (ELF section .fapmeta): name, version, API, target, icon.

Layout (lib/flipper_application/application_manifest.h in the firmware, packed little-endian):
    u32 magic 'HDGR' (0x52474448), u32 manifest_version, u16 api_minor, u16 api_major,
    u16 hardware_target, u16 stack_size, u32 app_version (major<<16 | minor),
    char name[32], u8 has_icon, u8 icon[32]
The icon is a 10x10 1-bit image in Flipper's asset format: 0x00 + raw XBM rows (LSB first, 2 bytes
per row), or 0x01 0x00 + u16 length + heatshrink data (window 8, lookahead 4).
"""

import base64
import struct

MAGIC = b"HDGR"
ICON_W = ICON_H = 10


def _heatshrink_decode(data, window=8, lookahead=4, out_len=None):
    out = bytearray()
    pos, bit = 0, 0

    def read(n):
        nonlocal pos, bit
        v = 0
        for _ in range(n):
            if pos >= len(data):
                return None
            v = (v << 1) | ((data[pos] >> (7 - bit)) & 1)
            bit += 1
            if bit == 8:
                bit, pos = 0, pos + 1
        return v

    while out_len is None or len(out) < out_len:
        tag = read(1)
        if tag is None:
            break
        if tag:
            b = read(8)
            if b is None:
                break
            out.append(b)
        else:
            idx, cnt = read(window), read(lookahead)
            if idx is None or cnt is None:
                break
            idx, cnt = idx + 1, cnt + 1
            for _ in range(cnt):
                out.append(out[-idx] if idx <= len(out) else 0)
    return bytes(out[:out_len] if out_len else out)


def decode_icon(icon):
    """Raw 10x10 XBM bytes (20 bytes) from the manifest's icon field, or None."""
    row = (ICON_W + 7) // 8
    size = row * ICON_H
    if not icon:
        return None
    if icon[0] == 0x00:
        return bytes(icon[1:1 + size])
    if icon[0] == 0x01:
        n = struct.unpack_from("<H", icon, 2)[0]
        return _heatshrink_decode(icon[4:4 + n], out_len=size)
    return None


def parse(data):
    """Manifest fields from a .fap's bytes, or None if it has no manifest."""
    i = data.find(MAGIC)
    if i < 0 or len(data) < i + 85:
        return None
    _, ver, api_minor, api_major, hw, stack, appver = struct.unpack_from("<IIHHHHI", data, i)
    name = data[i + 20:i + 52].split(b"\0")[0].decode("utf-8", "replace")
    has_icon = data[i + 52]
    icon = decode_icon(data[i + 53:i + 85]) if has_icon else None
    return {
        "name": name,
        "version": f"{appver >> 16}.{appver & 0xFFFF}",
        "api": f"{api_major}.{api_minor}",
        "target": hw,
        "icon": base64.b64encode(icon).decode() if icon else None,   # 10x10 XBM, LSB first
    }


def icon_ascii(b64):
    raw = base64.b64decode(b64)
    rows = []
    for y in range(ICON_H):
        r = ""
        for x in range(ICON_W):
            byte = raw[y * 2 + x // 8]
            r += "#" if (byte >> (x % 8)) & 1 else "."
        rows.append(r)
    return "\n".join(rows)
