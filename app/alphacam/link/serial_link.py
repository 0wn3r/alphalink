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
    pass


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


def open_port(path: str, **overrides):
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
            idle_timeout: float | None = 10.0, log: TransferLog | None = None) -> bytes:
    """Receive until the second '%' (Manual Ops Save, as ATMANOP stores it), or the NC's DC4 end code once data has
    come. Raw bytes are returned before any decoding. first_timeout: give up if nothing arrives at all;
    idle_timeout: give up after a pause once data flowed (the bytes so far ride on the TransferTimeout)."""
    log = log or TransferLog()
    out = bytearray()
    percents = 0
    t_last = time.monotonic()
    while True:
        _check(cancel)
        chunk = ser.read(256)
        now = time.monotonic()
        if chunk:
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
    # legacy /dev/ttyS* without hardware report "n/a"; leave them out
    ports = [(p.device, p.description or p.device) for p in lp.comports() if p.description != "n/a"]
    # end-to-end tests: only virtual ports (socat ptys) standing in for the lathe, never a real adapter (tests/e2e)
    test = [d for d in os.environ.get("ALPHALINK_TEST_PORTS", "").split(":") if d]
    if test:
        return [(d, "test port") for d in test]
    return sorted(ports, key=lambda p: (not p[0].startswith("/dev/ttyUSB"), p[0]))
