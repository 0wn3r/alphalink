"""Screenshots for the Training and Reference Manuals: manual_shots.py OUTDIR

Run headless, once per language, e.g.
  ALPHALINK_LANG=uk GSK_RENDERER=cairo GDK_BACKEND=x11 xvfb-run -a -s "-screen 0 1400x900x24" \
      python3 tools/manual_shots.py shots/uk
"""
import sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw, Gtk, GLib
from alphacam_gtk.application import Application
from alphacam_gtk.window import MainWindow
from alphacam.model import Point, Line
from alphacam.model.project import Project, Billet
OUT=Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
app=Application()
def find(root, kind):
    out=[]; c=root.get_first_child()
    while c:
        if isinstance(c,kind): out.append(c)
        out+=find(c,kind); c=c.get_next_sibling()
    return out
def shot(widget, name):
    w,h=widget.get_width(),widget.get_height()
    pt=Gtk.WidgetPaintable(widget=widget); sn=Gtk.Snapshot(); pt.snapshot(sn,w,h)
    widget.get_native().get_renderer().render_texture(sn.to_node(),None).save_to_png(str(OUT/f'{name}.png'))
def run():
    w=MainWindow(app); w.set_default_size(1368,860); w.present()
    d=w.drawing
    def typ(*pts):
        for t in pts:
            d.entry.set_text(t); d._entry_activate(d.entry)
        d.finish_chain()
    def edit(i, **kw):
        w.undo.apply('edit', lambda p: [setattr(p.operations[i], k, v) for k, v in kw.items()]); w.refresh()
    def close():
        dl=w.get_visible_dialog()
        if dl: dl.force_close()
        for p in find(w.canvas, Gtk.Popover): p.popdown()
    def gid(x1,z1,x2,z2):
        for g in w.project.geometry:
            if {(g.a.x,g.a.z),(g.b.x,g.b.z)}=={(x1,z1),(x2,z2)}: return g.id
    def shaft():
        w.set_project(Project(billet=Billet(100,60), first_tool=3))
        d.set_tool('line'); typ('0,0','40,0','@0,-25','60,-25','60,-50','100,-50')
        d._apply_corner('chamfer', Point(40,0), 1, 45); d._apply_corner('fillet', Point(60,-50), 2)
    def rough():
        w.add_op('tool_change'); edit(0, tool=1)
        w.add_op('area_clear'); edit(1, depth_of_cut=2.0, feed=0.2, start=Point(120,5), offset_x=0.2, offset_z=0.1)
        w.add_op('tool_change'); edit(2, tool=3)
        w.add_op('finish'); edit(3, start=Point(120,5))
        w._select_op(1)
    def groove_part():
        close(); w.set_project(Project(billet=Billet(60,60), first_tool=3)); d.set_tool('line')
        typ('0,0','40,0','40,-20','34,-20','34,-26','40,-26','40,-40','60,-40'); typ('38.2,0','38.2,-18')
        w.add_op('tool_change'); edit(0, tool=16)
        w.add_op('groove')
        ids=[gid(40,0,40,-20),gid(40,-20,34,-20),gid(34,-20,34,-26),gid(34,-26,40,-26),gid(40,-26,40,-40)]
        edit(1, contour=ids, tip_width=2.0, stepover=1.5, start=Point(70,5))
        w.add_op('tool_change'); edit(2, tool=8)
        w.add_op('thread'); edit(3, outer_line=gid(40,0,40,-20), inner_line=gid(38.2,0,38.2,-18), pitch=1.5, cut_depth=0.2, spring_passes=1, start=Point(70,5))
        d.set_tool('select'); w._select_op(3)
    def bore():
        close(); w.set_project(Project(billet=Billet(80,50), first_tool=12)); d.set_tool('line')
        typ('50,0','50,-20','36,-20','36,-40','20,-40'); d.set_tool('select')
        w.add_op('tool_change'); edit(0, tool=12)
        w.add_op('area_clear'); edit(1, external=False, start=Point(15,5))
        w.add_op('finish'); edit(2, external=False, start=Point(15,5))
        w._select_op(1)
    steps=[
     ('01_start', lambda: None, None),
     ('02_new_part', lambda: w.new_dialog(), None),
     ('03_shaft', lambda: (close(), shaft(), d.set_tool('select')), None),
     ('04_chamfer', lambda: (d.set_tool('chamfer'), d._size_popover('chamfer', Point(60,-25))), 'pop'),
     ('05_rough_finish', lambda: (close(), d.set_tool('select'), rough()), None),
     ('13_simulation', lambda: (w._select_op(None), w.sim.goto(0), *[w.sim.step_pass(1) for _ in range(9)]), None),
     ('14_more_menu', lambda: (w.sim.goto(None), d.more.popup()), 'more'),
     ('15_part_settings', lambda: (d.more.popdown(), w.part_settings_dialog()), None),
     ('18_thread', lambda: (close(), d.set_tool('select'), d.thread_dialog(gid(40,-1,40,-25))), None),
     ('06_pick', lambda: (close(), w._select_op(None), d.start_pick(3), [d._toggle_pick(g.id) for g in w.project.geometry[:3]]), None),
     ('07_groove_thread', lambda: (d.cancel_pick(), groove_part()), None),
     ('08_bore', lambda: bore(), None),
     ('09_program', lambda: (close(), shaft(), d.set_tool('select'), rough(), w._select_op(None), w.stack.set_visible_child_name('program')), None),
     ('10_sheet', lambda: w.stack.set_visible_child_name('sheet'), None),
     ('16_tools', lambda: w.stack.set_visible_child_name('tools'), None),
     ('17_preferences', lambda: w.preferences_dialog(), None),
     ('11_machine', lambda: (close(), w.stack.set_visible_child_name('machine')), None),
     ('12_send', lambda: w.machine._confirm_send(), None),
     ('19_safety', lambda: (close(), shaft(), d.set_tool('select'), rough(), w.stack.set_visible_child_name('part'), edit(3, offset_x=-1.0), w._select_op(3)), None),
    ]
    i=[0]
    def step():
        if i[0]>=len(steps):
            close(); w.undo.mark_saved(); app.quit(); return False
        name,fn,kind=steps[i[0]]
        try: fn()
        except Exception: print('STEP FAIL',name); traceback.print_exc()
        w.dismiss_toasts()  # a toast from setting the part up is not what the shot shows
        def cap():
            try:
                if kind=='more':
                    pops=[p for p in find(d.more, Gtk.Popover) if p.get_visible()]
                    shot(pops[-1], name)
                elif kind=='pop':
                    pops=[p for p in find(w.canvas, Gtk.Popover) if p.get_visible()]
                    shot(pops[-1], name)
                else: shot(w,name)
            except Exception: print('SHOT FAIL',name); traceback.print_exc()
            i[0]+=1; GLib.timeout_add(50, step); return False
        GLib.timeout_add(700, cap); return False
    GLib.timeout_add(500, step)
app.connect('activate', lambda *_: GLib.idle_add(run)); app.run([])
