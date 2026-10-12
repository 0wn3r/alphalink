#!/bin/sh
# patch254.sh FILEOFFSET : run the re-save test with one float32 25.4 constant in the unpacked ALPHACAM.EXE set to 1.0
T=$(cd "$(dirname "$0")" && pwd); A=$T/../alphacam_samples; U=$T/../unpacked/ALPHACAM_OVL.EXE
PRE="cp $A/MET2.SD1 $A/MET2.SDG $A/MET2.SDT DRAWINGS/; cp $U ALPHACAM.EXE; python3 -c \"import struct;p='ALPHACAM.EXE';d=bytearray(open(p,'rb').read());d[$1:$1+4]=struct.pack('<f',1.0);open(p,'wb').write(d)\""
PRE="$PRE" METRIC=1 RUN=pk ./acsession.sh ALPHACAM < "$T/ac_scripts/resave_met2.ks" >/dev/null 2>&1
O=$HOME/acruns/pk/PROGRAMS/MET2.600
[ -f $O ] || { echo "$1: NO OUTPUT"; exit 0; }
echo "$1: $(grep -m2 -E '^G0[01]|^#503' $O | tr -d '\r' | tr '\n' ' ')"
