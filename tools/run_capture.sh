#!/bin/sh
# Run on the machine where dosbox-x is installed.
#   MODE=tcp  (default) : DOSBox-X nullmodem -> TCP 5001 -> cnc_capture.py   (byte stream + flow control)
#   MODE=pty            : DOSBox-X directserial -> socat pty pair -> cnc_capture.py, and reports the
#                         line settings (baud/bits/parity/stop) ALINK programmed, via stty.
# Extra args are passed to cnc_capture.py, e.g.  ./run_capture.sh --xoff-after 200 --xoff-byte 0x93
set -e
T=$(cd "$(dirname "$0")" && pwd); W=$(cd "$T/../.." && pwd)
DOSC="$W/dosc"; mkdir -p "$DOSC"; [ -e "$DOSC/ALPHACAM" ] || ln -s ../vendor/Alpha181 "$DOSC/ALPHACAM"
P="$W/vendor/Alpha181/PROGRAMS/LOOPTEST.600"
[ -f "$P" ] || printf '%%\r\nO1000(LOOPTEST)\r\nG00 X100.0 Z5.0\r\nG01 X50.0 F0.2\r\nM30\r\n%%\r\n' > "$P"
CONF=$(mktemp --suffix=.conf); sed "s#@DOSC@#$DOSC#" "$T/dosbox-alink.conf" > "$CONF"
if [ "${MODE:-tcp}" = pty ]; then
  socat pty,raw,echo=0,link=/tmp/alpha_dos pty,raw,echo=0,link=/tmp/alpha_cnc & SOC=$!; sleep 0.5
  DEV=$(readlink -f /tmp/alpha_dos); RP=${DEV#/dev/}
  sed -i "s|^serial1=.*|serial1=directserial realport:$RP|" "$CONF"
  python3 "$T/cnc_capture.py" /tmp/alpha_cnc --stty /tmp/alpha_dos --expect "$P" "$@" & CAP=$!
  dosbox-x -conf "$CONF" & DB=$!
else
  dosbox-x -conf "$CONF" & DB=$!; sleep 3
  python3 "$T/cnc_capture.py" socket://127.0.0.1:5001 --expect "$P" "$@" & CAP=$!
fi
echo ">> In DOSBox: ALINK starts and LOOPTEST is typed automatically. After 'End Of Transmission' press Enter, then Esc."
wait $CAP; kill $DB ${SOC:-} 2>/dev/null || true
