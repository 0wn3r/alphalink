#!/bin/sh
# aprint_run.sh SAMPLE NAME OPT   - run APRINT (scratch copy, PRN patched to a file) on alphacam_samples/SAMPLE.*
# copied as DRAWINGS/NAME.*; OPT 1 = Print Tool Moves, 2 = Print Operation Details. Output -> aprint_samples/NAME_OPT.txt
R=$(cd "$(dirname "$0")/.." && pwd); S=$R/alphacam_samples; SMP=$1; N=$2; O=$3
K=$(mktemp); { echo "sleep 3"; [ "$O" = 2 ] && echo "key Down"; printf 'key Return\nsleep 2\ntype %s\nkey Return\nsleep 2\nkey Return\nsleep 8\nkey Return\nsleep 2\n' $N; } > $K
MMETRIC=1 PRE="python3 -c \"
d=bytearray(open('APRINT.EXE','rb').read()); i=d.find(b'PRN  \\\\\\\\ALPHACAM'); d[i:i+5]=b'PRT  '; open('APRINT.EXE','wb').write(d)\"; for e in SDG SDT SD1; do cp $S/$SMP.\$e DRAWINGS/$N.\$e 2>/dev/null; done" sh "$R/tools/acsession.sh" APRINT < $K
tr -d '\r' < $HOME/acwork/ALPHACAM/PRT > "$R/aprint_samples/${N}_$O.txt"; rm -f $K
