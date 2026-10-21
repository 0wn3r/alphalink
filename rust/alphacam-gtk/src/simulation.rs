//! Simulation transport (TOUCH.md 7.2): step through the posted moves, pass by pass, or scrub (simulation.py,
//! ui/simulation.blp).
//!
//! Buttons: first, previous pass, play/pause, next pass, last. The scale scrubs move by move; the label shows the
//! operation, pass and the move's PRG line, and the Program page highlight follows.

use std::cell::{Cell, OnceCell, RefCell};
use std::time::Duration;

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::moves::pass_starts;
use alphacam::py::fmt_g;
use gtk::glib;

use crate::canvas::LatheCanvas;
use crate::i18n::{fill, gettext, ngettext, tr};

pub const MOVES_PER_SECOND: f64 = 25.0;
/// playback speed, cycled by the speed button
pub const SPEEDS: [f64; 5] = [1.0, 2.0, 5.0, 10.0, 0.5];

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/simulation.ui")]
    pub struct SimulationBar {
        #[template_child]
        pub first_button: TemplateChild<gtk::Button>,
        #[template_child]
        pub previous_button: TemplateChild<gtk::Button>,
        #[template_child]
        pub play_button: TemplateChild<gtk::Button>,
        #[template_child]
        pub next_button: TemplateChild<gtk::Button>,
        #[template_child]
        pub last_button: TemplateChild<gtk::Button>,
        #[template_child]
        pub scale: TemplateChild<gtk::Scale>,
        #[template_child]
        pub to_op: TemplateChild<gtk::Button>,
        #[template_child]
        pub speed_button: TemplateChild<gtk::Button>,
        #[template_child]
        pub label: TemplateChild<gtk::Label>,

        pub canvas: OnceCell<LatheCanvas>,
        pub on_move: OnceCell<Box<dyn Fn(usize)>>,
        pub play: RefCell<Option<glib::SourceId>>,
        pub speed: Cell<f64>,
        pub selected_op: Cell<Option<usize>>,
        /// setting the scale from code: its value-changed is not a scrub
        pub guard: Cell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SimulationBar {
        const NAME: &'static str = "AlphaLinkSimulationBar";
        type Type = super::SimulationBar;
        type ParentType = gtk::Box;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            super::bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for SimulationBar {
        fn dispose(&self) {
            if let Some(id) = self.play.take() {
                id.remove();
            }
        }
    }
    impl WidgetImpl for SimulationBar {}
    impl BoxImpl for SimulationBar {}
}

glib::wrapper! {
    pub struct SimulationBar(ObjectSubclass<imp::SimulationBar>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

/// Binds the handlers ui/simulation.blp names. The template scope passes (emitter, signal arguments..., template
/// object); the handlers here need only the last, the bar itself.
fn bind_callbacks(klass: &mut <imp::SimulationBar as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&SimulationBar));
    let handlers: [Handler; 8] = [
        ("_first", |bar| bar.goto(Some(0))),
        ("_previous_pass", |bar| bar.step_pass(-1)),
        ("_play_clicked", |bar| bar.toggle_play()),
        ("_next_pass", |bar| bar.step_pass(1)),
        ("_show_all", |bar| bar.goto(None)),
        ("_to_op_clicked", |bar| bar.goto_op(bar.imp().selected_op.get())),
        ("_speed_clicked", |bar| bar.next_speed()),
        ("_scrubbed", |bar| bar.scrubbed()),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            if let Some(bar) = values.last().and_then(|v| v.get::<SimulationBar>().ok()) {
                handler(&bar);
            }
            None
        });
    }
}

impl SimulationBar {
    /// `on_line(n)`: the simulation reached PRG line n (the Program page follows)
    pub fn new(canvas: &LatheCanvas, on_line: impl Fn(usize) + 'static) -> Self {
        let bar: Self = glib::Object::new();
        let imp = bar.imp();
        imp.speed.set(1.0);
        let _ = imp.canvas.set(canvas.clone());
        let _ = imp.on_move.set(Box::new(on_line));
        bar.reset();
        bar
    }

    fn canvas(&self) -> &LatheCanvas {
        self.imp().canvas.get().expect("SimulationBar::new sets the canvas")
    }

    fn moves(&self) -> usize {
        self.canvas().state().segs.len()
    }

    fn set_sim(&self, index: Option<usize>) {
        self.canvas().state_mut().sim_index = index;
    }

    fn set_scale(&self, value: f64) {
        let imp = self.imp();
        imp.guard.set(true);
        imp.scale.set_value(value);
        imp.guard.set(false);
    }

    /// New moves (a re-post): stop, show everything.
    pub fn reset(&self) {
        self.stop();
        let imp = self.imp();
        let n = self.moves() as f64;
        imp.guard.set(true);
        // PgUp / PgDn on the slider: a tenth of the moves (POINTER_KEYBOARD.md 7)
        imp.scale.adjustment().configure(n, 0.0, n, 1.0, (n / 10.0).round().max(1.0), 0.0);
        imp.guard.set(false);
        self.set_sim(None);
        self.select_op(imp.selected_op.get()); // new moves: is the selected operation still in them
        self.update_label();
    }

    /// Show the first `index` moves done (None: everything, without the stock; all `n` moves: the finished stock).
    pub fn goto(&self, index: Option<usize>) {
        let n = self.moves();
        let sim = index.filter(|i| *i <= n);
        self.set_sim(sim);
        self.set_scale(sim.unwrap_or(n) as f64);
        self.update_label();
        self.canvas().queue_draw();
    }

    /// To the next (1) or previous (-1) pass start.
    pub fn step_pass(&self, direction: i32) {
        let (mut starts, cur) = {
            let s = self.canvas().state();
            (pass_starts(&s.segs), s.sim_index.unwrap_or(s.segs.len()))
        };
        starts.push(self.moves());
        let next = if direction > 0 {
            starts.iter().copied().find(|s| *s > cur)
        } else {
            Some(starts.iter().rev().copied().find(|s| *s < cur).unwrap_or(0))
        };
        self.goto(next);
    }

    pub fn select_op(&self, index: Option<usize>) {
        let imp = self.imp();
        imp.selected_op.set(index);
        let found = index.is_some() && self.canvas().state().segs.iter().any(|sg| sg.op_index == index);
        imp.to_op.set_sensitive(found);
    }

    /// Jump to the operation's first move (shown and named in the label); Play or Next Pass runs on from it.
    pub fn goto_op(&self, index: Option<usize>) {
        let first = self.canvas().state().segs.iter().position(|sg| sg.op_index == index);
        if let Some(first) = first {
            self.goto(Some(first + 1));
        }
    }

    fn interval(&self) -> Duration {
        Duration::from_millis(((1000.0 / (MOVES_PER_SECOND * self.imp().speed.get())) as u64).max(1))
    }

    fn start_timer(&self) {
        let bar = self.downgrade();
        let id = glib::timeout_add_local(self.interval(), move || match bar.upgrade() {
            Some(bar) => bar.tick(),
            None => glib::ControlFlow::Break,
        });
        self.imp().play.replace(Some(id));
    }

    pub fn next_speed(&self) {
        let imp = self.imp();
        let i = SPEEDS.iter().position(|s| *s == imp.speed.get()).unwrap_or(0);
        imp.speed.set(SPEEDS[(i + 1) % SPEEDS.len()]);
        imp.speed_button.set_label(&format!("{}×", fmt_g(imp.speed.get())));
        if let Some(id) = imp.play.take() {
            // playing: carry on at the new speed
            id.remove();
            self.start_timer();
        }
    }

    pub fn toggle_play(&self) {
        if self.imp().play.borrow().is_some() {
            self.stop();
            return;
        }
        if self.canvas().state().sim_index.is_none() {
            self.goto(Some(0));
        }
        self.imp().play_button.set_icon_name("media-playback-pause-symbolic");
        self.start_timer();
    }

    pub fn stop(&self) {
        let imp = self.imp();
        if let Some(id) = imp.play.take() {
            id.remove();
        }
        imp.play_button.set_icon_name("media-playback-start-symbolic");
    }

    fn tick(&self) -> glib::ControlFlow {
        let i = self.canvas().state().sim_index.unwrap_or(0) + 1;
        if i >= self.moves() {
            self.goto(Some(self.moves())); // the end: the tool on the last point, the stock as the program leaves it
            let imp = self.imp();
            imp.play.replace(None); // returning Break removes the source
            imp.play_button.set_icon_name("media-playback-start-symbolic");
            return glib::ControlFlow::Break;
        }
        self.goto(Some(i));
        glib::ControlFlow::Continue
    }

    fn scrubbed(&self) {
        let imp = self.imp();
        if imp.guard.get() || imp.canvas.get().is_none() {
            return;
        }
        self.stop();
        let v = imp.scale.value().round().max(0.0) as usize;
        let n = self.moves();
        self.set_sim(if v >= n { None } else { Some(v) });
        self.update_label();
        self.canvas().queue_draw();
    }

    fn update_label(&self) {
        let imp = self.imp();
        let (n, i, seg) = {
            let s = self.canvas().state();
            let seg = s.sim_index.map(|i| s.segs[i.saturating_sub(1)]);
            (s.segs.len(), s.sim_index, seg)
        };
        // nothing to play or step through without moves
        for b in [&imp.first_button, &imp.previous_button, &imp.play_button, &imp.next_button, &imp.last_button,
                  &imp.to_op] {
            b.set_sensitive(n > 0);
        }
        if n == 0 {
            imp.label.set_label(&gettext("no moves"));
            return;
        }
        let (Some(i), Some(s)) = (i, seg) else {
            imp.label.set_label(&fill(&ngettext("{n} move", "{n} moves", n as u32), &[("n", &n)]));
            return;
        };
        let op = match s.op_index {
            None => gettext("tool block"),
            Some(k) => tr("op {n}", &[("n", &(k + 1))]),
        };
        let pass = if s.pass_no != 0 { format!(" · {}", tr("pass {n}", &[("n", &s.pass_no)])) } else { String::new() };
        imp.label.set_label(&tr(
            "{i}/{total} · {op}{pass_} · line {line}",
            &[("i", &i), ("total", &n), ("op", &op), ("pass_", &pass), ("line", &s.line)],
        ));
        if let Some(on_move) = imp.on_move.get() {
            on_move(s.line);
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::rc::Rc;

    use super::*;

    /// The transport over the example's moves, and the setup sheet's PDF (both need GTK, which lives on one test
    /// thread). Skipped without a display.
    #[test]
    fn transport_and_pdf() {
        if std::env::var_os("DISPLAY").is_none() && std::env::var_os("WAYLAND_DISPLAY").is_none() {
            return;
        }
        gtk::init().expect("gtk");
        gtk::gio::resources_register_include!("alphalink-cam.gresource").expect("the app's resources");
        let canvas = LatheCanvas::new();
        crate::report::tests::writes_pdf(&canvas); // sets the example on the canvas
        let lines = Rc::new(RefCell::new(vec![]));
        let seen = lines.clone();
        let bar = SimulationBar::new(&canvas, move |l| seen.borrow_mut().push(l));
        let segs = canvas.state().segs.clone();
        let n = segs.len();
        assert!(n > 0);
        assert_eq!(bar.imp().label.label(), fill(&ngettext("{n} move", "{n} moves", n as u32), &[("n", &n)]));
        // previous pass from the end: the last pass start
        bar.step_pass(-1);
        let starts = pass_starts(&segs);
        assert_eq!(canvas.state().sim_index, starts.last().copied());
        bar.goto(Some(0));
        bar.step_pass(1);
        assert_eq!(canvas.state().sim_index, Some(starts[1]));
        assert_eq!(*lines.borrow().last().unwrap(), segs[starts[1] - 1].line);
        // to the selected operation's first move, named in the label
        bar.select_op(Some(1));
        assert!(bar.imp().to_op.is_sensitive());
        let first = segs.iter().position(|s| s.op_index == Some(1)).unwrap();
        bar.goto_op(Some(1));
        assert_eq!(canvas.state().sim_index, Some(first + 1));
        assert!(bar.imp().label.label().contains("op 2"), "{}", bar.imp().label.label());
        // scrubbing to the end shows everything
        bar.imp().scale.set_value(n as f64);
        assert_eq!(canvas.state().sim_index, None);
        // speeds cycle, playing and stopping
        bar.next_speed();
        assert_eq!(bar.imp().speed_button.label().as_deref(), Some("2×"));
        bar.toggle_play();
        assert_eq!(canvas.state().sim_index, Some(0));
        assert!(bar.imp().play.borrow().is_some());
        assert_eq!(bar.tick(), glib::ControlFlow::Continue);
        assert_eq!(canvas.state().sim_index, Some(1));
        bar.stop();
        assert!(bar.imp().play.borrow().is_none());
        bar.goto(Some(n - 1));
        assert_eq!(bar.tick(), glib::ControlFlow::Break); // the last move: the finished stock stays shown
        assert_eq!(canvas.state().sim_index, Some(n));
        bar.select_op(Some(99));
        assert!(!bar.imp().to_op.is_sensitive());
    }
}
