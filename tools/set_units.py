#!/usr/bin/env python3
"""set_units.py ALPHACAM.MES [--drawing metric|imperial] [--machine metric|imperial]
ALPHACAM V1.81 keeps two unit settings in ALPHACAM.MES (80-byte records; ASETUP writes them at install):
  record 0x0C, first field  = MACHINE / .600 output units: 1 = inch  -> header 'AI', values /25.4, 4 decimals
                                                           0 = metric -> header 'AZ', values in mm, 3 decimals
  record 0x0D, first field  = default units for NEW drawings (0 = metric, 1 = imperial); also holds billet defaults
Each drawing stores its own units (SDG settings block), so record 0x0D only affects new drawings."""
import sys, argparse
ap = argparse.ArgumentParser(); ap.add_argument('mes'); ap.add_argument('--drawing'); ap.add_argument('--machine')
a = ap.parse_args()
d = bytearray(open(a.mes, 'rb').read())
for rec, val in ((0x0C, a.machine), (0x0D, a.drawing)):
    if val:
        d[rec*80:rec*80+3] = b'  0' if val.lower().startswith('m') else b'  1'
open(a.mes, 'wb').write(d)
print('record 0x0C (machine/.600):', d[0x0C*80:0x0C*80+3].decode().strip(), '| record 0x0D (new drawings):', d[0x0D*80:0x0D*80+3].decode().strip(), '(0=metric 1=inch)')
