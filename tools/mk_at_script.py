#!/usr/bin/env python3
"""mk_at_script.py OP NAME [opts]  - write an xkeys script for ALPHA-T: draw the MET2 test part, run one CAM op, save NAME.
OP: menu y of the CAM item (finish 138, area 119, face 157, groove 176, thread 195, drill 214) or 'none'.
--menu "keys"  extra keys in the op options menu before EXECUTE (space separated xkeys commands, ';' = newline)
--exec N       number of Down presses from the first option to EXECUTE (default 4)
--shot-menu    take a screenshot of the options menu
--pts "x,z x,z ..." contour points (default MET2)"""
import sys,argparse
ap=argparse.ArgumentParser(); ap.add_argument('op'); ap.add_argument('name'); ap.add_argument('--menu',default='')
ap.add_argument('--exec',type=int,default=4); ap.add_argument('--pts',default='0,0 40,0 40,-25 60,-25 60,-50 100,-50')
ap.add_argument('--start',default='120,5'); ap.add_argument('--prefinish',default='space'); ap.add_argument('--stop-at-menu',action='store_true')
ap.add_argument('--tool',default='',help="keys after clicking CHANGE TOOL (';' separated); empty = no tool change")
ap.add_argument('--extra',default='',help="extra separate lines 'x,z:x,z x,z:x,z' drawn after the contour")
ap.add_argument('--post',default='',help="keys after EXECUTE (';' separated) before the pre-finish question")
a=ap.parse_args()
pts=[p for p in a.pts.split()]
L=["sleep 4","key space","sleep 3","key F1","sleep 3"]
for p,q in zip(pts,pts[1:]):
    L+=["click 580 119","sleep 1","type "+p,"key Return","sleep 0.5","type "+q,"key Return","sleep 1"]
for seg in a.extra.split():
    p,q=seg.split(':'); L+=["click 580 119","sleep 1","type "+p,"key Return","sleep 0.5","type "+q,"key Return","sleep 1"]
L+=["shot g_"+a.name+"_draw","click 580 461","sleep 1.5"]
if a.tool: L+=["click 580 100","sleep 2","shot g_"+a.name+"_tool1"]+a.tool.split(';')+["sleep 2","shot g_"+a.name+"_tool2"]
if a.op!='none':
    L+=["click 580 %s"%a.op,"sleep 1.5","type "+a.start,"key Return","sleep 1.5"]+["key Down"]*6+["sleep 1","key Return","sleep 1.5","key Return","sleep 1"]
    L+=["key Return","sleep 0.8"]*(len(pts)-1)
    L+=["sleep 1","shot g_"+a.name+"_menu"]
    if a.stop_at_menu: open('/dev/stdout','w').write("\n".join(L)+"\n"); sys.exit()
    if a.menu: L+=a.menu.split(';')
    L+=["key Down"]*a.exec+["sleep 0.5","key Return","sleep 10"]
    if a.post: L+=a.post.split(';')
    L+=["shot g_"+a.name+"_exec","key Return","sleep 8","key "+a.prefinish,"sleep 4"]
L+=["click 580 442","sleep 2","key Return","sleep 4","type "+a.name,"key Return","sleep 3","type 10","key Return","sleep 3","key 0","sleep 4","key 1","sleep 5","shot g_"+a.name+"_saved"]
print("\n".join(L))
