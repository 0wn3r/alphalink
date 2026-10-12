#!/bin/sh
# postflip.sh ROW FIELD [NEWVAL]: patch field FIELD (0-based, whitespace-split) of row ROW of FANUC600.600 in the
# scratch copy, re-save the MET2 sample drawing and diff the regenerated .600 against the reference.
T=$(cd "$(dirname "$0")" && pwd); A=$T/../alphacam_samples
R=$1; F=$2; V=${3:-flip}
PRE="cp $A/MET2.SD1 $A/MET2.SDG $A/MET2.SDT DRAWINGS/; python3 - <<'PY'
p='FANUC600.600'; L=open(p,'rb').read().split(b'\r\n')
import re
row=L[$R-1].decode(); toks=list(re.finditer(r'\S+',row)); t=toks[$F]
old=t.group(); new=('0' if old!='0' else '1') if '$V'=='flip' else '$V'
row=row[:t.start()]+new.rjust(len(old))[-max(len(old),len(new)):]+row[t.end():] if len(new)<=len(old) else row[:t.start()-(len(new)-len(old))]+new+row[t.end():]
L[$R-1]=row.encode(); open(p,'wb').write(b'\r\n'.join(L))
PY"
PRE="$PRE" METRIC=1 RUN=pf ./acsession.sh ALPHACAM < "$T/ac_scripts/resave_met2.ks" >/dev/null 2>&1
O=$HOME/acruns/pf/PROGRAMS/MET2.600
if [ ! -f $O ]; then echo "row$R f$F: NO OUTPUT"; return 0 2>/dev/null || exit 0; fi
D=$(diff <(tr -d '\r' < $A/MET2.600) <(tr -d '\r' < $O) | grep '^[<>]' | head -4 | tr '\n' ' ')
echo "row$R f$F -> ${D:-same}"
