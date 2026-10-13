"""Simulation transport (TOUCH.md 7.2): step through the posted moves, pass by pass, or scrub.

Buttons: first, previous pass, play/pause, next pass, last. The scale scrubs move by move; the label shows the
operation, pass and the move's PRG line, and the Program page highlight follows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .canvas.paths import pass_starts  # noqa: E402

MOVES_PER_SECOND = 25


class SimulationBar(Gtk.Box):
    def __init__(self, canvas, on_move=None):
        super().__init__(spacing=6, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        self.add_css_class("sim-bar")
        self.c, self.on_move = canvas, on_move
        self._play = 0
        buttons = [("media-skip-backward-symbolic", "First move", lambda: self.goto(0)),
                   ("media-seek-backward-symbolic", "Previous pass", lambda: self.step_pass(-1)),
                   (None, None, None),
                   ("media-seek-forward-symbolic", "Next pass", lambda: self.step_pass(1)),
                   ("media-skip-forward-symbolic", "Show all", lambda: self.goto(None))]
        for icon, tip, cb in buttons:
            if icon is None:
                self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Play / Pause")
                self.play_button.connect("clicked", lambda *_: self.toggle_play())
                self.append(self.play_button)
                continue
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda _b, cb=cb: cb())
            b.add_css_class("flat")
            self.append(b)
        self.scale = Gtk.Scale(hexpand=True, draw_value=False, adjustment=Gtk.Adjustment(upper=1, step_increment=1))
        self.scale.connect("value-changed", self._scrubbed)
        self.append(self.scale)
        self.label = Gtk.Label(width_chars=26, xalign=0)
        self.label.add_css_class("caption")
        self.label.add_css_class("numeric")
        self.append(self.label)
        self._guard = False
        self.reset()

    def reset(self) -> None:
        self.stop()
        n = len(self.c.segs)
        self._guard = True
        self.scale.get_adjustment().configure(n, 0, n, 1, 10, 0)
        self._guard = False
        self.c.sim_index = None
        self._label()

    def goto(self, index: int | None) -> None:
        n = len(self.c.segs)
        self.c.sim_index = None if index is None or index >= n else max(0, index)
        self._guard = True
        self.scale.set_value(n if self.c.sim_index is None else self.c.sim_index)
        self._guard = False
        self._label()
        self.c.queue_draw()

    def step_pass(self, direction: int) -> None:
        starts = pass_starts(self.c.segs) + [len(self.c.segs)]
        cur = len(self.c.segs) if self.c.sim_index is None else self.c.sim_index
        if direction > 0:
            nxt = next((s for s in starts if s > cur), None)
        else:
            nxt = next((s for s in reversed(starts) if s < cur), 0)
        self.goto(nxt)

    def toggle_play(self) -> None:
        if self._play:
            self.stop()
            return
        if self.c.sim_index is None:
            self.goto(0)
        self.play_button.set_icon_name("media-playback-pause-symbolic")
        self._play = GLib.timeout_add(int(1000 / MOVES_PER_SECOND), self._tick)

    def stop(self) -> None:
        if self._play:
            GLib.source_remove(self._play)
            self._play = 0
        self.play_button.set_icon_name("media-playback-start-symbolic")

    def _tick(self) -> bool:
        i = (self.c.sim_index or 0) + 1
        if i >= len(self.c.segs):
            self.goto(None)
            self._play = 0
            self.play_button.set_icon_name("media-playback-start-symbolic")
            return False
        self.goto(i)
        return True

    def _scrubbed(self, scale) -> None:
        if self._guard:
            return
        self.stop()
        v = int(round(scale.get_value()))
        self.c.sim_index = None if v >= len(self.c.segs) else v
        self._label()
        self.c.queue_draw()

    def _label(self) -> None:
        segs, i = self.c.segs, self.c.sim_index
        if not segs:
            self.label.set_label("no moves")
            return
        if i is None:
            self.label.set_label(f"{len(segs)} moves")
            return
        s = segs[max(i - 1, 0)]
        op = "tool block" if s.op_index is None else f"op {s.op_index + 1}"
        pas = f" · pass {s.pass_no}" if s.pass_no else ""
        self.label.set_label(f"{i}/{len(segs)} · {op}{pas} · line {s.line}")
        if self.on_move:
            self.on_move(s.line)
