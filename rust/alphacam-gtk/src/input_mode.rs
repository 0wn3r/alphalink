//! InputMode (TOUCH.md 3): watches the source of every press on the window and switches the .touch density
//! class.
//!
//! Automatic switching needs two presses in a row from the same kind of device, so one stray touch (or one mouse
//! click on a touch screen) does not reflow the window.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use gtk::prelude::*;
use gtk::{gdk, glib};

thread_local! {
    /// the test bus's ForceSource (ALPHALINK_TEST=1 only): every press counts as this source
    static FORCED: Cell<Option<gdk::InputSource>> = const { Cell::new(None) };
}

/// Make every press count as `source` ("pen", "touch"; anything else: the real device again), for the e2e tests,
/// which have no tablet device.
pub fn force_source(source: &str) {
    FORCED.with(|f| {
        f.set(match source {
            "pen" => Some(gdk::InputSource::Pen),
            "touch" => Some(gdk::InputSource::Touchscreen),
            _ => None,
        })
    });
}

pub fn forced_source() -> Option<gdk::InputSource> {
    FORCED.with(Cell::get)
}

/// The source of the event a controller is handling: the forced one, else its device's.
pub fn event_source(controller: &impl IsA<gtk::EventController>) -> Option<gdk::InputSource> {
    forced_source().or_else(|| controller.current_event_device().map(|d| d.source()))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Pointer,
    Touch,
}

/// auto | pointer | touch
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Preference {
    Auto,
    Fixed(Mode),
}

impl Preference {
    pub fn parse(text: &str) -> Self {
        match text {
            "touch" => Preference::Fixed(Mode::Touch),
            "pointer" => Preference::Fixed(Mode::Pointer),
            _ => Preference::Auto,
        }
    }
}

type Listener<T> = Box<dyn Fn(T)>;

/// Also owns the touch lock (TOUCH.md 7.4): while locked, touch events are swallowed except on the widgets in
/// `lock_exempt` (the unlock button); mouse, pen and keyboard keep working.
pub struct InputMode {
    window: gtk::Widget,
    preference: Cell<Preference>,
    mode: Cell<Mode>,
    last: Cell<Option<Mode>>,
    locked: Cell<bool>,
    /// the last press was the pen's (POINTER_KEYBOARD.md 10): its helpers show until a mouse or touchpad press
    pen: Cell<bool>,
    pub lock_exempt: RefCell<Vec<gtk::Widget>>,
    on_changed: RefCell<Vec<Listener<Mode>>>,
    on_lock_changed: RefCell<Vec<Listener<bool>>>,
}

impl InputMode {
    pub fn new(window: &impl IsA<gtk::Widget>, preference: Preference) -> Rc<Self> {
        let this = Rc::new(InputMode {
            window: window.clone().upcast(),
            preference: Cell::new(preference),
            mode: Cell::new(match preference {
                Preference::Fixed(m) => m,
                Preference::Auto => Mode::Pointer,
            }),
            last: Cell::new(None),
            locked: Cell::new(false),
            pen: Cell::new(false),
            lock_exempt: RefCell::default(),
            on_changed: RefCell::default(),
            on_lock_changed: RefCell::default(),
        });
        let click = gtk::GestureClick::builder().button(0).propagation_phase(gtk::PropagationPhase::Capture).build();
        let weak = Rc::downgrade(&this);
        click.connect_pressed(move |gesture, _, _, _| {
            if let Some(this) = weak.upgrade() {
                this.pressed(gesture);
            }
        });
        window.add_controller(click);
        let guard = gtk::EventControllerLegacy::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let weak = Rc::downgrade(&this);
        guard.connect_event(move |_, event| match weak.upgrade() {
            Some(this) if this.swallow(event) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        window.add_controller(guard);
        this.apply();
        this
    }

    pub fn mode(&self) -> Mode {
        self.mode.get()
    }

    /// The pen's helpers (zoom buttons, keypad) are shown.
    pub fn pen(&self) -> bool {
        self.pen.get()
    }

    pub fn locked(&self) -> bool {
        self.locked.get()
    }

    pub fn connect_changed(&self, f: impl Fn(Mode) + 'static) {
        self.on_changed.borrow_mut().push(Box::new(f));
    }

    pub fn connect_lock_changed(&self, f: impl Fn(bool) + 'static) {
        self.on_lock_changed.borrow_mut().push(Box::new(f));
    }

    pub fn set_locked(&self, locked: bool) {
        if locked != self.locked.get() {
            self.locked.set(locked);
            for f in self.on_lock_changed.borrow().iter() {
                f(locked);
            }
        }
    }

    fn swallow(&self, event: &gdk::Event) -> bool {
        use gdk::EventType::*;
        if !self.locked.get()
            || !matches!(event.event_type(), TouchBegin | TouchUpdate | TouchEnd | ButtonPress | ButtonRelease
                | MotionNotify)
        {
            return false;
        }
        if forced_source().or_else(|| event.device().map(|d| d.source())) != Some(gdk::InputSource::Touchscreen) {
            return false;
        }
        let mut target = event.position().and_then(|(x, y)| self.window.pick(x, y, gtk::PickFlags::DEFAULT));
        while let Some(t) = target {
            if self.lock_exempt.borrow().contains(&t) {
                return false;
            }
            target = t.parent();
        }
        true // swallow the touch
    }

    fn pressed(&self, gesture: &gtk::GestureClick) {
        let source = event_source(gesture);
        // the pen keeps the layout as it is (a user alternating pen and finger sees no reflow) and shows its helpers;
        // a mouse or touchpad press hides them again
        let pen = matches!(source, Some(gdk::InputSource::Pen));
        let touch = source == Some(gdk::InputSource::Touchscreen);
        if pen || !touch {
            if self.pen.replace(pen) != pen {
                self.apply();
            }
            if pen {
                return;
            }
        }
        let kind = if touch { Mode::Touch } else { Mode::Pointer };
        if self.preference.get() == Preference::Auto && self.last.get() == Some(kind) && kind != self.mode.get() {
            self.mode.set(kind);
            self.apply();
        }
        self.last.set(Some(kind));
    }

    pub fn set_preference(&self, preference: Preference) {
        self.preference.set(preference);
        if let Preference::Fixed(m) = preference {
            self.mode.set(m);
        }
        self.apply();
    }

    fn apply(&self) {
        if self.mode.get() == Mode::Touch {
            self.window.add_css_class("touch");
        } else {
            self.window.remove_css_class("touch");
        }
        for f in self.on_changed.borrow().iter() {
            f(self.mode.get());
        }
    }
}
