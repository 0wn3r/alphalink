#!/usr/bin/env python3
"""Fake CNC: capture what ALINK (running in DOSBox-X) puts on the wire.

  python3 cnc_capture.py socket://127.0.0.1:5001 --expect ../../vendor/Alpha181/PROGRAMS/LOOPTEST.600
  python3 cnc_capture.py /tmp/alpha_cnc --stty /tmp/alpha_dos      (directserial/pty mode)

Options let you inject XOFF after N bytes to check flow control:
  --xoff-after 200 --xoff-byte 0x93 --hold 2      (0x93 should pause ALINK, 0x13 should not)
Writes capture.bin and capture.hex next to this script. Stops after --idle seconds of silence.
"""
import argparse, os, subprocess, sys, time, serial

ap = argparse.ArgumentParser()
ap.add_argument('url'); ap.add_argument('--expect')
ap.add_argument('--xoff-after', type=int); ap.add_argument('--xoff-byte', default='0x93')
ap.add_argument('--hold', type=float, default=2.0); ap.add_argument('--idle', type=float, default=8.0)
ap.add_argument('--stty', help='pty path DOSBox-X opened; its termios = line settings set by the DOS program')
a = ap.parse_args()

class ListenPort:
    """'listen:PORT' - accept one TCP connection (DOSBox nullmodem configured with server:127.0.0.1)."""
    def __init__(self, port):
        import socket
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', port)); srv.listen(1)
        print(f'waiting for DOSBox to connect on {port} ...', file=sys.stderr)
        self.s, _ = srv.accept(); self.s.settimeout(0.05)
    def read(self, n):
        import socket
        try:
            d = self.s.recv(n)
        except socket.timeout:
            return b''
        if not d:
            raise serial.SerialException('socket disconnected')
        return d
    def write(self, b): self.s.sendall(b)

if a.url.startswith('listen:'):
    ser = ListenPort(int(a.url.split(':')[1]))
else:
    ser = serial.serial_for_url(a.url, 9600, timeout=0.05)
print(f'listening on {a.url} ... start the transfer in DOSBox-X', file=sys.stderr)
buf, stamps, first, last, xoff_sent, hold_count = bytearray(), [], None, None, False, None
stty_done = False
while True:
    try:
        chunk = ser.read(4096)
    except serial.SerialException:
        print('peer disconnected', file=sys.stderr); break
    now = time.time()
    if chunk:
        if first is None:
            first = now; print('data started', file=sys.stderr)
        if a.stty and not stty_done:
            print('line settings on DOSBox side:', subprocess.run(['stty', '-F', a.stty],
                  capture_output=True, text=True).stdout.split('\n')[0], file=sys.stderr)
            stty_done = True
        buf += chunk; last = now
        if a.xoff_after and not xoff_sent and len(buf) >= a.xoff_after:
            ser.write(bytes([int(a.xoff_byte, 0)])); xoff_sent = True; t_x = now; n_x = len(buf)
            print(f'sent XOFF {a.xoff_byte} at byte {len(buf)}', file=sys.stderr)
    if xoff_sent and hold_count is None and now - t_x >= a.hold:
        hold_count = len(buf) - n_x
        ser.write(b'\x11'); print(f'sent XON; bytes received during {a.hold}s hold: {hold_count}', file=sys.stderr)
    if first and now - last > a.idle:
        break

here = os.path.dirname(os.path.abspath(__file__))
open(f'{here}/capture.bin', 'wb').write(buf)
with open(f'{here}/capture.hex', 'w') as f:
    for i in range(0, len(buf), 16):
        row = buf[i:i+16]
        f.write(f'{i:06x}  {row.hex(" "):<48}  {"".join(chr(c) if 32 <= c < 127 else "." for c in row)}\n')
dur = (last - first) if first else 0
print(f'captured {len(buf)} bytes in {dur:.2f}s (~{len(buf)/dur if dur else 0:.0f} B/s) -> capture.bin / capture.hex')
print(f'contains LF: {10 in buf}; CR count: {buf.count(13)}; bytes >0x7F: {sum(c > 127 for c in buf)}')
if a.expect:
    exp = open(a.expect, 'rb').read().replace(b'\n', b'')
    print('MATCHES ALINK model (file with LF removed)' if bytes(buf) == exp else
          f'DIFFERS from model: expected {len(exp)} bytes')
