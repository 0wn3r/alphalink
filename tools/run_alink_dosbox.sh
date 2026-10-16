#!/bin/sh
# Headless run of the ORIGINAL ALINK.EXE in DOSBox 0.74 (unpacked in alpha-work/tools-bin) with wire capture.
#   tools/run_alink_dosbox.sh [PROGNAME] [cnc_capture options...]
#   e.g.  tools/run_alink_dosbox.sh BIGTEST --xoff-after 300 --xoff-byte 0x93 --hold 3
# PROGNAME is a file in Alpha181/PROGRAMS without .600 (default LOOPTEST). Needs Xvfb, python3-serial.
set -e
T=$(cd "$(dirname "$0")" && pwd); W=$(cd "$T/../.." && pwd); N=${1:-LOOPTEST}; [ $# -gt 0 ] && shift
mkdir -p "$W/dosc"; [ -e "$W/dosc/ALPHACAM" ] || ln -s ../vendor/Alpha181 "$W/dosc/ALPHACAM"
C=$(mktemp --suffix=.conf)
sed "s#@DOSC@#$W/dosc#; s#@TOOLS@#$T#; s#KEYS LOOPTEST#KEYS $N#" "$T/dosbox074-alink.conf" > "$C"
export LD_LIBRARY_PATH="$W/tools-bin/dosbox/usr/lib/x86_64-linux-gnu" SDL_AUDIODRIVER=dummy DISPLAY=:99
Xvfb :99 -screen 0 800x600x24 >/dev/null 2>&1 & XV=$!; sleep 1
python3 "$T/cnc_capture.py" listen:5001 --expect "$W/vendor/Alpha181/PROGRAMS/$N.600" --idle 5 "$@" & CP=$!; sleep 1
"$W/tools-bin/dosbox/usr/bin/dosbox" -conf "$C" >/dev/null 2>&1 & DB=$!
wait $CP; kill $DB $XV 2>/dev/null || true
