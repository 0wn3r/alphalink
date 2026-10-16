#!/usr/bin/env python3
"""mk_at_rad.py NAME OP RETURNS SEGMENTS [EXEC] - an xkeys script for ALPHA-T like mk_at_script.py, for drawings with radius
corners. SEGMENTS is a Python list of ("x,z", "x,z") lines and "R<r>" items; an "R" fillets the current point (the end of
the line just drawn), so draw the two lines of a corner with the second one ending on it. RETURNS = elements to
accept when picking the contour; OP and EXEC as in mk_at_script.py (area 119 / 9, finish 138 / 4)."""
name, op, nret = sys.argv[1], sys.argv[2], int(sys.argv[3])
segs = eval(sys.argv[4])
execn = int(sys.argv[5]) if len(sys.argv) > 5 else 9
L = ["sleep 4", "key space", "sleep 3", "key F1", "sleep 3"]
for s in segs:
    if isinstance(s, str):
        L += ["click 580 195", "sleep 2", "type " + s[1:], "key Return", "sleep 2", "key Return", "sleep 2"]
    else:
        L += ["click 580 119", "sleep 1", "type " + s[0], "key Return", "sleep 0.5", "type " + s[1], "key Return", "sleep 1"]
L += ["shot g_" + name + "_draw", "click 580 461", "sleep 1.5"]
L += ["click 580 %s" % op, "sleep 1.5", "type 120,5", "key Return", "sleep 1.5"] + ["key Down"] * 6 + ["sleep 1", "key Return", "sleep 1.5", "key Return", "sleep 1"]
L += ["key Return", "sleep 0.8"] * nret
L += ["sleep 1", "shot g_" + name + "_menu"] + ["key Down"] * execn + ["sleep 0.5", "key Return", "sleep 10", "shot g_" + name + "_exec", "key Return", "sleep 8", "key space", "sleep 4"]
L += ["click 580 442", "sleep 2", "key Return", "sleep 4", "type " + name, "key Return", "sleep 3", "type 10", "key Return", "sleep 3", "key 0", "sleep 4", "key 1", "sleep 5", "shot g_" + name + "_saved"]
print("\n".join(L))
