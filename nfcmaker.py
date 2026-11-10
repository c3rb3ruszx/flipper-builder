"""NFC tags from scratch: NDEF records -> a Flipper .nfc file of an NTAG213/215/216 the Flipper can emulate.

Same layout the firmware writes for a blank NTAG (lib/nfc/helpers/nfc_data_generator.c) with the NDEF
message from page 4, like Momentum's NFC Maker (GPL-3.0): UID 04 xx.., BCC bytes, capability container
E1 10 <size> 00, config pages (STRG_MOD_EN, AUTH0 FF, VCTID 05, PWD FF..), file format version 4.
Record payloads follow the NFC Forum RTDs (URI, Text), the Wi-Fi Alliance WSC credential, vCard 3.0,
Android Application Records and the Bluetooth OOB pairing record.
"""

import os
import re

# ---------------------------------------------------------------- tag types

TAGS = {  # name: (pages total, CC data-area byte (x8 = bytes for NDEF), version storage byte)
    "NTAG213": (45, 0x12, 0x0F),
    "NTAG215": (135, 0x3E, 0x11),
    "NTAG216": (231, 0x6D, 0x13),
}

URI_PREFIXES = [  # NFC Forum URI RTD abbreviation codes, longest first so the best match wins
    (0x02, "https://www."), (0x01, "http://www."), (0x04, "https://"), (0x03, "http://"),
    (0x05, "tel:"), (0x06, "mailto:"), (0x0D, "ftp://"), (0x1D, "file://"),
]

WIFI_AUTH = {"open": 0x01, "wpa-personal": 0x02, "shared": 0x04, "wpa-enterprise": 0x08,
             "wpa2-enterprise": 0x10, "wpa2-personal": 0x20}
WIFI_ENC = {"none": 0x01, "wep": 0x02, "tkip": 0x04, "aes": 0x08}


class NfcError(ValueError):
    pass


# ---------------------------------------------------------------- records

def _rec(tnf, rtype, payload):
    return {"tnf": tnf, "type": rtype.encode("ascii"), "payload": payload}


def uri_record(uri):
    uri = (uri or "").strip()
    if not uri:
        raise NfcError("Enter a link.")
    for code, prefix in URI_PREFIXES:
        if uri.lower().startswith(prefix):
            return _rec(0x01, "U", bytes([code]) + uri[len(prefix):].encode("utf-8"))
    return _rec(0x01, "U", b"\x00" + uri.encode("utf-8"))


def text_record(text, lang="en"):
    if not text:
        raise NfcError("Enter some text.")
    lang = (lang or "en").strip()[:8] or "en"
    return _rec(0x01, "T", bytes([len(lang)]) + lang.encode("ascii", "ignore") + text.encode("utf-8"))


def _tlv16(tag, value):
    return tag.to_bytes(2, "big") + len(value).to_bytes(2, "big") + value


def wifi_record(ssid, password="", auth="wpa2-personal", enc="aes"):
    ssid = ssid or ""
    if not ssid:
        raise NfcError("Enter the Wi-Fi network name.")
    if auth == "open":
        enc, password = "none", ""
    elif not password:
        raise NfcError("Enter the Wi-Fi password (or pick Open).")
    cred = (_tlv16(0x1026, b"\x01")                               # network index
            + _tlv16(0x1045, ssid.encode("utf-8"))                # SSID
            + _tlv16(0x1003, WIFI_AUTH[auth].to_bytes(2, "big"))  # authentication type
            + _tlv16(0x100F, WIFI_ENC[enc].to_bytes(2, "big"))    # encryption type
            + _tlv16(0x1027, password.encode("utf-8"))            # network key
            + _tlv16(0x1020, b"\xff" * 6))                        # MAC: any
    return _rec(0x02, "application/vnd.wfa.wsc", _tlv16(0x100E, cred))


def _vesc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def vcard_record(first="", last="", phone="", email="", url="", org=""):
    if not (first or last):
        raise NfcError("Enter a name for the contact.")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"N:{_vesc(last)};{_vesc(first)};;;",
             f"FN:{_vesc((first + ' ' + last).strip())}"]
    if org:
        lines.append(f"ORG:{_vesc(org)}")
    if phone:
        lines.append(f"TEL:{phone.strip()}")
    if email:
        lines.append(f"EMAIL:{email.strip()}")
    if url:
        lines.append(f"URL:{url.strip()}")
    lines.append("END:VCARD")
    return _rec(0x02, "text/vcard", ("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _q(s):
    from urllib.parse import quote
    return quote(s or "", safe="")


def phone_record(number):
    number = re.sub(r"[^\d+*#]", "", number or "")
    if not number:
        raise NfcError("Enter a phone number.")
    return uri_record("tel:" + number)


def sms_record(number, body=""):
    number = re.sub(r"[^\d+*#]", "", number or "")
    if not number:
        raise NfcError("Enter a phone number.")
    return uri_record(f"sms:{number}" + (f"?body={_q(body)}" if body else ""))


def email_record(to, subject="", body=""):
    if not to or "@" not in to:
        raise NfcError("Enter an email address.")
    q = "&".join(x for x in (f"subject={_q(subject)}" if subject else "", f"body={_q(body)}" if body else "") if x)
    return uri_record("mailto:" + to.strip() + (("?" + q) if q else ""))


def geo_record(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise NfcError("Enter a latitude and longitude, like 33.8938 and 35.5018.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise NfcError("Latitude goes from -90 to 90, longitude from -180 to 180.")
    return uri_record(f"geo:{lat:.6f},{lon:.6f}")


def app_record(package):
    """Android Application Record: opens (or offers to install) an Android app."""
    package = (package or "").strip()
    if not re.fullmatch(r"[A-Za-z][\w]*(\.[A-Za-z][\w]*)+", package):
        raise NfcError("Enter an Android package name, like com.spotify.music.")
    return _rec(0x04, "android.com:pkg", package.encode("ascii"))


def bluetooth_record(mac):
    """Bluetooth pairing (OOB): a phone that taps it offers to pair with that device."""
    hexs = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(hexs) != 12:
        raise NfcError("Enter the Bluetooth address, like 00:11:22:AA:BB:CC.")
    addr = bytes.fromhex(hexs)[::-1]                              # little-endian on the wire
    return _rec(0x02, "application/vnd.bluetooth.ep.oob", (len(addr) + 2).to_bytes(2, "little") + addr)


BUILDERS = {
    "url": lambda f: uri_record(f.get("url")),
    "text": lambda f: text_record(f.get("text"), f.get("lang", "en")),
    "wifi": lambda f: wifi_record(f.get("ssid"), f.get("password", ""), f.get("auth", "wpa2-personal"),
                                  f.get("enc", "aes")),
    "contact": lambda f: vcard_record(f.get("first", ""), f.get("last", ""), f.get("phone", ""),
                                      f.get("email", ""), f.get("url", ""), f.get("org", "")),
    "phone": lambda f: phone_record(f.get("number")),
    "sms": lambda f: sms_record(f.get("number"), f.get("body", "")),
    "email": lambda f: email_record(f.get("to"), f.get("subject", ""), f.get("body", "")),
    "geo": lambda f: geo_record(f.get("lat"), f.get("lon")),
    "app": lambda f: app_record(f.get("package")),
    "bluetooth": lambda f: bluetooth_record(f.get("mac")),
}


# ---------------------------------------------------------------- NDEF message + tag memory

def ndef_message(records):
    if not records:
        raise NfcError("Add at least one record.")
    out = bytearray()
    for i, r in enumerate(records):
        flags = r["tnf"] & 0x07
        if i == 0:
            flags |= 0x80                                         # MB
        if i == len(records) - 1:
            flags |= 0x40                                         # ME
        short = len(r["payload"]) < 256
        if short:
            flags |= 0x10                                         # SR
        out.append(flags)
        out.append(len(r["type"]))
        out += bytes([len(r["payload"])]) if short else len(r["payload"]).to_bytes(4, "big")
        out += r["type"] + r["payload"]
    return bytes(out)


def ndef_tlv(message):
    n = len(message)
    head = bytes([0x03, n]) if n < 0xFF else bytes([0x03, 0xFF]) + n.to_bytes(2, "big")
    return head + message + b"\xFE"


def capacity(tag):
    return TAGS[tag][1] * 8


def random_uid():
    uid = bytearray(os.urandom(7))
    uid[0] = 0x04                                                 # NXP
    uid[3] |= 0x01                                                # never the cascade tag 0x88
    uid[6] = 0x80 | (uid[6] & 0x0F)
    return bytes(uid)


def _hex(b):
    return " ".join(f"{x:02X}" for x in b)


def build_nfc(records, tag="NTAG215", uid=None):
    """-> (file text, info dict). Raises NfcError when it doesn't fit."""
    if tag not in TAGS:
        raise NfcError("Pick NTAG213, NTAG215 or NTAG216.")
    total, cc_size, storage = TAGS[tag]
    tlv = ndef_tlv(ndef_message(records))
    cap = capacity(tag)
    if len(tlv) > cap:
        bigger = [t for t in TAGS if capacity(t) >= len(tlv)]
        raise NfcError(f"That needs {len(tlv)} bytes but an {tag} holds {cap}."
                       + (f" Pick {bigger[0]}." if bigger else " Shorten it."))
    uid = uid or random_uid()
    pages = [bytearray(4) for _ in range(total)]
    pages[0][:] = bytes([uid[0], uid[1], uid[2], 0x88 ^ uid[0] ^ uid[1] ^ uid[2]])
    pages[1][:] = uid[3:7]
    pages[2][:] = bytes([uid[3] ^ uid[4] ^ uid[5] ^ uid[6], 0x48, 0x00, 0x00])
    pages[3][:] = bytes([0xE1, 0x10, cc_size, 0x00])              # NDEF, v1.0, size, read/write
    data = tlv + bytes(cap - len(tlv))
    for i in range(cap // 4):
        pages[4 + i][:] = data[i * 4:i * 4 + 4]
    cfg = total - 4
    pages[cfg - 1][:] = bytes([0x00, 0x00, 0x00, 0xBD])           # dynamic lock bytes + RFUI
    pages[cfg][:] = bytes([0x04, 0x00, 0x00, 0xFF])               # CFG0: STRG_MOD_EN, AUTH0 = FF (no password)
    pages[cfg + 1][:] = bytes([0x00, 0x05, 0x00, 0x00])           # CFG1: VCTID
    pages[cfg + 2][:] = b"\xFF\xFF\xFF\xFF"                        # PWD
    pages[cfg + 3][:] = b"\x00\x00\x00\x00"                        # PACK
    lines = [
        "Filetype: Flipper NFC device",
        "Version: 4",
        "# Device type can be ISO14443-3A, ISO14443-3B, ISO14443-4A, ISO14443-4B, ISO15693-3, FeliCa, "
        "NTAG/Ultralight, Mifare Classic, Mifare Plus, Mifare DESFire, SLIX, ST25TB, NTAG4xx, Type 4 Tag",
        "Device type: NTAG/Ultralight",
        "# UID is common for all formats",
        f"UID: {_hex(uid)}",
        "# ISO14443-3A specific data",
        "ATQA: 00 44",
        "SAK: 00",
        "# NTAG/Ultralight specific data",
        "Data format version: 2",
        f"NTAG/Ultralight type: {tag}",
        f"Signature: {_hex(bytes(32))}",
        f"Mifare version: {_hex(bytes([0x00, 0x04, 0x04, 0x02, 0x01, 0x00, storage, 0x03]))}",
    ]
    for i in range(3):
        lines += [f"Counter {i}: 0", f"Tearing {i}: 00"]
    lines += [f"Pages total: {total}", f"Pages read: {total}"]
    lines += [f"Page {i}: {_hex(p)}" for i, p in enumerate(pages)]
    lines.append("Failed authentication attempts: 0")
    return "\n".join(lines) + "\n", {"tag": tag, "uid": _hex(uid), "bytes": len(tlv), "capacity": cap}


def describe(kind, f):
    """What a phone does when it taps the tag, for the preview."""
    return {
        "url": f"Opens {f.get('url', '')}",
        "text": "Shows the text",
        "wifi": f"Offers to join the Wi-Fi network \"{f.get('ssid', '')}\"",
        "contact": f"Offers to save the contact {(f.get('first', '') + ' ' + f.get('last', '')).strip()}",
        "phone": f"Offers to call {f.get('number', '')}",
        "sms": f"Starts a text message to {f.get('number', '')}",
        "email": f"Starts an email to {f.get('to', '')}",
        "geo": "Opens the location in a maps app",
        "app": f"Opens the Android app {f.get('package', '')} (or its store page)",
        "bluetooth": f"Offers to pair with the Bluetooth device {f.get('mac', '')}",
    }.get(kind, "")


def make(items, tag="NTAG215"):
    """items: [{"kind": ..., ...fields}] -> {"nfc", "tag", "uid", "bytes", "capacity", "does": [...]}"""
    records, does = [], []
    for it in items:
        kind = it.get("kind")
        if kind not in BUILDERS:
            raise NfcError(f"Unknown record type: {kind}")
        records.append(BUILDERS[kind](it))
        does.append(describe(kind, it))
    text, info = build_nfc(records, tag)
    return {"nfc": text, "does": does, **info}


def parse_ndef(tlv_bytes):
    """Decode an NDEF TLV back into records, for tests and the viewer: [(tnf, type, payload)]."""
    i = 0
    while i < len(tlv_bytes) and tlv_bytes[i] != 0x03:
        if tlv_bytes[i] == 0x00:
            i += 1
            continue
        i += 2 + tlv_bytes[i + 1]
    if i >= len(tlv_bytes):
        return []
    n = tlv_bytes[i + 1]
    j = i + 2
    if n == 0xFF:
        n = int.from_bytes(tlv_bytes[i + 2:i + 4], "big")
        j = i + 4
    msg, out, k = tlv_bytes[j:j + n], [], 0
    while k < len(msg):
        flags, tlen = msg[k], msg[k + 1]
        k += 2
        if flags & 0x10:
            plen, k = msg[k], k + 1
        else:
            plen, k = int.from_bytes(msg[k:k + 4], "big"), k + 4
        idlen = 0
        if flags & 0x08:
            idlen, k = msg[k], k + 1
        rtype = msg[k:k + tlen]
        k += tlen + idlen
        out.append((flags & 0x07, rtype.decode("ascii", "replace"), msg[k:k + plen]))
        k += plen
        if flags & 0x40:
            break
    return out
