#!/usr/bin/env python3
"""patch_metric600.py IN.EXE OUT.EXE [--undo]
Stop ALPHACAM V1.81 from converting metric sessions to inches when it writes the .600 program.
Overlay 16 (file offset 0x52400) is the .600 writer. At file offset 0x53A20 it tests the units flag
(seg [0x407E]:0 == 1, i.e. metric) and, if set, divides every coordinate by 25.4 (float32 at DGROUP:0x4082).
Patch: 75 4C (JNZ skip) -> EB 4C (JMP skip), so the values are written unchanged (mm). Imperial sessions are unaffected."""
import sys
src, dst = sys.argv[1], sys.argv[2]
d = bytearray(open(src, 'rb').read())
OFF = 0x53A20
want, new = (b'\xEB\x4C', 0x75) if '--undo' in sys.argv else (b'\x75\x4C', 0xEB)
if d[OFF:OFF+2] != want:
    sys.exit('unexpected bytes %s at 0x%X - not ALPHACAM V1.81 or already patched' % (d[OFF:OFF+2].hex(), OFF))
d[OFF] = new
open(dst, 'wb').write(d)
print('written', dst)
