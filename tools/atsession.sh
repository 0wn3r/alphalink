#!/bin/sh
# acsession.sh PROG [ARGS]  < script     - run an ALPHACAM module in DOSBox-X under Xvfb and drive it with xkeys.
# Script commands: key <keysym> | skey <keysym> | type <text> | sleep <s> | shot <name>  (shots -> re-notes/_shots/<name>.png)
W=$(cd "$(dirname "$0")/../.." && pwd); T=$W/re-notes/tools; S=$W/re-notes/_shots
# Work on a scratch copy so the real Alpha181 folder is never modified.
# KEEP=1 reuses the previous scratch state; RUN=name snapshots the scratch dir afterwards to ~/acruns/name.
SC=$HOME/atwork; if [ "${KEEP:-0}" != 1 ]; then rm -rf $SC; mkdir -p $SC; cp -r "$W/AlphaT" $SC/ALPHA-T; (cd $SC/ALPHA-T; rm -f ALPHTZIP.EXE ATSETUP.EXE; mkdir -p DRAWINGS MANOPS MACROS DXF; printf 1 > PORT.CFG); fi
# EXTRACONF="[parallel]\nparallel1=...": extra dosbox-x config sections (literal newlines)
# PRE="shell cmd": run in the scratch ALPHACAM dir before starting (e.g. to patch the post)
[ -n "$PRE" ] && (cd $SC/ALPHA-T && sh -c "$PRE")
# METRIC=1: new drawings in mm; MMETRIC=1: .600 output in mm (header AZ). Old note: set the installed default units (ALPHACAM.MES record 0x0D, first field: 0=metric 1=imperial)
[ "${METRIC:-0}" = 1 ] && python3 "$T/set_units.py" "$SC/ALPHA-T/ALPHA-T.MES" --drawing metric >/dev/null
[ "${MMETRIC:-0}" = 1 ] && python3 "$T/set_units.py" "$SC/ALPHA-T/ALPHA-T.MES" --machine metric >/dev/null
C=$(mktemp --suffix=.conf); cat > $C <<EOC
[sdl]
output=surface
[dosbox]
memsize=16
[cpu]
cycles=fixed 30000
[mixer]
nosound=true
${EXTRACONF}
[autoexec]
mount c "$SC"
c:
cd \\ALPHA-T
$*
EOC
export SDL_AUDIODRIVER=dummy DISPLAY=:97
Xvfb :97 -screen 0 1024x768x24 >/dev/null 2>&1 & XV=$!; sleep 1
"$W/tools-bin/dosbox-x" -conf $C -fastlaunch >/dev/null 2>&1 & DB=$!
sleep 4
sed "s#^shot \(.*\)#shot $S/\1.png#" | "$T/xkeys"
kill -9 $DB 2>/dev/null; kill $XV 2>/dev/null; rm -f $C
[ -n "$RUN" ] && { rm -rf $HOME/acruns/$RUN; mkdir -p $HOME/acruns; cp -r $SC/ALPHA-T $HOME/acruns/$RUN; }
