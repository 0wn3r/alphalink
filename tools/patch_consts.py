#!/usr/bin/env python3
"""patch_consts.py IN OUT KIND IDX...  - replace the IDX-th occurrences (0-based) of constant 2.0 (KIND f32|f64) with 1.0"""
import sys,struct,re
src,dst,kind=sys.argv[1:4]; idx={int(x) for x in sys.argv[4:]}
fmt='<f' if kind=='f32' else '<d'
d=bytearray(open(src,'rb').read()); p=struct.pack(fmt,2.0); q=struct.pack(fmt,1.0)
occ=[m.start() for m in re.finditer(re.escape(p),bytes(d))]
for i in idx: d[occ[i]:occ[i]+len(p)]=q
open(dst,'wb').write(d); print('patched',[hex(occ[i]) for i in sorted(idx)])
