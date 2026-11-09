"""Reads Flipper capture files (.sub, .ir, .nfc, .rfid, .ibtn) into something people can read.

All of them use Flipper's key/value text format: "Key: value" lines, "#" comments, and for .ir a
"#" line between signals. Unknown files come back as plain text.
"""

import re

PRESETS = {
    "FuriHalSubGhzPresetOok270Async": "AM270 (OOK, 270 kHz)",
    "FuriHalSubGhzPresetOok650Async": "AM650 (OOK, 650 kHz)",
    "FuriHalSubGhzPreset2FSKDev238Async": "FM238 (2-FSK, 2.38 kHz)",
    "FuriHalSubGhzPreset2FSKDev476Async": "FM476 (2-FSK, 47.6 kHz)",
    "FuriHalSubGhzPresetMSK99_97KbAsync": "MSK (99.97 kb/s)",
    "FuriHalSubGhzPresetGFSK9_99KbAsync": "GFSK (9.99 kb/s)",
    "FuriHalSubGhzPresetCustom": "Custom",
}
IR_PROTOCOLS = {"NEC", "NECext", "NEC42", "NEC42ext", "Samsung32", "RC6", "RC5", "RC5X", "SIRC", "SIRC15",
                "SIRC20", "Kaseikyo", "RCA", "Pioneer"}
TEXT_EXT = {".txt", ".js", ".fmf", ".log", ".md", ".csv", ".json", ".ini", ".cfg", ".conf", ".sub", ".ir",
            ".nfc", ".rfid", ".ibtn", ".u2f", ".fff", ".keys", ".dict", ".ducky", ".bad", ".xml", ".html", ".yml"}
MAX_VIEW = 512 * 1024


def kind_of(name):
    n = name.lower()
    for ext, kind in ((".sub", "subghz"), (".ir", "ir"), (".nfc", "nfc"), (".rfid", "rfid"), (".ibtn", "ibutton")):
        if n.endswith(ext):
            return kind
    if n.endswith(".js"):
        return "js"
    if n.endswith(".txt") and ("badusb" in n or "/badusb/" in n):
        return "badusb"
    return "text"


def is_text(data):
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _pairs(text):
    """Every "Key: value" line in order (keys can repeat, e.g. RAW_Data)."""
    out = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        k, sep, v = line.partition(":")
        if sep:
            out.append((k.strip(), v.strip()))
    return out


def _first(pairs, *keys):
    for want in keys:
        for k, v in pairs:
            if k.lower() == want.lower():
                return v
    return None


def _hex_le(value):
    """'07 00 00 00' (little-endian bytes, as .ir files store them) -> '0x07' for the CLI."""
    try:
        b = bytes(int(x, 16) for x in value.split())
    except ValueError:
        return None
    return hex(int.from_bytes(b, "little"))


def _mhz(hz):
    try:
        return f"{int(hz) / 1e6:.2f} MHz"
    except (TypeError, ValueError):
        return hz


def parse(name, text, path=""):
    kind = kind_of(path or name)
    pairs = _pairs(text)
    res = {"kind": kind, "filetype": _first(pairs, "Filetype") or "", "fields": [], "text": text}
    f = res["fields"]

    if kind == "subghz":
        raw = [int(x) for k, v in pairs if k == "RAW_Data" for x in v.split() if re.fullmatch(r"-?\d+", x)]
        proto = _first(pairs, "Protocol") or "?"
        res["title"] = "Sub-GHz recording" if proto == "RAW" else f"Sub-GHz key: {proto}"
        f.append(["Frequency", _mhz(_first(pairs, "Frequency"))])
        preset = _first(pairs, "Preset") or ""
        f.append(["Modulation", PRESETS.get(preset, preset or "?")])
        f.append(["Protocol", proto])
        for k in ("Bit", "Key", "TE", "Repeat", "Manufacture", "Serial", "Btn", "Cnt"):
            v = _first(pairs, k)
            if v:
                f.append([{"Bit": "Bits", "TE": "TE (us)"}.get(k, k), v])
        if raw:
            total_us = sum(abs(x) for x in raw)
            f.append(["Samples", f"{len(raw):,}"])
            f.append(["Length", f"{total_us / 1e6:.2f} s"])
            # a light envelope for drawing: |level| over time, at most 1200 points
            res["wave"] = raw[:20000]
        res["note"] = "Replaying Sub-GHz signals is done on the Flipper itself (Sub-GHz app), within your local rules."

    elif kind == "ir":
        signals, cur, raw_signals = [], None, []
        for k, v in pairs:
            if k == "name":
                cur = {"name": v}
                signals.append(cur)
                raw_signals.append([[k, v]])
            elif cur is not None:
                cur[k] = v
                raw_signals[-1].append([k, v])
        res["signals"] = raw_signals          # every field in file order, so an edited remote can be written back
        buttons = []
        for s in signals:
            b = {"name": s.get("name", "?"), "type": s.get("type", "?")}
            if s.get("type") == "parsed":
                proto, addr, cmd = s.get("protocol", ""), _hex_le(s.get("address", "")), _hex_le(s.get("command", ""))
                b["detail"] = f"{proto} · address {addr} · command {cmd}"
                if proto in IR_PROTOCOLS and addr and cmd:
                    b["tx"] = f"ir tx {proto} {addr} {cmd}"
            elif s.get("type") == "raw":
                samples = s.get("data", "").split()
                freq, duty = s.get("frequency", "38000"), s.get("duty_cycle", "0.33")
                try:
                    dc = int(round(float(duty) * 100)) if float(duty) <= 1 else int(float(duty))
                except ValueError:
                    dc = 33
                b["detail"] = f"raw · {int(freq) / 1000:g} kHz · {len(samples)} samples"
                if 0 < len(samples) <= 512:
                    b["tx"] = f"ir tx RAW F:{freq} DC:{dc} {' '.join(samples)}"
            buttons.append(b)
        res["title"] = "Infrared remote"
        f.append(["Buttons", str(len(buttons))])
        res["buttons"] = buttons
        res["note"] = "Send fires the button from your Flipper's IR LED, so point it at the device."

    elif kind == "nfc":
        dev = _first(pairs, "Device type") or "?"
        res["title"] = f"NFC card: {dev}"
        for k in ("Device type", "UID", "ATQA", "SAK", "Mifare Classic type", "Data format version",
                  "NTAG/Ultralight type", "Mifare version", "Signature", "Counter 0", "Pages total", "Pages read"):
            v = _first(pairs, k)
            if v and not (k == "Device type"):
                f.append([k, v])
        f.insert(0, ["Type", dev])
        blocks = [(k, v) for k, v in pairs if re.fullmatch(r"(Block|Page) \d+", k)]
        if blocks:
            unread = sum(1 for _, v in blocks if "??" in v)
            label = "Blocks" if blocks[0][0].startswith("Block") else "Pages"
            f.append([label, f"{len(blocks)} ({len(blocks) - unread} read{', ' + str(unread) + ' missing' if unread else ''})"])
            res["blocks"] = [[k, v] for k, v in blocks[:512]]

    elif kind == "rfid":
        kt = _first(pairs, "Key type") or "?"
        res["title"] = f"RFID tag: {kt}"
        f.append(["Type", kt])
        f.append(["Data", _first(pairs, "Data") or "?"])

    elif kind == "ibutton":
        proto = _first(pairs, "Protocol", "Key type") or "?"
        res["title"] = f"iButton key: {proto}"
        f.append(["Protocol", proto])
        for k in ("Rom Data", "Data", "Sram Data", "Eeprom Data"):
            v = _first(pairs, k)
            if v:
                f.append([k, v if len(v) < 80 else v[:80] + "..."])

    else:
        res["title"] = {"js": "JavaScript app", "badusb": "BadUSB script"}.get(kind, "Text file")
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        f.append(["Lines", str(lines)])

    if res["filetype"]:
        f.append(["File type", res["filetype"]])
    return res
