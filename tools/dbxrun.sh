#!/bin/sh
# dbxrun.sh PROG "KEYS" SECONDS SHOTNAME   - run an ALPHACAM module headless in DOSBox-X, stuff keys, screenshot
# KEYS uses KEYS.COM syntax: letters/digits, ^=Enter, `=Esc, ~=Down arrow
W=$(cd "$(dirname "$0")/../.." && pwd); T=$W/re-notes/tools; S=$W/re-notes/_shots
C=$(mktemp --suffix=.conf); cat > $C <<EOC
[sdl]
output=surface
[dosbox]
memsize=16
[cpu]
cycles=fixed 20000
[mixer]
nosound=true
[autoexec]
mount c "$W/dosc"
mount d "$T"
c:
cd \\ALPHACAM
d:\\KEYS $2
c:
$1
EOC
export SDL_AUDIODRIVER=dummy DISPLAY=:98
# DOSBox-X asks "Quit DOSBox-X?" through zenity; without this the question pops up on the real desktop
unset WAYLAND_DISPLAY
Xvfb :98 -screen 0 1024x768x24 >/dev/null 2>&1 & XV=$!; sleep 1
"$W/tools-bin/dosbox-x" -conf $C -fastlaunch >/dev/null 2>&1 & DB=$!
sleep ${3:-8}; import -window root -crop 720x420+0+0 "$S/$4.png"; kill $DB; sleep 0.5; kill -9 $DB 2>/dev/null; kill $XV; rm -f $C
