"""Simulation transport (TOUCH.md 7.2): step through the posted moves, pass by pass, or scrub.

Buttons: first, previous pass, play/pause, next pass, last. The scale scrubs move by move; the label shows the
operation, pass and the move's PRG line, and the Program page highlight follows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Pango, GLib, Gtk  # noqa: E402

from .canvas.paths import pass_starts  # noqa: E402
from .i18n import _, ngettext  # noqa: E402

MOVES_PER_SECOND = 25
SPEEDS = (1, 2, 5, 10, 0.5)  # playback speed, cycled by the speed button


class SimulationBar(Gtk.Box):
    def __init__(self, canvas, on_move=None):
        super().__init__(spacing=6, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        self.add_css_class("sim-bar")
        self.c, self.on_move = canvas, on_move
        self._play = 0
        self._buttons = []
        buttons = [("media-skip-backward-symbolic", _("First Move"), lambda: self.goto(0)),
                   ("media-seek-backward-symbolic", _("Previous Pass"), lambda: self.step_pass(-1)),
                   (None, None, None),
                   ("media-seek-forward-symbolic", _("Next Pass"), lambda: self.step_pass(1)),
                   ("media-skip-forward-symbolic", _("Show All"), lambda: self.goto(None))]
        for icon, tip, cb in buttons:
            if icon is None:
                self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text=_("Play / Pause"))
                self.play_button.connect("clicked", lambda *_: self.toggle_play())
                self._buttons.append(self.play_button)
                self.append(self.play_button)
                continue
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            self._buttons.append(b)
            b.connect("clicked", lambda _b, cb=cb: cb())
            b.add_css_class("flat")
            self.append(b)
        self.scale = Gtk.Scale(hexpand=True, draw_value=False, adjustment=Gtk.Adjustment(upper=1, step_increment=1))
        self.scale.connect("value-changed", self._scrubbed)
        self.append(self.scale)
        # the selected operation's first move ("run to operation"), and the playback speed
        self.to_op = Gtk.Button(icon_name="find-location-symbolic", tooltip_text=_("Start of the Selected Operation"),
                                sensitive=False)
        self.to_op.add_css_class("flat")
        self.to_op.connect("clicked", lambda *_: self.goto_op(self.selected_op))
        self._buttons.append(self.to_op)
        self.append(self.to_op)
        self.speed = 1
        self.speed_button = Gtk.Button(label="1×", tooltip_text=_("Playback Speed"))
        self.speed_button.add_css_class("flat")
        self.speed_button.add_css_class("numeric")
        self.speed_button.connect("clicked", lambda *_: self.next_speed())
        self.append(self.speed_button)
        self.selected_op = None
        self.label = Gtk.Label(width_chars=12, max_width_chars=40, xalign=0, ellipsize=Pango.EllipsizeMode.END)
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
        if hasattr(self, "to_op"):
            self.select_op(self.selected_op)  # new moves: is the selected operation still in them
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

    def select_op(self, index: int | None) -> None:
        self.selected_op = index
        self.to_op.set_sensitive(index is not None and any(sg.op_index == index for sg in self.c.segs))

    def goto_op(self, index: int | None) -> None:
        """Jump to the operation's first move (shown and named in the label); Play or Next Pass runs on from it."""
        first = next((i for i, sg in enumerate(self.c.segs) if sg.op_index == index), None)
        if first is not None:
            self.goto(first + 1)

    def next_speed(self) -> None:
        self.speed = SPEEDS[(SPEEDS.index(self.speed) + 1) % len(SPEEDS)]
        self.speed_button.set_label(f"{self.speed:g}×")
        if self._play:  # playing: carry on at the new speed
            GLib.source_remove(self._play)
            self._play = GLib.timeout_add(max(1, int(1000 / (MOVES_PER_SECOND * self.speed))), self._tick)

    def toggle_play(self) -> None:
        if self._play:
            self.stop()
            return
        if self.c.sim_index is None:
            self.goto(0)
        self.play_button.set_icon_name("media-playback-pause-symbolic")
        self._play = GLib.timeout_add(max(1, int(1000 / (MOVES_PER_SECOND * self.speed))), self._tick)

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
        for b in self._buttons:  # nothing to play or step through without moves
            b.set_sensitive(bool(segs))
        if not segs:
            self.label.set_label(_("no moves"))
            return
        if i is None:
            self.label.set_label(ngettext("{n} move", "{n} moves", len(segs)).format(n=len(segs)))
            return
        s = segs[max(i - 1, 0)]
        op = _("tool block") if s.op_index is None else _("op {n}").format(n=s.op_index + 1)
        pas = " · " + _("pass {n}").format(n=s.pass_no) if s.pass_no else ""
        self.label.set_label(_("{i}/{total} · {op}{pass_} · line {line}").format(
            i=i, total=len(segs), op=op, pass_=pas, line=s.line))
        if self.on_move:
            self.on_move(s.line)
