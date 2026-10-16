"""Simulation transport (TOUCH.md 7.2): step through the posted moves, pass by pass, or scrub.

Buttons: first, previous pass, play/pause, next pass, last. The scale scrubs move by move; the label shows the
operation, pass and the move's PRG line, and the Program page highlight follows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .canvas.paths import pass_starts  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .ui import template  # noqa: E402

MOVES_PER_SECOND = 25
SPEEDS = (1, 2, 5, 10, 0.5)  # playback speed, cycled by the speed button


@template("simulation")
class SimulationBar(Gtk.Box):
    __gtype_name__ = "AlphaLinkSimulationBar"

    first_button = Gtk.Template.Child()
    previous_button = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()
    last_button = Gtk.Template.Child()
    scale = Gtk.Template.Child()
    to_op = Gtk.Template.Child()
    speed_button = Gtk.Template.Child()
    label = Gtk.Template.Child()

    _guard = True  # until __init__ has the canvas: the scale's first value is not a scrub

    def __init__(self, canvas, on_move=None):
        super().__init__()
        self.c, self.on_move = canvas, on_move
        self._play = 0
        self._buttons = [self.first_button, self.previous_button, self.play_button, self.next_button,
                         self.last_button, self.to_op]
        self.speed = 1
        self.selected_op = None
        self._guard = False
        self.reset()

    @Gtk.Template.Callback()
    def _first(self, *_a) -> None:
        self.goto(0)

    @Gtk.Template.Callback()
    def _previous_pass(self, *_a) -> None:
        self.step_pass(-1)

    @Gtk.Template.Callback()
    def _play_clicked(self, *_a) -> None:
        self.toggle_play()

    @Gtk.Template.Callback()
    def _next_pass(self, *_a) -> None:
        self.step_pass(1)

    @Gtk.Template.Callback()
    def _show_all(self, *_a) -> None:
        self.goto(None)

    @Gtk.Template.Callback()
    def _to_op_clicked(self, *_a) -> None:
        self.goto_op(self.selected_op)

    @Gtk.Template.Callback()
    def _speed_clicked(self, *_a) -> None:
        self.next_speed()

    def reset(self) -> None:
        self.stop()
        n = len(self.c.segs)
        self._guard = True
        self.scale.get_adjustment().configure(n, 0, n, 1, 10, 0)
        self._guard = False
        self.c.sim_index = None
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

    @Gtk.Template.Callback()
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
