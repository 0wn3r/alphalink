"""InputRouter: owns the canvas' GTK controllers and turns events into view changes and taps (TOUCH.md 4, 5.3).

  one-finger drag / mouse drag   pan; a fling continues with decelerating motion
  two-finger pinch               zoom about the fingers' centre, and pan with the centre (pinch wins over drag)
  double tap / double click      fit; again returns to the previous view
  tap / click                    select the toolpath under it, with a radius from the event's source
  wheel                          pan; Ctrl+wheel or touchpad pinch-scroll zooms
Rotation is ignored on purpose (T5)."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

FLING_DECAY = 0.92  # velocity factor per 16 ms frame
FLING_MIN = 40.0  # px/s


def source_name(controller: Gtk.EventController) -> str:
    dev = controller.get_current_event_device()
    src = dev.get_source() if dev is not None else None
    if src == Gdk.InputSource.TOUCHSCREEN:
        return "touch"
    if src == Gdk.InputSource.PEN:
        return "pen"
    return "mouse"


class InputRouter:
    def __init__(self, widget, on_view_changed, on_tap, on_fit, on_pointer):
        self.w = widget
        self.on_view_changed, self.on_tap, self.on_fit, self.on_pointer = on_view_changed, on_tap, on_fit, on_pointer
        self._pinching = False
        self._fling_id = 0

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        widget.add_controller(drag)
        self._drag = drag
        self._last_drag = (0.0, 0.0)

        zoom = Gtk.GestureZoom()
        zoom.connect("begin", self._zoom_begin)
        zoom.connect("scale-changed", self._zoom_changed)
        zoom.connect("end", lambda *_: setattr(self, "_pinching", False))
        widget.add_controller(zoom)
        self._zoom = zoom

        swipe = Gtk.GestureSwipe()
        swipe.connect("swipe", self._fling)
        widget.add_controller(swipe)

        click = Gtk.GestureClick()
        click.connect("released", self._released)
        widget.add_controller(click)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scroll)
        widget.add_controller(scroll)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda c, x, y: self.on_pointer(x, y))
        widget.add_controller(motion)

    # --- pan --------------------------------------------------------------------------------------------

    def _drag_begin(self, gesture, x, y):
        self._stop_fling()
        self._last_drag = (0.0, 0.0)

    def _drag_update(self, gesture, dx, dy):
        if self._pinching:
            return
        lx, ly = self._last_drag
        self._last_drag = (dx, dy)
        self.w.view.pan(dx - lx, dy - ly)
        self.on_view_changed()

    # --- pinch ------------------------------------------------------------------------------------------

    def _zoom_begin(self, gesture, sequence):
        self._pinching = True
        self._stop_fling()
        self._drag.reset()  # pinch always wins (5.3)
        ok, cx, cy = gesture.get_bounding_box_center()
        self._pinch_scale = 1.0
        self._pinch_centre = (cx, cy)

    def _zoom_changed(self, gesture, scale):
        ok, cx, cy = gesture.get_bounding_box_center()
        if not ok:
            return
        px, py = self._pinch_centre
        self.w.view.pan(cx - px, cy - py)  # two fingers moving together pan
        self.w.view.zoom_at(cx, cy, scale / self._pinch_scale)
        self._pinch_scale, self._pinch_centre = scale, (cx, cy)
        self.on_view_changed()

    # --- fling ------------------------------------------------------------------------------------------

    def _fling(self, gesture, vx, vy):
        if self._pinching or (abs(vx) < FLING_MIN and abs(vy) < FLING_MIN):
            return
        self._stop_fling()
        state = {"vx": vx, "vy": vy, "t": time.monotonic()}

        def tick():
            now = time.monotonic()
            dt = now - state["t"]
            state["t"] = now
            self.w.view.pan(state["vx"] * dt, state["vy"] * dt)
            decay = FLING_DECAY ** (dt / 0.016)
            state["vx"] *= decay
            state["vy"] *= decay
            self.on_view_changed()
            if abs(state["vx"]) < FLING_MIN and abs(state["vy"]) < FLING_MIN:
                self._fling_id = 0
                return False
            return True

        self._fling_id = GLib.timeout_add(16, tick)

    def _stop_fling(self):
        if self._fling_id:
            GLib.source_remove(self._fling_id)
            self._fling_id = 0

    # --- taps -------------------------------------------------------------------------------------------

    def _released(self, gesture, n_press, x, y):
        if n_press == 2:
            self.on_fit()
            return
        offset = self._drag.get_offset()
        if offset[0] and (abs(offset[1]) > 8 or abs(offset[2]) > 8):
            return  # it was a pan
        self.on_tap(x, y, source_name(gesture))

    # --- wheel / touchpad -------------------------------------------------------------------------------

    def _scroll(self, ctl, dx, dy):
        state = ctl.get_current_event_state()
        unit = ctl.get_unit()
        step = 40.0 if unit == Gdk.ScrollUnit.WHEEL else 1.0
        if state & Gdk.ModifierType.CONTROL_MASK:
            px, py = self.w.pointer
            self.w.view.zoom_at(px, py, 0.87 if dy > 0 else 1.15)
        else:
            self.w.view.pan(-dx * step, -dy * step)
        self.on_view_changed()
        return True
