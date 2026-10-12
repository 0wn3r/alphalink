#!/usr/bin/env python3
"""Dump an ALPHACAM .SDL geometry file (format reverse engineered from ADXF output)."""
import struct, sys
d = open(sys.argv[1], 'rb').read()
w = struct.unpack('<256h', d[:512])
npts = w[1]
print('word0=0x%04x (lo=%d hi=%d) npts=%d' % (w[0] & 0xffff, w[0] & 0xff, (w[0] >> 8) & 0xff, npts))
X = struct.unpack('<768f', d[0xc00:0x1800]); Y = struct.unpack('<768f', d[0x1800:0x2400])
print('points:', [(round(X[i], 4), round(Y[i], 4)) for i in range(npts)])
i = 2; ents = []
while i + 2 < 256 and any(w[i:i+3]):
    ents.append(w[i:i+3]); i += 3
for e in ents: print('  seg', e)
for k in range(10):
    blk = d[0x200 + k*256:0x200 + (k+1)*256]
    if blk[:-4].strip(b'\0'): print('block', k, blk.rstrip(b'\0')[:80])
