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
/// the finger's speed at the lift is measured over its last this many ms at least
const VELOCITY_SPAN_MS: u32 = 16;
/// the drag keeps its press and this many last moves
const VELOCITY_MOVES: usize = 16;
/// a pen that moves further than this before the long press time is drawing or dragging, not holding
const PEN_SLOP_PX: f64 = 8.0;

/// the pen's pending long press: its timer and where the pen went down
type PenHold = RefCell<Option<(glib::SourceId, (f64, f64))>>;

pub fn source(controller: &impl IsA<gtk::EventController>) -> Source {
    match crate::input_mode::event_source(controller) {
        Some(gdk::InputSource::Touchscreen) => Source::Touch,
        // GTK 4 gives the eraser end this source too (its tool says Eraser): a pointer like the tip, erasing nothing
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
    /// the drag's press and last moves (event time ms, dx, dy) for the fling's velocity
    samples: RefCell<std::collections::VecDeque<(u32, f64, f64)>>,
    /// a pen (or a forced source's) press waiting to become a long press
    hold: PenHold,
}

impl Router {
    fn sample(&self, time: u32, dx: f64, dy: f64) {
        let mut s = self.samples.borrow_mut();
        s.push_back((time, dx, dy));
        if s.len() > VELOCITY_MOVES + 1 {
            s.remove(1); // the press stays first
        }
    }

    /// The finger's speed at the lift, px/s; zero when the events carry no time apart.
    fn velocity(&self) -> (f64, f64) {
        velocity(self.samples.borrow_mut().make_contiguous())
    }

    /// A long press by timer, for the pen and for a forced source (the touch long press is GTK's own).
    fn hold_start(self: &Rc<Self>, w: &LatheCanvas, x: f64, y: f64) {
        self.hold_stop();
        let delay = w.settings().gtk_long_press_time().max(1) as u64;
        let r = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(delay), glib::clone!(#[weak] w, move || {
            let Some(r) = r.upgrade() else { return };
            r.hold.take();
            r.long.set(true);
            w.long_press(x, y);
        }));
        *self.hold.borrow_mut() = Some((id, (x, y)));
    }

    fn hold_move(&self, x: f64, y: f64) {
        let start = self.hold.borrow().as_ref().map(|(_, p)| *p);
        if start.is_some_and(|(x0, y0)| (x - x0).hypot(y - y0) > PEN_SLOP_PX) {
            self.hold_stop();
        }
    }

    fn hold_stop(&self) {
        if let Some((id, _)) = self.hold.take() {
            id.remove();
        }
    }

    fn stop_fling(&self) {
        if let Some(id) = self.fling.take() {
            glib::g_debug!("alphalink-cam", "fling: stopped by a new gesture");
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
        r.samples.borrow_mut().clear();
        r.sample(g.current_event_time(), 0.0, 0.0);
        // a forced source on a mouse or a libei pointer (the test bus): its long press by timer, as the pen's
        let real = g.current_event_device().map(|d| d.source());
        if crate::input_mode::forced_source().is_some()
            && !matches!(real, Some(gdk::InputSource::Touchscreen | gdk::InputSource::Pen))
        {
            r.hold_start(&w, x, y);
        }
        // decided at press time by the hit test with the event's source radius (5.3): a handle moves, space pans
        r.editing.set(w.handle_hooks().is_some_and(|h| h.begin(x, y, source(g))));
        r.start.set((x, y));
    }));
    drag.connect_drag_update(glib::clone!(#[weak] w, #[strong] r, move |g, dx, dy| {
        r.sample(g.current_event_time(), dx, dy);
        let (x0, y0) = r.start.get();
        r.hold_move(x0 + dx, y0 + dy);
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
        r.hold_stop();
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

    // the middle button drags the view, also over an element or a handle (POINTER_KEYBOARD.md 3)
    let middle = gtk::GestureDrag::builder().button(2).build();
    let last = Rc::new(std::cell::Cell::new((0.0, 0.0)));
    middle.connect_drag_begin(glib::clone!(#[strong] r, #[strong] last, move |_, _, _| {
        r.stop_fling();
        last.set((0.0, 0.0));
    }));
    middle.connect_drag_update(glib::clone!(#[weak] w, #[strong] last, move |_, dx, dy| {
        let (lx, ly) = last.replace((dx, dy));
        w.state_mut().view.pan(dx - lx, dy - ly);
        w.user_view_change();
    }));
    w.add_controller(middle);

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
    swipe.connect_swipe(glib::clone!(#[weak] w, #[strong] r, move |_, vx, vy| {
        // GTK's swipe velocity came out far below the finger's (the e2e flick of 200 px at 3000 px/s carried only
        // 58 px further): the finger's own speed over its last moves wins when higher
        let (ox, oy) = r.velocity();
        glib::g_debug!("alphalink-cam", "fling: GTK {:.0} px/s, moves {:.0} px/s over {:?}", vx.hypot(vy),
            ox.hypot(oy), r.samples.borrow());
        let (vx, vy) = if ox.hypot(oy) > vx.hypot(vy) { (ox, oy) } else { (vx, vy) };
        fling(&w, &r, vx, vy)
    }));
    w.add_controller(swipe);

    // long press (touch): the context menu at the finger (TOUCH.md 4)
    let hold = gtk::GestureLongPress::builder().touch_only(true).build();
    hold.connect_pressed(glib::clone!(#[weak] w, #[strong] r, move |_, x, y| {
        r.long.set(true);
        w.long_press(x, y);
    }));
    w.add_controller(hold);

    // long press with the pen: the same menu, so a pen without a barrel button reaches it (POINTER_KEYBOARD.md 10).
    // A stylus-only gesture, as a long press that also listens to the mouse upsets the mouse's own gestures.
    let pen = gtk::GestureStylus::new();
    pen.connect_down(glib::clone!(#[weak] w, #[strong] r, move |_, x, y| r.hold_start(&w, x, y)));
    pen.connect_motion(glib::clone!(#[strong] r, move |_, x, y| r.hold_move(x, y)));
    pen.connect_up(glib::clone!(#[strong] r, move |_, _, _| r.hold_stop()));
    w.add_controller(pen);

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
    let start = Instant::now();
    let travel = Cell::new(0.0);
    let state = Cell::new((vx, vy, start));
    let weak_r = Rc::downgrade(r);
    let id = glib::timeout_add_local(Duration::from_millis(16), glib::clone!(#[weak] w,
        #[upgrade_or] glib::ControlFlow::Break, move || {
            let (mut vx, mut vy, t) = state.get();
            let now = Instant::now();
            let dt = (now - t).as_secs_f64();
            w.state_mut().view.pan(vx * dt, vy * dt);
            travel.set(travel.get() + vx.hypot(vy) * dt);
            let decay = FLING_DECAY.powf(dt / 0.016);
            vx *= decay;
            vy *= decay;
            state.set((vx, vy, now));
            w.user_view_change();
            if vx.abs() < FLING_MIN && vy.abs() < FLING_MIN {
                glib::g_debug!("alphalink-cam", "fling: {:.0} px in {:?}", travel.get(), start.elapsed());
                if let Some(r) = weak_r.upgrade() {
                    r.fling.take(); // returning Break removes the source
                }
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        }));
    r.fling.replace(Some(id));
}

/// px/s over the shortest stretch of at least [`VELOCITY_SPAN_MS`] that ends at the last move, `samples` being
/// (event time ms, dx, dy) with the press first: a finger that rested before the flick does not average the speed
/// down, and moves that arrive in a burst with (nearly) equal times reach back to an earlier one. The whole drag when
/// it is shorter than that.
fn velocity(samples: &[(u32, f64, f64)]) -> (f64, f64) {
    let Some(&(t1, x1, y1)) = samples.last() else { return (0.0, 0.0) };
    let Some(&(t0, x0, y0)) = samples.iter().rev().find(|(t, _, _)| t1.wrapping_sub(*t) >= VELOCITY_SPAN_MS)
        .or(samples.first()) else { return (0.0, 0.0) };
    let dt = t1.wrapping_sub(t0) as f64 / 1000.0;
    if dt <= 0.0 {
        return (0.0, 0.0);
    }
    ((x1 - x0) / dt, (y1 - y0) / dt)
}

#[cfg(test)]
mod tests {
    #[test]
    fn velocity_is_the_last_moves() {
        // rested 80 ms after the press, then 50 px per 16 ms
        let s = [(0, 0.0, 0.0), (80, 0.0, 0.0), (96, 50.0, 0.0), (112, 100.0, 0.0), (128, 150.0, 0.0), (144, 200.0, 0.0)];
        let (vx, vy) = super::velocity(&s);
        assert!((vx - 3125.0).abs() < 1.0 && vy == 0.0, "{vx}");
        // the press and a single move
        assert_eq!(super::velocity(&[(0, 0.0, 0.0), (20, 0.0, 40.0)]), (0.0, 2000.0));
        // moves that arrive in a burst: back to the move before it
        let s = [(0, 0.0, 0.0), (16, 50.0, 0.0), (32, 100.0, 0.0), (63, 150.0, 0.0), (64, 200.0, 0.0)];
        assert_eq!(super::velocity(&s), (3125.0, 0.0));
        let s = [(0, 0.0, 0.0), (64, 50.0, 0.0), (64, 100.0, 0.0), (64, 200.0, 0.0)];
        assert_eq!(super::velocity(&s), (3125.0, 0.0));
        // no time apart at all
        assert_eq!(super::velocity(&[(5, 0.0, 0.0), (5, 9.0, 0.0), (5, 18.0, 0.0)]), (0.0, 0.0));
        assert_eq!(super::velocity(&[]), (0.0, 0.0));
    }

    #[test]
    fn shift_scroll() {
        assert_eq!(super::shift_scroll(0.0, 2.0, true), (2.0, 0.0));
        assert_eq!(super::shift_scroll(1.0, 2.0, true), (1.0, 2.0));
        assert_eq!(super::shift_scroll(0.0, 2.0, false), (0.0, 2.0));
    }
}
