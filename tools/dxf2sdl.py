#!/usr/bin/env python3
"""dxf2sdl.py - re-implementation of ALPHACAM ADXF.EXE (DXF -> .SDL geometry file).
usage: dxf2sdl.py in.dxf out.sdl [--imperial]
Rules reverse engineered from ADXF V1.81 output (see ../ALPHACAM_RS232_PROTOCOL.md, ADXF section)."""
import math, struct, sys

def groups(path):
    lines = open(path, 'r', errors='replace').read().splitlines()
    for i in range(0, len(lines) - 1, 2):
        yield int(lines[i].strip()), lines[i + 1].strip()

def entities(path):
    sec, cur = None, None
    for c, v in groups(path):
        if c == 0:
            if cur is not None and sec == 'ENTITIES':
                yield cur
            cur = None
            if v == 'SECTION': sec = 'pending'; continue
            if v == 'ENDSEC': sec = None; continue
            cur = {'type': v, 'g': []}
        elif c == 2 and sec == 'pending':
            sec = v
        elif cur is not None:
            cur['g'].append((c, v))
    if cur is not None and sec == 'ENTITIES':
        yield cur

class SDL:
    def __init__(self, scale): self.pts, self.segs, self.k = [], [], scale
    def p(self, x, y):
        q = (struct.unpack('<f', struct.pack('<f', x * self.k))[0],
             struct.unpack('<f', struct.pack('<f', y * self.k))[0])
        if q not in self.pts: self.pts.append(q)
        return self.pts.index(q) + 1
    def line(self, a, b):
        e = self.p(*b); s = self.p(*a); self.segs.append((-s, -e, 0))
    def arc(self, a, b, c, ccw):
        e = self.p(*b); cc = self.p(*c); s = self.p(*a)
        self.segs.append((-s, -e, cc if ccw else -cc))
    def write(self, path):
        words = [len(self.segs) | 0x3000, len(self.pts)] + [w for s in self.segs for w in s]
        tab = bytearray(0xC00); i = 0; pos = 0
        for wv in words:
            if pos in (0x1FC,) or (pos > 0x1FC and (pos - 0x1FC) % 0x100 == 0):
                tab[pos:pos + 4] = b'\xff' * 4; pos += 4
            struct.pack_into('<h', tab, pos, wv); pos += 2
        for m in range(0x1FC, 0xC00, 0x100):
            tab[m:m + 4] = b'\xff' * 4
        X = bytearray(0xC00); Y = bytearray(0xC00)
        for n, (x, y) in enumerate(self.pts):
            struct.pack_into('<f', X, 4 * n, x); struct.pack_into('<f', Y, 4 * n, y)
        open(path, 'wb').write(bytes(tab) + bytes(X) + bytes(Y))

def bulge_arc(sdl, a, b, bulge):
    th = 4 * math.atan(bulge); d = math.dist(a, b); r = d / (2 * math.sin(abs(th) / 2))
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    h = math.sqrt(max(r * r - (d / 2) ** 2, 0)) * (1 if abs(th) < math.pi else -1) * (1 if bulge > 0 else -1)
    ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
    c = (mx - uy * h, my + ux * h)
    c = (round(c[0], 9), round(c[1], 9))
    sdl.arc(a, b, c, bulge > 0)

def convert(src, dst, imperial=False):
    sdl = SDL(25.4 if imperial else 1.0)
    poly = None
    for e in entities(src):
        g = {}
        for c, v in e['g']: g.setdefault(c, v)
        f = lambda c, d=0.0: float(g.get(c, d))
        t = e['type']
        if t == 'LINE':
            sdl.line((f(10), f(20)), (f(11), f(21)))
        elif t == 'ARC':
            cx, cy, r = f(10), f(20), f(40); a0, a1 = math.radians(f(50)), math.radians(f(51))
            pt = lambda a: (round(cx + r * math.cos(a), 9), round(cy + r * math.sin(a), 9))
            sdl.arc(pt(a0), pt(a1), (cx, cy), True)
        elif t == 'CIRCLE':
            cx, cy, r = f(10), f(20), f(40); sdl.arc((cx + r, cy), (cx + r, cy), (cx, cy), False)
        elif t in ('SOLID', 'TRACE'):
            q = [(f(10 + k), f(20 + k)) for k in range(4)]
            for k in range(4): sdl.line(q[k], q[(k + 1) % 4])
        elif t == 'POLYLINE':
            poly = {'closed': int(f(70)) & 1, 'v': []}
        elif t == 'VERTEX' and poly is not None:
            poly['v'].append(((f(10), f(20)), f(42)))
        elif t == 'SEQEND' and poly is not None:
            v = poly['v'] + ([poly['v'][0]] if poly['closed'] and poly['v'] else [])
            for (a, bg), (b, _) in zip(v, v[1:]):
                if bg: bulge_arc(sdl, a, b, bg)
                else: sdl.line(a, b)
            poly = None
        # POINT, TEXT, SHAPE, INSERT/BLOCK, ATTDEF/ATTRIB: ignored by ADXF
    sdl.write(dst)

if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2], '--imperial' in sys.argv)
