#!/usr/bin/env python3
"""alphalink.py - modern re-implementation of ALPHACAM V1.81 (DOS) serial transfers.

Behaviour reproduced from reverse engineering of ALINK.EXE / AMANOPS.EXE
(see ../ALPHACAM_RS232_PROTOCOL.md).

Modes
  send-alink   like ALINK:          9600 8E2, XON/XOFF, file sent raw, LF (0x0A) dropped
  send-load    like AMANOPS LOAD:   9600 7E2, XON/XOFF, even parity put in bit 7, >0x7F -> 0x00
  send-alphat  like ATSEND (Alphalink T, for Alpha "T" lathes): 9600 7E2, file sent unchanged (CR LF kept),
               XON 0x11 / XOFF 0x13
  recv-alphat  like ATMANOP SAVE (Alphalink T): 9600 7E2, bytes saved until the 2nd '%'
  recv-save    like AMANOPS SAVE:   9600 8E2, XON/XOFF, bytes saved raw until 2nd '%' or Ctrl-C

Flow control: by default mimics the original driver exactly (XON=0x11, XOFF=0x93 only).
--accept-dc3 also treats a plain 0x13 as XOFF (what the original SHOULD do on 7-bit lines).
Line settings can be overridden with --baud/--bytesize/--parity/--stopbits.
"""
import argparse, sys, time
import serial

XON, XOFF_ORIG, DC3 = 0x11, 0x93, 0x13
PROFILES = {
    'send-alink': dict(bytesize=8, parity='E', stopbits=2),
    'send-load':  dict(bytesize=7, parity='E', stopbits=2),
    'recv-save':  dict(bytesize=8, parity='E', stopbits=2),
    'send-alphat': dict(bytesize=7, parity='E', stopbits=2),
    'recv-alphat': dict(bytesize=7, parity='E', stopbits=2),   # like ATMANOP "Save Manual Ops from ALPHA"
   # like Alphalink-T ATSEND (verified in DOSBox-X)
}
EVEN_PAR = bytes(c | (0x80 if bin(c).count('1') & 1 else 0) for c in range(128)) + bytes(128)

def transform(mode, data):
    if mode == 'send-alink':
        return data.replace(b'\n', b'')
    if mode == 'send-load':
        return data.translate(EVEN_PAR)
    return data

def open_port(a, mode):
    p = dict(PROFILES[mode])
    if a.bytesize: p['bytesize'] = a.bytesize
    if a.parity: p['parity'] = a.parity
    if a.stopbits: p['stopbits'] = a.stopbits
    ser = serial.serial_for_url(a.port, baudrate=a.baud, timeout=0.05, write_timeout=5,
                                xonxoff=False, rtscts=False, do_not_open=True, **p)
    ser.open()
    try:
        ser.dtr = True; ser.rts = True     # original sets MCR=0x0B (DTR+RTS+OUT2)
    except Exception:
        pass
    return ser

def send(a, mode):  # send-alphat: transform() returns data unchanged
    data = transform(mode, open(a.file, 'rb').read())
    xoffs = {XOFF_ORIG} | ({DC3} if (a.accept_dc3 or mode == 'send-alphat') else set())
    ser = open_port(a, mode)
    bits_per_char = 1 + ser.bytesize + (ser.parity != 'N') + ser.stopbits
    paused, i, chunk = False, 0, 4           # small chunks ~ original 64-byte TX queue
    if a.wait_xon:
        print('Waiting for XON (DC1) from the control ... (start the load on the lathe; Ctrl-C to abort)', file=sys.stderr)
        while True:
            b = ser.read(1)
            if b:
                print(f'  got {b.hex()}', file=sys.stderr)
                if b[0] == XON: break
    print(f'Sending {len(data)} bytes ({mode}) on {a.port} ...', file=sys.stderr)
    while i < len(data):
        for b in ser.read(ser.in_waiting or 0):
            if b in xoffs: paused = True
            elif b == XON: paused = False
        if paused:
            time.sleep(0.01); continue
        n = 1 if (a.char_delay or a.line_delay) else chunk
        blk = data[i:i + n]
        ser.write(blk); i += n
        # pace at line rate so behaviour matches a real UART even on ptys/sockets
        time.sleep(len(blk) * bits_per_char / a.baud)
        if a.char_delay or a.line_delay:
            ser.flush()                      # make USB adapters really send byte by byte
            time.sleep(a.char_delay / 1000 + (a.line_delay / 1000 if blk in (b'\r', b'\n') else 0))
        if a.verbose: print(f'\r{min(i,len(data))}/{len(data)}', end='', file=sys.stderr)
    ser.flush()
    print('\nEnd Of Transmission.', file=sys.stderr)
    ser.close()

def recv(a, mode):
    ser = open_port(a, mode)
    out, pct = bytearray(), 0
    print(f'Waiting for data on {a.port} ... (Ctrl-C to stop)', file=sys.stderr)
    try:
        while True:
            for b in ser.read(256):
                out.append(b)
                if b == 0x25:
                    pct += 1
                    if pct == 2:
                        raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    ser.close()
    if out:
        open(a.file, 'wb').write(out)
        print(f'Saved {len(out)} bytes to {a.file}', file=sys.stderr)
    else:
        print('Nothing received; no file written.', file=sys.stderr)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=PROFILES)
    ap.add_argument('port', help='e.g. /dev/ttyUSB0, COM1, or a pyserial URL')
    ap.add_argument('file')
    ap.add_argument('--baud', type=int, default=9600)
    ap.add_argument('--bytesize', type=int, choices=[7, 8])
    ap.add_argument('--parity', choices=['N', 'E', 'O'])
    ap.add_argument('--stopbits', type=int, choices=[1, 2])
    ap.add_argument('--accept-dc3', action='store_true', help='also treat 0x13 as XOFF')
    ap.add_argument('--char-delay', type=float, default=0, help='extra ms after every byte (slow PC->Alpha link)')
    ap.add_argument('--line-delay', type=float, default=0, help='extra ms after every CR/LF')
    ap.add_argument('--wait-xon', action='store_true', help='wait for DC1 (0x11) from the control before sending')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    (recv if a.mode.startswith('recv') else send)(a, a.mode)

if __name__ == '__main__':
    main()
