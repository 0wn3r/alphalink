"""LatheCanvas: draws the part, billet, chuck and the posted toolpaths; touch and pointer input via InputRouter."""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from alphacam.io.prg_parse import MoveKind  # noqa: E402
from alphacam.model.geometry import Arc  # noqa: E402

from .hittest import RADIUS_PX, hit  # noqa: E402
from .input import InputRouter  # noqa: E402
from .paths import segments  # noqa: E402
from .view import ViewTransform  # noqa: E402

# (light, dark) RGB
COLOURS = {
    "bg": ((0.98, 0.98, 0.97), (0.13, 0.13, 0.14)),
    "axis": ((0.55, 0.55, 0.55), (0.5, 0.5, 0.5)),
    "billet": ((0.6, 0.6, 0.62), (0.45, 0.45, 0.48)),
    "chuck": ((0.8, 0.8, 0.82), (0.28, 0.28, 0.31)),
    "part": ((0.1, 0.1, 0.12), (0.92, 0.92, 0.92)),
    "feed": ((0.11, 0.42, 0.85), (0.38, 0.62, 1.0)),
    "rapid": ((0.9, 0.38, 0.0), (1.0, 0.6, 0.25)),
    "thread": ((0.55, 0.2, 0.75), (0.75, 0.5, 0.95)),
}


class LatheCanvas(Gtk.DrawingArea):
    __gsignals__ = {
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        "op-tapped": (GObject.SignalFlags.RUN_FIRST, None, (int,)),  # -1: tapped empty space
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.project = None
        self.preview = None
        self.segs = []
        self.selected: int | None = None
        self.view = ViewTransform()
        self.pointer = (0.0, 0.0)
        self._fit_pending = True
        self._before_fit: ViewTransform | None = None
        self.set_draw_func(self._draw)
        self.connect("resize", lambda *_: self._fit_pending and self.fit())
        self.router = InputRouter(self, self.queue_draw, self._tap, self.toggle_fit, self._pointer)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())

    # --- data -------------------------------------------------------------------------------------------

    def set_project(self, project, preview) -> None:
        self.project, self.preview, self.selected = project, preview, None
        self.segs = segments(preview)
        self._fit_pending = True
        self.fit()

    def update(self, project, preview) -> None:
        """New preview after an edit: keep the view (no refit) and the selection."""
        self.project, self.preview = project, preview
        self.segs = segments(preview)
        self.queue_draw()

    def select(self, op_index: int | None) -> None:
        self.selected = op_index
        self.queue_draw()

    # --- view -------------------------------------------------------------------------------------------

    def bounds(self):
        zs, rs = [0.0], [0.0]
        p = self.project
        if p is not None:
            zs += [-p.billet.length - 21]
            rs.append(p.billet.diameter / 2 + 16)
            for g in p.geometry:
                for pt in (g.a, g.b):
                    zs.append(pt.z)
                    rs.append(pt.x / 2)
        for s in self.segs:
            for x, z in (s.a, s.b):
                zs.append(z)
                rs.append(x / 2)
        r1 = max(rs)
        return min(zs), max(zs), min(min(rs), -0.12 * r1), r1

    def fit(self) -> None:
        w, h = self.get_width(), self.get_height()
        if w < 10 or h < 10:
            self._fit_pending = True
            return
        self.view.fit(self.bounds(), w, h)
        self._fit_pending = False
        self.queue_draw()

    def toggle_fit(self) -> None:
        """Double tap: fit; double tap again returns to the previous view (TOUCH.md 4)."""
        if self._before_fit is not None:
            self.view, self._before_fit = self._before_fit, None
            self.queue_draw()
            return
        self._before_fit = self.view.copy()
        self.fit()

    def zoom(self, factor: float) -> None:
        self.view.zoom_at(self.get_width() / 2, self.get_height() / 2, factor)
        self.queue_draw()

    def _pointer(self, x, y) -> None:
        self.pointer = (x, y)
        self.emit("cursor-moved", *self.view.to_world(x, y))

    def _tap(self, x, y, source) -> None:
        s = hit(self.segs, self.view, x, y, RADIUS_PX[source])
        self.emit("op-tapped", -1 if s is None or s.op_index is None else s.op_index)

    # --- drawing ----------------------------------------------------------------------------------------

    def _rgb(self, cr, name: str, alpha: float = 1.0) -> None:
        light, dark = COLOURS[name]
        r, g, b = dark if Adw.StyleManager.get_default().get_dark() else light
        cr.set_source_rgba(r, g, b, alpha)

    def _draw(self, area, cr, width, height) -> None:
        self._rgb(cr, "bg")
        cr.paint()
        if self.project is None:
            return
        v, p = self.view, self.project
        self._rgb(cr, "axis")
        cr.set_line_width(1)
        cr.set_dash([12, 4, 2, 4])
        y0 = v.to_screen(0, 0)[1]
        cr.move_to(0, y0)
        cr.line_to(width, y0)
        cr.stroke()
        cr.set_dash([])
        # stepped chuck jaws behind the billet (20 mm, like the ALPHA-T drawing layer)
        zc, rb = -p.billet.length, p.billet.diameter / 2
        self._rgb(cr, "chuck")
        for i, step in enumerate((5, 10, 15)):
            x0, ytop = v.to_screen(2 * (rb + step), zc - 7 * i)
            x1, ybot = v.to_screen(2 * (rb - 5 + step - 5 * (i > 0)), zc - 7 * (i + 1))
            cr.rectangle(x1, ytop, x0 - x1, ybot - ytop)
            cr.fill()
        # billet
        self._rgb(cr, "billet")
        cr.set_dash([6, 4])
        xa, ya = v.to_screen(p.billet.diameter, 0)
        xb, yb = v.to_screen(-p.billet.diameter, -p.billet.length)
        cr.rectangle(xb, ya, xa - xb, yb - ya)
        cr.stroke()
        cr.set_dash([])
        # part profile: upper half bold, mirror faint
        for mirror, alpha, lw in ((-1, 0.25, 1.0), (1, 1.0, 2.2)):
            self._rgb(cr, "part", alpha)
            cr.set_line_width(lw)
            for g in p.geometry:
                if isinstance(g, Arc):
                    self._arc(cr, (g.a.x * mirror, g.a.z), (g.b.x * mirror, g.b.z), (g.centre.x * mirror, g.centre.z),
                              g.ccw == (mirror > 0))
                else:
                    cr.move_to(*v.to_screen(g.a.x * mirror, g.a.z))
                    cr.line_to(*v.to_screen(g.b.x * mirror, g.b.z))
                cr.stroke()
        self._paths(cr)

    def _arc(self, cr, a, b, c, ccw: bool) -> None:
        """G3/ccw is counter-clockwise with Z right and X up: decreasing cairo angle on screen (y down)."""
        v = self.view
        cx, cy = v.to_screen(*c)
        ax, ay = v.to_screen(*a)
        bx, by = v.to_screen(*b)
        cr.new_sub_path()
        (cr.arc_negative if ccw else cr.arc)(cx, cy, math.hypot(ax - cx, ay - cy),
                                             math.atan2(ay - cy, ax - cx), math.atan2(by - cy, bx - cx))

    def _paths(self, cr) -> None:
        sel = self.selected
        for s in self.segs:
            focus = sel is None or s.op_index == sel
            alpha = 1.0 if focus else 0.15
            lw = 2.0 if sel is not None and focus else 1.1
            if s.kind is MoveKind.DWELL:
                self._rgb(cr, "feed", alpha)
                cr.arc(*self.view.to_screen(*s.b), 3.5, 0, 2 * math.pi)
                cr.fill()
                continue
            self._rgb(cr, {MoveKind.RAPID: "rapid", MoveKind.THREAD: "thread"}.get(s.kind, "feed"), alpha)
            cr.set_line_width(lw)
            cr.set_dash([5, 4] if s.kind is MoveKind.RAPID else [])
            if s.centre is not None:
                self._arc(cr, s.a, s.b, s.centre, s.kind is MoveKind.ARC_CCW)
            else:
                cr.move_to(*self.view.to_screen(*s.a))
                cr.line_to(*self.view.to_screen(*s.b))
            cr.stroke()
        cr.set_dash([])
