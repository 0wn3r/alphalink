"""Alpha-T serial transfers (data/line_settings.json, docs/RE_NOTES.md Progress 26/29, tools/alphalink.py).

Line: 9600 baud, 7 data bits, even parity, 2 stop bits, DTR and RTS asserted. Flow control is software:
the control sends DC1 (0x11) to start or resume and DC3 (0x13; 0x93 seen from some drivers) to pause, and the
sender must stop within 10 characters of DC3 (FANUC alarm 087). We therefore write in small chunks, paced at the
line rate, with an optional per-character delay that flushes USB adapters byte by byte (2 ms works on the 400T).

Every function takes an already-open port-like object (pyserial Serial or a test double) so the logic is testable
without hardware. `cancel` is a threading.Event; `progress(done, total)` is called from the worker thread."""

from __future__ import annotations

from ..i18n import tr

import os
import time
from dataclasses import dataclass, field
from typing import Callable

XON = 0x11
XOFFS = frozenset({0x13, 0x93})
LINE = dict(baudrate=9600, bytesize=7, parity="E", stopbits=2)
BITS_PER_CHAR = 1 + 7 + 1 + 2
EVEN_PARITY = bytes(c | (0x80 if bin(c).count("1") & 1 else 0) for c in range(128)) + bytes(128)


class Cancelled(Exception):
    def __init__(self, data: bytes = b""):
        super().__init__()
        self.data = data  # a receive stopped by the user keeps what arrived


class TransferTimeout(Exception):
    def __init__(self, message: str = "", data: bytes = b""):
        super().__init__(message)
        self.data = data  # what arrived before the line went quiet: never thrown away


@dataclass
class SendOptions:
    wait_xon: bool = True  # wait for the control's DC1 before the first byte (recommended, TOUCH/RE_NOTES)
    xon_timeout: float | None = None  # seconds; None = until cancelled
    char_delay_ms: float = 2.0
    line_delay_ms: float = 0.0
    chunk: int = 4  # bytes per write when no per-character delay is used
    pace: bool = True  # sleep for the bytes' time on the wire (keeps ptys/sockets realistic)


@dataclass
class TransferLog:
    """Timestamped record of a transfer, for diagnosis (DESIGN.md 7)."""

    events: list[tuple[float, str]] = field(default_factory=list)
    t0: float = field(default_factory=time.monotonic)

    def add(self, text: str) -> None:
        self.events.append((time.monotonic() - self.t0, text))

    def text(self) -> str:
        return "".join(f"{t:8.3f}  {e}\n" for t, e in self.events)


BT_PREFIX = "bt://"  # bt://AA:BB:CC:DD:EE:FF[/channel]: a paired Bluetooth serial (SPP) adapter, straight over RFCOMM
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"


def is_bluetooth(path: str | None) -> bool:
    return bool(path) and (path.startswith(BT_PREFIX) or os.path.basename(path).startswith("rfcomm"))


class RfcommPort:
    """A Bluetooth serial adapter reached over an RFCOMM socket, with the parts of pyserial's interface the transfers
    use. The line itself (9600, 7E2) is set in the adapter: RFCOMM carries bytes, not the PC's port settings, and has
    no modem lines (reading one raises AttributeError, shown as unknown)."""

    def __init__(self, url: str, timeout: float = 0.05, connect_timeout: float = 10.0, sock=None):
        import socket

        self.port = url
        addr = url[len(BT_PREFIX):] if url.startswith(BT_PREFIX) else url
        mac, _, ch = addr.partition("/")
        self.address, self.channel = mac.upper(), int(ch or 1)  # SPP adapters listen on channel 1
        if sock is None:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(connect_timeout)
            sock.connect((self.address, self.channel))
        self.sock = sock
        self.sock.settimeout(timeout)

    def read(self, n: int = 1) -> bytes:
        import socket

        try:
            return self.sock.recv(n)
        except (socket.timeout, BlockingIOError):
            return b""

    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def flush(self) -> None:
        pass  # sendall has handed the bytes over

    def reset_input_buffer(self) -> None:
        self.sock.setblocking(False)
        try:
            while self.sock.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.settimeout(0.05)

    def close(self) -> None:
        self.sock.close()


class SoftParityPort:
    """For adapters that only frame 8 data bits: set them to 9600 8N2 and the app makes the 7E2 characters itself
    (even parity in bit 7 going out, bit 7 dropped coming in). On the wire it is the same frame as 7E2."""

    def __init__(self, inner):
        self.inner = inner
        self.port = getattr(inner, "port", None)

    def read(self, n: int = 1) -> bytes:
        return bytes(b & 0x7F for b in self.inner.read(n))

    def write(self, data: bytes) -> int:
        return self.inner.write(bytes(b & 0x7F for b in data).translate(EVEN_PARITY))

    def __getattr__(self, name):
        return getattr(self.inner, name)


def open_port(path: str, soft_parity: bool = False, **overrides):
    """A pyserial port (or, for bt:// addresses, an RFCOMM socket), with the line's 7E2 framing; soft_parity: 8-bit
    framing with the parity made here (adapters without 7-bit framing)."""
    if path and path.startswith(BT_PREFIX):
        port = RfcommPort(path)
        return SoftParityPort(port) if soft_parity else port
    if soft_parity:
        overrides = dict(overrides, bytesize=8, parity="N")
        return SoftParityPort(open_port(path, **overrides))
    import serial  # imported here so the rest of the module works without pyserial

    params = dict(LINE, **overrides)
    ser = serial.serial_for_url(path, timeout=0.05, write_timeout=5, xonxoff=False, rtscts=False,
                                do_not_open=True, **params)
    ser.open()
    try:
        ser.dtr = True
        ser.rts = True
    except (OSError, ValueError, AttributeError):
        pass  # ptys and some URLs have no modem lines
    return ser


def modem_lines(ser) -> dict[str, bool | None]:
    out = {}
    for name in ("cts", "dsr", "cd", "ri"):
        try:
            out[name] = bool(getattr(ser, name))
        except (OSError, ValueError, AttributeError):
            out[name] = None
    return out


def _check(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def wait_for_xon(ser, timeout: float | None, cancel=None, log: TransferLog | None = None) -> None:
    t0 = time.monotonic()
    while True:
        _check(cancel)
        b = ser.read(1)
        if b:
            if log:
                log.add(f"got {b.hex()}")
            if b[0] == XON:
                return
        if timeout is not None and time.monotonic() - t0 > timeout:
            raise TransferTimeout("no DC1 (XON) from the control")


def send(ser, data: bytes, opts: SendOptions | None = None, progress: Callable[[int, int], None] | None = None,
         cancel=None, log: TransferLog | None = None) -> int:
    """Send `data` unchanged (CR LF kept). Returns the number of bytes sent."""
    opts = opts or SendOptions()
    log = log or TransferLog()
    if opts.wait_xon:
        log.add("waiting for DC1")
        wait_for_xon(ser, opts.xon_timeout, cancel, log)
    log.add(f"sending {len(data)} bytes")
    per_char = opts.char_delay_ms > 0 or opts.line_delay_ms > 0
    step = 1 if per_char else max(1, opts.chunk)
    paused, i = False, 0
    while i < len(data):
        _check(cancel)
        waiting = ser.in_waiting
        if waiting:
            for b in ser.read(waiting):
                if b in XOFFS and not paused:
                    paused = True
                    log.add(f"XOFF at {i}")
                elif b == XON and paused:
                    paused = False
                    log.add(f"XON at {i}")
        if paused:
            time.sleep(0.01)
            continue
        blk = data[i:i + step]
        ser.write(blk)
        i += len(blk)
        if opts.pace:
            time.sleep(len(blk) * BITS_PER_CHAR / LINE["baudrate"])
        if per_char:
            ser.flush()
            delay = opts.char_delay_ms
            if blk in (b"\r", b"\n"):
                delay += opts.line_delay_ms
            time.sleep(delay / 1000)
        if progress:
            progress(i, len(data))
    ser.flush()
    log.add("end of transmission")
    return i


DC2, DC4 = 0x12, 0x14  # the NC's punch start and end codes (FANUC: DC2 before the data, DC4 after it)


def _tail(data: bytes, n: int = 48) -> str:
    return data[-n:].hex(" ")


def receive(ser, progress: Callable[[int], None] | None = None, cancel=None, first_timeout: float | None = None,
            idle_timeout: float | None = 10.0, log: TransferLog | None = None,
            quiet: Callable[[float], None] | None = None) -> bytes:
    """Receive until the second '%' (Manual Ops Save, as ATMANOP stores it), or the NC's DC4 end code once data has
    come. Raw bytes are returned before any decoding. first_timeout: give up if nothing arrives at all;
    idle_timeout: give up after a pause once data flowed (the bytes so far ride on the TransferTimeout); None waits
    for the end mark or the user (the control pauses between operations). quiet(seconds) is called about once a second
    while the line is quiet after data, so the user sees the pause."""
    log = log or TransferLog()
    out = bytearray()
    percents = 0
    t_last = time.monotonic()
    t_quiet = 0.0
    while True:
        if cancel is not None and cancel.is_set():
            log.add(f"stopped by the user after {len(out)} bytes, {percents} %; last bytes: {_tail(out)}")
            raise Cancelled(bytes(out))
        chunk = ser.read(256)
        now = time.monotonic()
        if chunk:
            if out and now - t_last > 2:
                log.add(f"pause of {now - t_last:.1f} s after {len(out)} bytes")
            if not out:
                log.add(f"first bytes: {chunk[:24].hex(' ')}")
            t_last = now
            for b in chunk:
                out.append(b)
                if b & 0x7F == 0x25:
                    percents += 1
                    log.add(f"% at byte {len(out)}")
                    if percents == 2:
                        log.add(f"second % after {len(out)} bytes")
                        return bytes(out)
                elif b & 0x7F == DC4 and len(out) > 1:
                    log.add(f"DC4 (end) after {len(out)} bytes, {percents} %; last bytes: {_tail(out)}")
                    return bytes(out)
            if progress:
                progress(len(out))
        elif not out and first_timeout is not None and now - t_last > first_timeout:
            raise TransferTimeout(tr("nothing received"))
        elif out and idle_timeout is not None and now - t_last > idle_timeout:
            log.add(f"idle timeout after {len(out)} bytes, {percents} %; last bytes: {_tail(out)}")
            raise TransferTimeout(tr("transfer stopped after {n} bytes (no second %)", n=len(out)), bytes(out))
        elif out and quiet and now - t_last > 2 and now - t_quiet >= 1:
            t_quiet = now
            quiet(now - t_last)


def manops_load_bytes(data: bytes) -> bytes:
    """Manual Ops Load (ATMANOP): even parity put in bit 7 of each byte, bytes above 0x7F become 0."""
    return data.translate(EVEN_PARITY)


def loopback(ser, message: bytes = b"%\r\nO1234 LOOPBACK TEST\r\n%\r\n", wait: float = 0.5) -> tuple[bool, bytes]:
    """With pins 2 and 3 bridged at the adapter, what goes out must come straight back."""
    ser.reset_input_buffer()
    ser.write(message)
    ser.flush()
    got = bytearray()
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait and len(got) < len(message):
        got += ser.read(len(message) - len(got))
    return bytes(got) == message, bytes(got)


def list_ports() -> list[tuple[str, str]]:
    """(device, description) for the serial ports present, USB adapters first."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    # legacy /dev/ttyS* without hardware report "n/a"; leave them out (rfcomm devices report "n/a" too: keep those)
    ports = [(p.device, "Bluetooth (rfcomm)" if "rfcomm" in p.device else p.description or p.device)
             for p in lp.comports() if p.description != "n/a" or "rfcomm" in p.device]
    # end-to-end tests: only virtual ports (socat ptys) standing in for the lathe, never a real adapter (tests/e2e)
    test = [d for d in os.environ.get("ALPHALINK_TEST_PORTS", "").split(":") if d]
    if test:
        return [(d, "test port") for d in test]
    return sorted(ports, key=lambda p: (not p[0].startswith("/dev/ttyUSB"), p[0])) + bluetooth_devices()


def bluetooth_devices() -> list[tuple[str, str]]:
    """Paired Bluetooth devices with a serial port profile (from BlueZ over D-Bus), as bt:// ports."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync("org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                              GLib.VariantType.new("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 2000, None)
    except Exception:  # noqa: BLE001 - no BlueZ, no system bus, sandbox without access: no Bluetooth ports
        return []
    out = []
    for _path, ifaces in reply.unpack()[0].items():
        dev = ifaces.get("org.bluez.Device1")
        if not dev or not dev.get("Paired"):
            continue
        uuids = [u.lower() for u in dev.get("UUIDs", [])]
        if uuids and SPP_UUID not in uuids:
            continue  # a device that lists nothing may still be a serial adapter
        if str(dev.get("Icon", "")).startswith(("audio", "phone", "input", "computer", "video")):
            continue  # earbuds and phones offer a serial profile too (for their apps), but are not adapters
        name = dev.get("Alias") or dev.get("Name") or dev.get("Address")
        out.append((BT_PREFIX + dev["Address"], f"{name} (Bluetooth)"))
    return sorted(out)
