"""EXPERIMENTAL: the Flipper over Bluetooth LE. Screen mirror + buttons, wireless.

The Flipper's BLE serial service carries the same protobuf RPC stream as USB after
`start_rpc_session`, so frames and input events use the same messages as device.Mirror.
The first connection needs pairing: the Flipper shows a PIN and Windows asks for it.
"""

import asyncio
import threading

import device

SERIAL_SERVICE = "8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000"
CHAR_TX = "19ed82ae-ed21-4c9d-4145-228e61fe0000"      # Flipper -> PC (indicate/notify)
CHAR_RX = "19ed82ae-ed21-4c9d-4145-228e62fe0000"      # PC -> Flipper (write)
CHAR_FLOW = "19ed82ae-ed21-4c9d-4145-228e63fe0000"    # Flipper's free RX buffer (bytes)


async def _scan(seconds):
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    out = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if name.startswith("Flipper") or SERIAL_SERVICE in uuids or any(u.startswith("00003082") for u in uuids):
            out.append({"address": addr, "name": name or "Flipper", "rssi": adv.rssi})
    return sorted(out, key=lambda d: -(d["rssi"] or -999))


def scan(seconds=6):
    """Flippers nearby that advertise over Bluetooth (just listening; nothing is sent)."""
    return asyncio.run(_scan(seconds))


class BleMirror:
    """Same surface as device.Mirror: start(), stop(), press(), .running."""

    def __init__(self, address, on_frame, on_state=lambda state, detail="": None):
        self.address, self.on_frame, self.on_state = address, on_frame, on_state
        self.running = False
        self._loop = None
        self._client = None
        self._cmd = 0
        self._buf = bytearray()
        self._free = 0
        self._stop_evt = None

    def start(self):
        threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True).start()

    def stop(self):
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)

    def pause(self):       # USB jobs don't share the BLE link
        pass

    def resume(self):
        pass

    def press(self, key, kind="short"):
        seq = {"short": ["press", "short", "release"], "long": ["press", "long", "release"]}.get(kind, [kind])
        for t in seq:
            self._send(23, device._field_varint(1, device.KEYS[key]) + device._field_varint(2, device.TYPES[t]))

    # -- internals
    def _send(self, field, payload=b""):
        if not (self._loop and self._client and self.running):
            return
        self._cmd += 1
        data = device._main(self._cmd, field, payload)
        asyncio.run_coroutine_threadsafe(self._write(data), self._loop)

    async def _write(self, data):
        for i in range(0, len(data), 180):
            await self._client.write_gatt_char(CHAR_RX, data[i:i + 180], response=True)

    def _on_data(self, _char, data):
        self._buf += data
        while True:
            ln, n, i = 0, 0, 0
            while i < len(self._buf):
                b = self._buf[i]
                i += 1
                ln |= (b & 0x7F) << n
                n += 7
                if not b & 0x80:
                    break
            else:
                return
            if len(self._buf) < i + ln:
                return
            msg, self._buf = bytes(self._buf[i:i + ln]), self._buf[i + ln:]
            try:
                fields = device._parse(msg)
            except Exception:  # noqa: BLE001
                continue
            if 22 in fields:
                frame = device._parse(fields[22][0])
                data_ = frame.get(1, [b""])[0]
                if len(data_) == 1024:
                    self.on_frame(data_, frame.get(2, [0])[0])

    async def _main(self):
        from bleak import BleakClient
        self._loop = asyncio.get_running_loop()
        self._stop_evt = asyncio.Event()
        self.on_state("connecting", "Connecting over Bluetooth...")
        try:
            async with BleakClient(self.address, timeout=20) as client:
                self._client = client
                try:
                    await client.pair()          # first time: the Flipper shows a PIN, Windows asks for it
                except Exception:  # noqa: BLE001
                    pass                          # already paired, or the platform pairs on demand
                await client.start_notify(CHAR_TX, self._on_data)
                self.running = True
                self._send(20)                    # start screen stream
                self.on_state("live", "Bluetooth")
                await self._stop_evt.wait()
                self._send(21)                    # stop screen stream
                await asyncio.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            self.on_state("error", f"Bluetooth: {e}")
        finally:
            self.running = False
            self._client = None
