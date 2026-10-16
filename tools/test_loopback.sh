#!/bin/sh
# Loopback self-test of alphalink.py using a socat pseudo-terminal pair (no hardware needed).
set -e
D=$(mktemp -d); cd "$(dirname "$0")"
socat -d pty,raw,echo=0,link=$D/a pty,raw,echo=0,link=$D/b 2>/dev/null & SP=$!
sleep 0.5
printf '%%\r\nO1000\r\nG00 X10. Z5.\r\nM30\r\n%%\r\n' > $D/prog.600
# 1) ALINK-style send -> SAVE-style receive
python3 alphalink.py recv-save $D/b $D/got.mop & RP=$!
sleep 0.3
python3 alphalink.py send-alink $D/a $D/prog.600
wait $RP
echo "--- sent file:";      od -c $D/prog.600 | head -3
echo "--- received (LF dropped by ALINK rule):"; od -c $D/got.mop | head -3
python3 - "$D" <<'P'
import sys; d=sys.argv[1]
src=open(d+'/prog.600','rb').read(); got=open(d+'/got.mop','rb').read()
exp=src.replace(b'\n',b''); exp=exp[:exp.index(b'%',1)+1]  # receiver stops at 2nd '%'
assert got==exp, 'MISMATCH'
print('PASS: send-alink -> recv-save matches expected CR-only stream')
P
# 2) LOAD-style send: check even-parity bit7 transform
python3 - <<'P'
import importlib.util,sys
s=importlib.util.spec_from_file_location('al','alphalink.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
t=m.transform('send-load',b'%O1\r\n')
assert t==bytes([0xa5,0xcf,0xb1,0x8d,0x0a]),t.hex()
print('PASS: send-load parity table', t.hex())
P
kill $SP 2>/dev/null; rm -rf $D

