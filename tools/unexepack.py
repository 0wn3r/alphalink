#!/usr/bin/env python3
"""Minimal Microsoft EXEPACK unpacker -> normal MZ EXE.  usage: unexepack.py in.exe out.exe"""
import struct, sys
def unpack(path, out):
    d = open(path,'rb').read()
    h = list(struct.unpack('<14H', d[:28]))
    hdr = h[4]*16
    img = bytes(d[hdr:(h[2]-1)*512 + (h[1] or 512)])
    eb, hlen = h[11]*16, h[10]
    f = struct.unpack('<%dH' % (hlen//2), img[eb:eb+hlen])
    real_ip, real_cs, mem_start, exepack_size, real_sp, real_ss, dest_len = f[:7]
    skip_len = f[7] if hlen >= 18 else 1
    assert f[-1] == 0x4252
    src = eb - (skip_len-1)*16 - 1
    buf = bytearray(dest_len*16)
    buf[:eb] = img[:eb]
    while img[src] == 0xFF: src -= 1
    dst = dest_len*16 - 1
    while True:
        cmd = img[src]; ln = img[src-1]<<8 | img[src-2]; src -= 3
        if cmd & 0xFE == 0xB0:
            v = img[src]; src -= 1
            buf[dst-ln+1:dst+1] = bytes([v])*ln; dst -= ln
        elif cmd & 0xFE == 0xB2:
            buf[dst-ln+1:dst+1] = img[src-ln+1:src+1]; dst -= ln; src -= ln
        else:
            raise ValueError('bad cmd %02x' % cmd)
        if cmd & 1: break
    # relocation table follows the error string in the stub
    s = img.find(b'Packed file is corrupt', eb)
    p = s + len(b'Packed file is corrupt')
    relocs = []
    for seg in range(16):
        n = struct.unpack('<H', img[p:p+2])[0]; p += 2
        for _ in range(n):
            o = struct.unpack('<H', img[p:p+2])[0]; p += 2
            relocs.append((o, seg*0x1000))
    # build MZ
    nrel = len(relocs)
    hdrsize = (28 + 4*nrel + 15)//16*16
    total = hdrsize + len(buf)
    newh = struct.pack('<14H', 0x5A4D, total % 512, (total+511)//512, nrel, hdrsize//16,
                       h[5] + 0, h[6], real_ss, real_sp, 0, real_ip, real_cs, 28, 0)
    rt = b''.join(struct.pack('<HH', o, s) for o, s in relocs)
    out_b = newh + rt
    out_b += b'\0'*(hdrsize-len(out_b)) + bytes(buf)
    open(out,'wb').write(out_b)
    return dict(ip=real_ip, cs=real_cs, ss=real_ss, sp=real_sp, size=len(buf), relocs=nrel)
if __name__ == '__main__':
    print(unpack(sys.argv[1], sys.argv[2]))
