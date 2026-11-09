"""Ringtones (RTTTL) and MIDI files -> Flipper Music Format (.fmf) for the Music Player app.

FMF notes are `[duration]NOTE[#][octave][.]`, e.g. `8C#5.`; `P` is a rest. Durations are 1, 2, 4, 8,
16, 32, 64 (1 = whole note); a dot adds half. MIDI is reduced to one melody line (the highest note
playing at any time, from the busiest non-drum channel), since the Flipper's buzzer plays one note.
"""

import re
import struct

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
DURS = [1, 2, 4, 8, 16, 32, 64]
MAX_NOTES = 1500


def _freq(name, octave):
    if name == "P":
        return 0
    n = NAMES.index(name) + (octave + 1) * 12          # MIDI number, A4 = 69 = 440 Hz
    return 440.0 * 2 ** ((n - 69) / 12)


def fmf_text(bpm, default_dur, default_oct, notes):
    """notes: [(dur, name, octave|None, dotted)] -> file text."""
    out = []
    for dur, name, octave, dotted in notes[:MAX_NOTES]:
        s = ("" if dur == default_dur else str(dur)) + name
        if name != "P" and octave is not None and octave != default_oct:
            s += str(octave)
        out.append(s + ("." if dotted else ""))
    return (f"Filetype: Flipper Music Format\nVersion: 0\nBPM: {int(bpm)}\nDuration: {default_dur}\n"
            f"Octave: {default_oct}\nNotes: {', '.join(out)}\n")


def preview(bpm, default_dur, default_oct, notes):
    """[(freq Hz, ms)] for playing it in the app."""
    beat_ms = 60000 / bpm                                  # a quarter note
    res = []
    for dur, name, octave, dotted in notes[:MAX_NOTES]:
        ms = beat_ms * 4 / dur * (1.5 if dotted else 1)
        res.append((round(_freq(name, default_oct if octave is None else octave), 2), round(ms)))
    return res


# ---------------------------------------------------------------- RTTTL

def from_rtttl(text):
    """'Name:d=4,o=5,b=125:8g,8a#.,p,...' -> (title, bpm, dur, octave, notes)."""
    text = " ".join(text.strip().split())
    parts = text.split(":")
    if len(parts) < 3:
        raise ValueError("That isn't an RTTTL ringtone (expected name:settings:notes).")
    title, settings, body = parts[0].strip(), parts[1], ":".join(parts[2:])
    d, o, b = 4, 6, 63
    for kv in settings.split(","):
        k, _, v = kv.strip().partition("=")
        if v.strip().isdigit():
            d, o, b = (int(v) if k.strip().lower() == "d" else d, int(v) if k.strip().lower() == "o" else o,
                       int(v) if k.strip().lower() == "b" else b)
    notes = []
    for tok in body.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)?([a-gp])(#?)(\.?)(\d)?(\.?)", tok)
        if not m:
            raise ValueError(f"Can't read the note '{tok}'.")
        dur = int(m.group(1)) if m.group(1) else d
        if dur not in DURS:
            dur = min(DURS, key=lambda x: abs(x - dur))
        name = "P" if m.group(2) == "p" else (m.group(2).upper() + m.group(3))
        if name not in NAMES and name != "P":                # E# / B# don't exist
            name = {"E#": "F", "B#": "C"}.get(name, name)
        octave = int(m.group(5)) if m.group(5) else o
        notes.append((dur, name, octave, bool(m.group(4) or m.group(6))))
    if not notes:
        raise ValueError("No notes found.")
    return title or "Ringtone", b, d, o, notes


# ---------------------------------------------------------------- MIDI

def _vlq(data, i):
    v = 0
    while True:
        c = data[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def _parse_midi(data):
    if data[:4] != b"MThd":
        raise ValueError("That isn't a MIDI file.")
    hlen = struct.unpack(">I", data[4:8])[0]
    _fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE-timed MIDI files aren't supported.")
    i, tempo, notes = 8 + hlen, None, []                   # notes: (start tick, end tick, channel, pitch)
    for _ in range(ntracks):
        if data[i:i + 4] != b"MTrk":
            break
        tlen = struct.unpack(">I", data[i + 4:i + 8])[0]
        j, end, tick, status, on = i + 8, i + 8 + tlen, 0, 0, {}
        while j < end:
            delta, j = _vlq(data, j)
            tick += delta
            b = data[j]
            if b & 0x80:
                status, j = b, j + 1
            if status == 0xFF:
                mtype, j = data[j], j + 1
                ln, j = _vlq(data, j)
                if mtype == 0x51 and tempo is None and ln == 3:
                    tempo = int.from_bytes(data[j:j + 3], "big")
                j += ln
            elif status in (0xF0, 0xF7):
                ln, j = _vlq(data, j)
                j += ln
            else:
                kind, ch = status & 0xF0, status & 0x0F
                if kind in (0xC0, 0xD0):
                    j += 1
                    continue
                a, v = data[j], data[j + 1]
                j += 2
                if kind == 0x90 and v > 0:
                    on[(ch, a)] = tick
                elif kind in (0x80, 0x90):
                    st = on.pop((ch, a), None)
                    if st is not None and tick > st:
                        notes.append((st, tick, ch, a))
        i = end
    return division, tempo or 500000, notes


def from_midi(data, title="Song"):
    division, tempo, notes = _parse_midi(data)
    notes = [n for n in notes if n[2] != 9]                # channel 10 is drums
    if not notes:
        raise ValueError("No melody found in that MIDI file.")
    counts = {}
    for n in notes:
        counts[n[2]] = counts.get(n[2], 0) + 1
    ch = max(counts, key=counts.get)
    mel = sorted(n for n in notes if n[2] == ch)
    # skyline: at each note start keep the highest pitch, cut overlaps
    line = []
    for st, en, _, p in mel:
        if line and st < line[-1][1]:
            if st == line[-1][0]:
                if p > line[-1][2]:
                    line[-1] = (st, en, p)
                continue
            line[-1] = (line[-1][0], st, line[-1][2])
        line.append((st, en, p))
    bpm = max(20, min(400, round(60_000_000 / tempo)))
    opts = sorted([(4 / d, d, False) for d in DURS] + [(6 / d, d, True) for d in DURS[1:]])

    def quant(ticks):
        beats = ticks / division
        return min(opts, key=lambda o: abs(o[0] - beats))

    out, cursor = [], line[0][0]
    for st, en, p in line:
        if st - cursor > division / 16:
            _, d, dot = quant(st - cursor)
            out.append((d, "P", None, dot))
        _, d, dot = quant(en - st)
        out.append((d, NAMES[p % 12], max(1, min(8, p // 12 - 1)), dot))
        cursor = en
    octs = [o for _, n, o, _ in out if n != "P"]
    default_oct = max(set(octs), key=octs.count)
    durs = [d for d, _, _, _ in out]
    default_dur = max(set(durs), key=durs.count)
    return title, bpm, default_dur, default_oct, out


def convert(name, data):
    """File name + bytes (MIDI) or text (RTTTL) -> {"title", "fmf", "preview", "notes"}."""
    if name.lower().endswith((".mid", ".midi")) or data[:4] == b"MThd":
        title, bpm, d, o, notes = from_midi(data, re.sub(r"\.midi?$", "", name, flags=re.I))
    else:
        title, bpm, d, o, notes = from_rtttl(data.decode("utf-8", "replace") if isinstance(data, bytes) else data)
    return {"title": title, "bpm": bpm, "fmf": fmf_text(bpm, d, o, notes), "preview": preview(bpm, d, o, notes),
            "notes": min(len(notes), MAX_NOTES), "truncated": len(notes) > MAX_NOTES}
