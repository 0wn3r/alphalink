//! Router: owns the canvas' GTK controllers and turns events into view changes and taps (TOUCH.md 4, 5.3).
//!
//!   one-finger drag / mouse drag   pan; a fling continues with decelerating motion
//!   two-finger pinch               zoom about the fingers' centre, and pan with the centre (pinch wins over drag)
//!   double tap / double click      fit; again returns to the previous view
//!   tap / click                    select the toolpath under it, with a radius from the event's source
//!   wheel                          pan up/down; Shift+wheel pans sideways; Ctrl+wheel or touchpad pinch-scroll
//!                                  zooms
//! Rotation is ignored on purpose (T5).

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::{Duration, Instant};

use gtk::prelude::*;
use gtk::{gdk, glib};

use super::hittest::Source;
use super::widget::LatheCanvas;

/// velocity factor per 16 ms frame
const FLING_DECAY: f64 = 0.92;
/// px/s
const FLING_MIN: f64 = 40.0;

pub fn source(controller: &impl IsA<gtk::EventController>) -> Source {
    match controller.current_event_device().map(|d| d.source()) {
        Some(gdk::InputSource::Touchscreen) => Source::Touch,
        Some(gdk::InputSource::Pen) => Source::Pen,
        _ => Source::Mouse,
    }
}

/// Shift turns a vertical wheel into a horizontal scroll (desktop convention; GTK does not do it for us). A
/// touchpad's own sideways movement (dx) is left alone.
pub fn shift_scroll(dx: f64, dy: f64, shift: bool) -> (f64, f64) {
    if shift && dx == 0.0 { (dy, 0.0) } else { (dx, dy) }
}

#[derive(Default)]
pub struct Router {
    /// a long press happened: the release that ends it is not a tap
    long: Cell<bool>,
    pinching: Cell<bool>,
    fling: RefCell<Option<glib::SourceId>>,
    /// this drag moves a point (a handle was under it) instead of panning
    editing: Cell<bool>,
    /// the swipe that ends an edit is no fling
    edited: Cell<bool>,
    start: Cell<(f64, f64)>,
    last_drag: Cell<(f64, f64)>,
    pinch_scale: Cell<f64>,
    pinch_centre: Cell<(f64, f64)>,
    last_tap: Cell<Option<(f64, f64)>>,
    drag: RefCell<Option<gtk::GestureDrag>>,
}

impl Router {
    fn stop_fling(&self) {
        if let Some(id) = self.fling.take() {
            id.remove();
        }
    }
}

pub fn install(w: &LatheCanvas) -> Rc<Router> {
    let r = Rc::new(Router::default());

    let drag = gtk::GestureDrag::new();
    drag.connect_drag_begin(glib::clone!(#[weak] w, #[strong] r, move |g, x, y| {
        r.stop_fling();
        r.last_drag.set((0.0, 0.0));
        // decided at press time by the hit test with the event's source radius (5.3): a handle moves, space pans
        r.editing.set(w.handle_hooks().is_some_and(|h| h.begin(x, y, source(g))));
        r.start.set((x, y));
    }));
    drag.connect_drag_update(glib::clone!(#[weak] w, #[strong] r, move |_, dx, dy| {
        if r.editing.get() {
            let (x0, y0) = r.start.get();
            if let Some(h) = w.handle_hooks() {
                h.drag(x0 + dx, y0 + dy);
            }
            return;
        }
        if r.pinching.get() {
            return;
        }
        let (lx, ly) = r.last_drag.replace((dx, dy));
        w.state_mut().view.pan(dx - lx, dy - ly);
        w.user_view_change();
    }));
    drag.connect_drag_end(glib::clone!(#[weak] w, #[strong] r, move |_, _, _| {
        if r.editing.get() {
            r.editing.set(false);
            r.edited.set(true);
            if let Some(h) = w.handle_hooks() {
                h.end();
            }
        }
    }));
    w.add_controller(drag.clone());
    r.drag.replace(Some(drag.clone()));

    let zoom = gtk::GestureZoom::new();
    zoom.connect_begin(glib::clone!(#[weak] w, #[strong] r, #[weak] drag, move |g, _| {
        if r.editing.get() {
            // a second finger: the half-made move is dropped, pinch wins (5.3)
            r.editing.set(false);
            if let Some(h) = w.handle_hooks() {
                h.cancel();
            }
        }
        r.pinching.set(true);
        r.stop_fling();
        drag.reset(); // pinch always wins (5.3)
        r.pinch_scale.set(1.0);
        r.pinch_centre.set(g.bounding_box_center().unwrap_or_default());
    }));
    zoom.connect_scale_changed(glib::clone!(#[weak] w, #[strong] r, move |g, scale| {
        let Some((cx, cy)) = g.bounding_box_center() else {
            return;
        };
        let (px, py) = r.pinch_centre.get();
        {
            let mut s = w.state_mut();
            s.view.pan(cx - px, cy - py); // two fingers moving together pan
            s.view.zoom_at(cx, cy, scale / r.pinch_scale.get());
        }
        r.pinch_scale.set(scale);
        r.pinch_centre.set((cx, cy));
        w.user_view_change();
    }));
    zoom.connect_end(glib::clone!(#[strong] r, move |_, _| r.pinching.set(false)));
    w.add_controller(zoom);

    let swipe = gtk::GestureSwipe::new();
    swipe.connect_swipe(glib::clone!(#[weak] w, #[strong] r, move |_, vx, vy| fling(&w, &r, vx, vy)));
    w.add_controller(swipe);

    // long press (touch): the context menu at the finger (TOUCH.md 4)
    let hold = gtk::GestureLongPress::builder().touch_only(true).build();
    hold.connect_pressed(glib::clone!(#[weak] w, #[strong] r, move |_, x, y| {
        r.long.set(true);
        w.long_press(x, y);
    }));
    w.add_controller(hold);

    // right click: the same context menu as a long press (HIG: Menu key and Shift+F10 too, drawing tools)
    let menu = gtk::GestureClick::builder().button(3).build();
    menu.connect_pressed(glib::clone!(#[weak] w, move |_, _, x, y| {
        w.grab_focus();
        w.long_press(x, y);
    }));
    w.add_controller(menu);

    let click = gtk::GestureClick::new();
    // the cursor keys and letters act on the drawing
    click.connect_pressed(glib::clone!(#[weak] w, move |_, _, _, _| {
        w.grab_focus();
    }));
    click.connect_released(glib::clone!(#[weak] w, #[strong] r, move |g, n_press, x, y| {
        if r.long.replace(false) {
            return;
        }
        // GTK counts quick touch taps as a double tap wherever they land; only two taps on one spot are one (quick
        // taps along a profile with the Line tool are points, found by tests/e2e)
        let last = r.last_tap.replace(Some((x, y)));
        if n_press >= 2 && last.is_some_and(|(lx, ly)| (lx - x).abs() + (ly - y).abs() < 24.0) {
            w.toggle_fit();
            return;
        }
        if let Some(drag) = r.drag.borrow().as_ref()
            && let Some((ox, oy)) = drag.offset()
            && (ox.abs() > 8.0 || oy.abs() > 8.0)
        {
            return; // it was a pan
        }
        w.tap(x, y, source(g));
    }));
    w.add_controller(click);

    let scroll = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::BOTH_AXES);
    scroll.connect_scroll(glib::clone!(#[weak] w, #[upgrade_or] glib::Propagation::Proceed, move |ctl, dx, dy| {
        let state = ctl.current_event_state();
        let step = if ctl.unit() == gdk::ScrollUnit::Wheel { 40.0 } else { 1.0 };
        {
            let mut s = w.state_mut();
            if state.contains(gdk::ModifierType::CONTROL_MASK) {
                let (px, py) = s.pointer;
                s.view.zoom_at(px, py, if dy > 0.0 { 0.87 } else { 1.15 });
            } else {
                let (dx, dy) = shift_scroll(dx, dy, state.contains(gdk::ModifierType::SHIFT_MASK));
                s.view.pan(-dx * step, -dy * step);
            }
        }
        w.user_view_change();
        glib::Propagation::Stop
    }));
    w.add_controller(scroll);

    let motion = gtk::EventControllerMotion::new();
    motion.connect_motion(glib::clone!(#[weak] w, move |_, x, y| w.pointer_moved(x, y)));
    // the pointer's place is known from entering too: a pointer that came in and has not moved since must not look
    // like it sits at 0,0 (the cursor keys compare against it; found by tests/e2e with libei input)
    motion.connect_enter(glib::clone!(#[weak] w, move |_, x, y| w.pointer_moved(x, y)));
    w.add_controller(motion);
    r
}

fn fling(w: &LatheCanvas, r: &Rc<Router>, vx: f64, vy: f64) {
    if r.editing.get() || r.edited.replace(false) {
        return;
    }
    if r.pinching.get() || (vx.abs() < FLING_MIN && vy.abs() < FLING_MIN) {
        return;
    }
    r.stop_fling();
    let state = Cell::new((vx, vy, Instant::now()));
    let weak_r = Rc::downgrade(r);
    let id = glib::timeout_add_local(Duration::from_millis(16), glib::clone!(#[weak] w,
        #[upgrade_or] glib::ControlFlow::Break, move || {
            let (mut vx, mut vy, t) = state.get();
            let now = Instant::now();
            let dt = (now - t).as_secs_f64();
            w.state_mut().view.pan(vx * dt, vy * dt);
            let decay = FLING_DECAY.powf(dt / 0.016);
            vx *= decay;
            vy *= decay;
            state.set((vx, vy, now));
            w.user_view_change();
            if vx.abs() < FLING_MIN && vy.abs() < FLING_MIN {
                if let Some(r) = weak_r.upgrade() {
                    r.fling.take(); // returning Break removes the source
                }
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        }));
    r.fling.replace(Some(id));
}

#[cfg(test)]
mod tests {
    #[test]
    fn shift_scroll() {
        assert_eq!(super::shift_scroll(0.0, 2.0, true), (2.0, 0.0));
        assert_eq!(super::shift_scroll(1.0, 2.0, true), (1.0, 2.0));
        assert_eq!(super::shift_scroll(0.0, 2.0, false), (0.0, 2.0));
    }
}
