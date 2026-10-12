#!/usr/bin/env python3
"""Flow-control test for alphalink.py over a socat pty pair (fresh pair per case).
Fake CNC lets ~100 bytes through, sends XOFF, measures bytes in a 1 s window, then sends XON.
Expected (original-driver behaviour): 0x93 pauses; plain 0x13 does NOT, unless --accept-dc3."""
import os, subprocess, sys, tempfile, time, serial
here = os.path.dirname(os.path.abspath(__file__))
N = 1500
cases = [(0x93, [], True), (0x13, [], False), (0x13, ['--accept-dc3'], True)]
ok = True
for xoff, opt, expect_pause in cases:
    d = tempfile.mkdtemp()
    soc = subprocess.Popen(['socat', f'pty,raw,echo=0,link={d}/a', f'pty,raw,echo=0,link={d}/b'],
                           stderr=subprocess.DEVNULL)
    time.sleep(0.4)
    open(f'{d}/big.600', 'wb').write(b'A' * N)
    cnc = serial.Serial(f'{d}/b', 9600, timeout=0.02)
    snd = subprocess.Popen([sys.executable, f'{here}/alphalink.py', 'send-alink', f'{d}/a', f'{d}/big.600', *opt],
                           stderr=subprocess.DEVNULL)
    got = 0; t0 = time.time()
    while got < 100 and time.time() - t0 < 5:
        got += len(cnc.read(64))
    cnc.write(bytes([xoff])); cnc.flush()
    time.sleep(0.2); got += len(cnc.read(10000))          # allow in-flight bytes
    t1 = time.time(); during = 0
    while time.time() - t1 < 1.0:
        during += len(cnc.read(10000))
    cnc.write(b'\x11'); cnc.flush()
    t2 = time.time(); after = 0
    while time.time() - t2 < 4 and got + during + after < N:
        after += len(cnc.read(10000))
    try: snd.wait(5)
    except subprocess.TimeoutExpired: snd.kill()
    cnc.close(); soc.kill()
    total = got + during + after
    paused = during == 0
    res = 'PASS' if paused == expect_pause and total == N else 'FAIL'
    ok &= res == 'PASS'
    print(f'{res}: XOFF=0x{xoff:02x} opts={" ".join(opt) or "-"}: bytes during 1s XOFF window={during}, '
          f'total={total}/{N} (expected {"pause" if expect_pause else "no pause"})')
sys.exit(0 if ok else 1)
