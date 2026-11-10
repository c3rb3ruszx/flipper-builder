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
    """Listen for up to `seconds`; stop 2 s after the first Flipper is heard (they advertise slowly)."""
    from bleak import BleakScanner
    found, first = {}, []

    def is_flipper(name, uuids):
        return name.startswith("Flipper") or SERIAL_SERVICE in uuids or any(u.startswith("00003082") for u in uuids)

    def seen(dev, adv):
        name = adv.local_name or dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if is_flipper(name, uuids):
            found[dev.address] = {"address": dev.address, "name": name or "Flipper", "rssi": adv.rssi}
            if not first:
                first.append(asyncio.get_running_loop().time())

    async with BleakScanner(detection_callback=seen):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while loop.time() - t0 < seconds:
            if first and loop.time() - first[0] > 2:
                break
            await asyncio.sleep(0.25)
    return sorted(found.values(), key=lambda d: -(d["rssi"] or -999))


def scan(seconds=15):
    """Flippers nearby that advertise over Bluetooth (just listening; nothing is sent)."""
    return asyncio.run(_scan(seconds))


async def pair(address, ask_pin, log=lambda msg: None):
    """Pair this PC with the Flipper inside Workbench (Windows): the Flipper shows a PIN and ask_pin()
    returns what the user typed (or None to cancel). Returns "paired", "already" or raises."""
    import sys
    if sys.platform != "win32":
        return "already"     # macOS / Linux pair on demand through the system prompt
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import (DevicePairingKinds, DevicePairingProtectionLevel,
                                                   DevicePairingResultStatus)
    dev = await BluetoothLEDevice.from_bluetooth_address_async(int(address.replace(":", ""), 16))
    if dev is None:
        raise RuntimeError("Windows can't find the Flipper. Is its Bluetooth on?")
    info = dev.device_information
    if info.pairing.is_paired:
        return "already"
    custom = info.pairing.custom

    def handler(_sender, args):
        if args.pairing_kind == DevicePairingKinds.PROVIDE_PIN:
            deferral = args.get_deferral()

            def answer():                # wait for the user off the WinRT thread
                try:
                    pin = ask_pin()
                    if pin:
                        args.accept(str(pin).strip())
                finally:
                    deferral.complete()
            threading.Thread(target=answer, daemon=True).start()
        else:                            # confirm-only / numbers shown on both: accept
            args.accept()

    token = custom.add_pairing_requested(handler)
    try:
        log("Pairing: your Flipper shows a PIN")
        kinds = DevicePairingKinds.PROVIDE_PIN | DevicePairingKinds.CONFIRM_ONLY | DevicePairingKinds.CONFIRM_PIN_MATCH
        result = await custom.pair_with_protection_level_async(kinds, DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION)
    finally:
        custom.remove_pairing_requested(token)
    st = result.status
    if st in (DevicePairingResultStatus.PAIRED, DevicePairingResultStatus.ALREADY_PAIRED):
        return "paired"
    names = {DevicePairingResultStatus.AUTHENTICATION_FAILURE: "the PIN didn't match",
             DevicePairingResultStatus.AUTHENTICATION_TIMEOUT: "it timed out waiting for the PIN",
             DevicePairingResultStatus.PAIRING_CANCELED: "pairing was cancelled",
             DevicePairingResultStatus.REJECTED_BY_HANDLER: "pairing was cancelled"}
    raise RuntimeError(f"Pairing failed: {names.get(st, st)}. Try again.")


class BleMirror:
    """Same surface as device.Mirror: start(), stop(), press(), .running."""

    def __init__(self, address, on_frame, on_state=lambda state, detail="": None, ask_pin=None):
        self.address, self.on_frame, self.on_state = address, on_frame, on_state
        self.ask_pin = ask_pin
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
        if self._loop and self._stop_evt and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop_evt.set)
            except RuntimeError:     # the connection already ended
                pass

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
        from bleak import BleakClient, BleakScanner
        self._loop = asyncio.get_running_loop()
        self._stop_evt = asyncio.Event()
        self.on_state("connecting", "Looking for your Flipper over Bluetooth...")
        try:
            # connect to a fresh scan result: on Windows, connecting by address alone often can't find it
            target = await BleakScanner.find_device_by_filter(
                lambda d, adv: d.address.upper() == self.address.upper()
                or any(u.lower().startswith("00003082") for u in (adv.service_uuids or [])), timeout=6)
            if target is None:
                # a paired Flipper that Windows has already connected stops advertising: connect by address
                target = self.address
            if self.ask_pin:                     # first time: pair in-app (PIN from the Flipper's screen)
                addr = target if isinstance(target, str) else target.address
                r = await pair(addr, self.ask_pin, lambda m: self.on_state("connecting", m))
                if r == "paired":
                    self.on_state("connecting", "Paired. Connecting...")
            self.on_state("connecting", "Connecting over Bluetooth...")
            async with BleakClient(target, timeout=30) as client:
                self._client = client
                await client.start_notify(CHAR_TX, self._on_data)
                self.running = True
                self._send(20)                    # start screen stream
                self.on_state("live", "Bluetooth")
                await self._stop_evt.wait()
                self._send(21)                    # stop screen stream
                await asyncio.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "not found" in msg.lower():
                msg = "Can't reach your Flipper. Is its Bluetooth on, and not connected to your phone?"
            self.on_state("error", f"Bluetooth: {msg}")
        finally:
            self.running = False
            self._client = None
