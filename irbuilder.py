"""Build your own IR remote: buttons from the IR database, from .ir files, learned from a real remote
or typed in, written out as one Flipper .ir file.

A button is {"name", "type": "parsed", "protocol", "address", "command"} (address and command as the
file stores them: 4 little-endian hex bytes, "07 00 00 00") or {"name", "type": "raw", "frequency",
"duty_cycle", "data"} (data = space-separated microseconds).
"""

import re

from capture import IR_PROTOCOLS, _hex_le

MAX_RAW = 1024
NAME_MAX = 32


class IrError(ValueError):
    pass


def le_bytes(value):
    """'0x04', '4', '04 00 00 00' -> '04 00 00 00'."""
    v = str(value or "").strip()
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2} ){3}[0-9A-Fa-f]{2}", v):
        return v.upper()
    try:
        n = int(v, 16)
    except ValueError:
        raise IrError(f"{value!r} isn't a hex number.")
    if not 0 <= n <= 0xFFFFFFFF:
        raise IrError(f"{value!r} is too big.")
    return " ".join(f"{b:02X}" for b in n.to_bytes(4, "little"))


def clean(b):
    """One button from the page -> checked button. Raises IrError."""
    name = re.sub(r"\s+", "_", str(b.get("name") or "").strip())[:NAME_MAX]
    if not name:
        raise IrError("Every button needs a name.")
    if b.get("type") == "raw":
        data = [int(x) for x in str(b.get("data") or "").split() if x.isdigit()]
        if not 0 < len(data) <= MAX_RAW:
            raise IrError(f"{name}: a raw signal needs between 1 and {MAX_RAW} timings.")
        try:
            freq = int(float(b.get("frequency") or 38000))
            duty = float(b.get("duty_cycle") or 0.33)
        except ValueError:
            raise IrError(f"{name}: frequency or duty cycle isn't a number.")
        if duty > 1:
            duty /= 100
        return {"name": name, "type": "raw", "frequency": str(freq), "duty_cycle": f"{duty:.6f}",
                "data": " ".join(map(str, data))}
    proto = str(b.get("protocol") or "")
    if proto not in IR_PROTOCOLS:
        raise IrError(f"{name}: unknown protocol {proto!r}.")
    return {"name": name, "type": "parsed", "protocol": proto, "address": le_bytes(b.get("address")),
            "command": le_bytes(b.get("command"))}


def from_fields(fields):
    """[[key, value], ...] for one button, as capture.parse returns them -> button (or None)."""
    d = {k: v for k, v in fields}
    try:
        return clean(d)
    except IrError:
        return None


def ir_text(buttons):
    out = "Filetype: IR signals file\nVersion: 1\n"
    for b in map(clean, buttons):
        out += "#\n" + "".join(f"{k}: {b[k]}\n" for k in
                               (("name", "type", "protocol", "address", "command") if b["type"] == "parsed"
                                else ("name", "type", "frequency", "duty_cycle", "data")))
    return out


def tx(b):
    """The CLI line that fires this button (same shape capture.parse builds)."""
    b = clean(b)
    if b["type"] == "parsed":
        return f"ir tx {b['protocol']} {_hex_le(b['address'])} {_hex_le(b['command'])}"
    data = b["data"].split()
    if len(data) > 512:
        raise IrError("That raw signal is too long to test from here.")
    return f"ir tx RAW F:{b['frequency']} DC:{round(float(b['duty_cycle']) * 100)} {' '.join(data)}"


DECODED = re.compile(r"^(\w+), A:0x([0-9A-Fa-f]+), C:0x([0-9A-Fa-f]+)( R)?$")
RAW_HEAD = re.compile(r"^RAW, (\d+) samples:$")


class RxParser:
    """Feeds on `ir rx` output; returns finished signals as they arrive (key repeats are skipped)."""

    def __init__(self):
        self.buf, self.want_raw = "", None

    def feed(self, text):
        self.buf += text.replace("\r", "")
        found = []
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.strip()
            if self.want_raw is not None:
                nums = [x for x in line.split() if x.isdigit()]
                if nums:
                    found.append({"name": "", "type": "raw", "frequency": "38000", "duty_cycle": "0.330000",
                                  "data": " ".join(nums[:MAX_RAW])})
                self.want_raw = None
                continue
            m = DECODED.match(line)
            if m and not m.group(4) and m.group(1) in IR_PROTOCOLS:
                found.append({"name": "", "type": "parsed", "protocol": m.group(1),
                              "address": le_bytes("0x" + m.group(2)), "command": le_bytes("0x" + m.group(3))})
            elif RAW_HEAD.match(line):
                self.want_raw = int(RAW_HEAD.match(line).group(1))
        return found
