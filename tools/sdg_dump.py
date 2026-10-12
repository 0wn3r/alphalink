#!/usr/bin/env python3
"""sdg_dump.py NAME.SDG - decode the geometry of an ALPHACAM V1.81 drawing.
Layout (same as ADXF .SDL for the first 0x2400 bytes): segment table at 0x0000 (word0 = 0x3000|count, word1 = points,
then int16 (start,end,centre) triples, FF FF FF FF marker at 0x1FC and every 0x100), float32 A[768] at 0x0C00 and
B[768] at 0x1800. Point coordinates are relative to the machine reference set by G92 (settings block 0x5700:
float[3] = Zref = billet length + 20 (G92 Z), float[4] = Xref/2 (81.6901, i.e. G92 X163.3803)):
    Z = A - Zref        X(diameter) = 2 * (B - Xref/2)      all in mm.
Second layer (generated, flag 0x7000): table 0x2700, A at 0x3300, B at 0x3F00 = billet outline (both halves,
Z0 .. -(length+20)) plus the stepped chuck jaws (last 20 mm, dia +10/+20/+30). Settings: [6] billet length,
[7] billet diameter, [8] first tool, [9]/[10] tool start X/Z."""
import struct, sys
d = open(sys.argv[1], 'rb').read()
st = struct.unpack('<16f', d[0x5700:0x5740])
zref, xr2 = st[3], st[4]
print('settings: units=%s billet dia=%g len=%g first tool=%g tool start X%g Z%g  Zref=%g Xref=%g' %
      ('imperial' if st[5] else 'metric', st[7], st[6], st[8], st[9], st[10], zref, 2 * xr2))
words = []; pos = 0
while pos < 0xC00:
    if pos == 0x1FC or (pos > 0x1FC and (pos - 0x1FC) % 0x100 == 0): pos += 4; continue
    words.append(struct.unpack_from('<h', d, pos)[0]); pos += 2
n, npts = words[0] & 0xFF, words[1]
A = struct.unpack_from('<768f', d, 0xC00); B = struct.unpack_from('<768f', d, 0x1800)
P = [(round(2 * (B[i] - xr2), 4), round(A[i] - zref, 4)) for i in range(npts)]
print('points (X dia, Z):', P)
for k in range(n):
    s, e, c = words[2 + 3 * k: 5 + 3 * k]
    if c == 0: print('  LINE  X%g Z%g -> X%g Z%g' % (*P[-s - 1], *P[-e - 1]))
    else: print('  ARC %s X%g Z%g -> X%g Z%g centre X%g Z%g' % ('CCW' if c > 0 else 'CW', *P[-s - 1], *P[-e - 1], *P[abs(c) - 1]))

def layer(tbl, a, b, title):
    ws = []; pos = 0
    while pos < 0xC00:
        if pos == 0x1FC or (pos > 0x1FC and (pos - 0x1FC) % 0x100 == 0): pos += 4; continue
        ws.append(struct.unpack_from('<h', d, tbl + pos)[0]); pos += 2
    cnt, np2 = ws[0] & 0xFF, ws[1]
    A2 = struct.unpack_from('<768f', d, a); B2 = struct.unpack_from('<768f', d, b)
    Q = [(round(2 * (B2[i] - xr2), 4), round(A2[i] - zref, 4)) for i in range(np2)]
    print(title, '%d segments' % cnt)
    for k in range(cnt):
        s_, e_, c_ = ws[2 + 3 * k: 5 + 3 * k]
        print('  LINE  X%g Z%g -> X%g Z%g' % (*Q[-s_ - 1], *Q[-e_ - 1]) + ('' if c_ == 0x7000 else '  (flag %#x)' % c_))
if len(sys.argv) > 2 and sys.argv[2] == '-a': layer(0x2700, 0x3300, 0x3F00, 'billet + chuck layer:')
