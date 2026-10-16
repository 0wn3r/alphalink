#!/usr/bin/env python3
"""mk_at_job.py NAME - xkeys script for a whole ALPHA-T job (C1 chamfer + relief groove shaft; T1 area clear, T3 finish,
T16 groove, with tool changes). Later tool changes first ask "Tool Change Position [X.., Z..]": Enter accepts.
Chamfers: RAD/CHAMFER, size, Enter, Space (toggles radius -> chamfer), Enter."""
import sys
name = sys.argv[1]
segs = [("0,0","30,0"),("30,-25","30,0"),"C1",("30,-25","26,-25"),("26,-25","26,-28"),("26,-28","40,-28"),("40,-28","40,-50"),("40,-50","100,-50")]
NRET = 8  # face, chamfer, X30, wall, floor, wall, X40, face
L = ["sleep 4","key space","sleep 3","key F1","sleep 3"]
for s in segs:
    if isinstance(s, str):
        L += ["click 580 195","sleep 2","type "+s[1:],"key Return","sleep 2","key space","sleep 1.5","key Return","sleep 2"]
    else:
        L += ["click 580 119","sleep 1","type "+s[0],"key Return","sleep 0.5","type "+s[1],"key Return","sleep 1"]
L += ["shot g_%s_draw" % name, "click 580 461", "sleep 1.5"]
def tool(keys, later=True):
    ask = ["sleep 3", "shot g_%s_tc%d" % (name, len(keys)), "key Return"] if later else []
    return ["click 580 100"] + ask + ["sleep 5"] + [k for key in keys for k in (key, "sleep 0.3")] + ["sleep 1", "key Return", "sleep 3", "key Down", "sleep 1", "key Return", "sleep 3"]
def pick(op):
    return ["click 580 %d" % op,"sleep 1.5","type 120,5","key Return","sleep 1.5"]+["key Down"]*6+["sleep 1","key Return","sleep 1.5","key Return","sleep 1"]+["key Return","sleep 0.8"]*NRET+["sleep 1"]
# T1 rough
L += tool(["key Left","key Left"], False) + pick(119) + ["shot g_%s_m1" % name] + ["key Down"]*9 + ["sleep 0.5","key Return","sleep 12","key Return","sleep 8","key space","sleep 4"]
# T3 finish
L += tool(["key Right","key Right"]) + pick(138) + ["shot g_%s_m2" % name] + ["key Down"]*4 + ["sleep 0.5","key Return","sleep 10","key Return","sleep 6"]
# T16 groove, tip width 2, stepover 1.5
L += tool(["key Right"]*13) + pick(176) + ["shot g_%s_m3" % name, "key Down","key Down","key Down","key Return","sleep 2","type 1.5","key Return","sleep 2","type 2","key Return","sleep 2"] + ["key Down"]*6 + ["sleep 0.5","key Return","sleep 10","key 1","sleep 4","key Return","sleep 6","key Return","sleep 30","shot g_%s_m4" % name]
L += ["click 580 442","sleep 4","key Return","sleep 5","type "+name,"key Return","sleep 3","type 10","key Return","sleep 3","key 0","sleep 4","key 1","sleep 40","shot g_%s_saved" % name]
print("\n".join(L))
