#!/usr/bin/env python3
"""serial_diag.py PORT [loop]  - check a USB-serial adapter / cable for the Alpha link.
  PORT       e.g. /dev/ttyUSB0
  (no arg)   show the modem-control inputs (CTS DSR CD RI) while toggling RTS and DTR
  loop       loopback test: bridge DB9 pin 2 and 3 (RxD-TxD) first, then run this
  listen N   listen N seconds at 9600 7E2 (Fanuc ISO) and print every byte as it arrives, with modem lines
"""
import sys, time, serial
port = sys.argv[1]; mode = sys.argv[2] if len(sys.argv) > 2 else 'lines'
s = serial.Serial(port, 9600, bytesize=8, parity='E', stopbits=2, timeout=0.5)
def lines(tag): print(f'{tag:14s} CTS={int(s.cts)} DSR={int(s.dsr)} CD={int(s.cd)} RI={int(s.ri)}')
if mode == 'listen':
    s.close(); s = serial.Serial(port, 9600, bytesize=7, parity='E', stopbits=2, timeout=0.2)
    s.dtr = s.rts = True; n = float(sys.argv[3]) if len(sys.argv) > 3 else 30
    print(f'Listening {n:.0f} s at 9600 7E2. Now press the arrow key on the lathe (CAM-DATEN LADEN) ...')
    t = time.time(); last = None
    while time.time() - t < n:
        st = (int(s.cts), int(s.dsr), int(s.cd))
        if st != last: print(f'{time.time()-t:6.1f}s  CTS={st[0]} DSR={st[1]} CD={st[2]}'); last = st
        b = s.read(64)
        if b: print(f'{time.time()-t:6.1f}s  got {b.hex(" ")}  {b!r}')
    print('done')
elif mode == 'loop':
    s.reset_input_buffer(); msg = b'%\r\nO1234 LOOPBACK TEST\r\n%\r\n'
    s.write(msg); s.flush(); time.sleep(0.3); got = s.read(200)
    print('sent    :', msg); print('received:', got)
    print('LOOPBACK OK' if got == msg else 'LOOPBACK FAILED (adapter, or pins 2-3 not bridged)')
else:
    for dtr in (False, True):
        for rts in (False, True):
            s.dtr, s.rts = dtr, rts; time.sleep(0.2); lines(f'DTR={int(dtr)} RTS={int(rts)}')
    s.dtr = s.rts = True
    print('Listening 10 s for anything from the lathe (press keys / start CAM load on the control) ...')
    t = time.time(); buf = b''
    while time.time() - t < 10: buf += s.read(64)
    print('received:', buf if buf else 'nothing')
s.close()
