//! HoldButton (TOUCH.md 7.4): an action that only fires after the button is held for `hold-seconds`.
//!
//! A progress bar inside the button fills while it is held; letting go early cancels. Keyboard: Space/Enter must
//! be held as well (key repeat keeps it going), so a stray tap or keypress never starts a transfer.

use std::cell::{Cell, RefCell};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use gtk::glib::subclass::Signal;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib};

use crate::i18n::gettext;

mod imp {
    use super::*;

    #[derive(glib::Properties)]
    #[properties(wrapper_type = super::HoldButton)]
    pub struct HoldButton {
        /// the button's label (and its accessible name); used in ui/machine.blp
        #[property(get = Self::text, set = Self::set_text)]
        pub text: std::marker::PhantomData<String>,
        #[property(get, set)]
        pub hold_seconds: Cell<f64>,
        pub label: gtk::Label,
        pub bar: gtk::ProgressBar,
        pub t0: Cell<Option<Instant>>,
        pub tick: RefCell<Option<glib::SourceId>>,
    }

    impl Default for HoldButton {
        fn default() -> Self {
            HoldButton {
                text: Default::default(),
                hold_seconds: Cell::new(1.5),
                label: gtk::Label::new(None),
                bar: gtk::ProgressBar::new(),
                t0: Cell::new(None),
                tick: RefCell::default(),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for HoldButton {
        const NAME: &'static str = "AlphaLinkHoldButton";
        type Type = super::HoldButton;
        type ParentType = gtk::Button;
    }

    #[glib::derived_properties]
    impl ObjectImpl for HoldButton {
        fn signals() -> &'static [Signal] {
            static SIGNALS: OnceLock<Vec<Signal>> = OnceLock::new();
            SIGNALS.get_or_init(|| vec![Signal::builder("held").build()])
        }

        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            let column = gtk::Box::new(gtk::Orientation::Vertical, 4);
            let hint = gtk::Label::new(Some(&gettext("Hold to start")));
            hint.add_css_class("caption");
            hint.add_css_class("dim-label");
            column.append(&self.label);
            column.append(&hint);
            column.append(&self.bar);
            obj.set_child(Some(&column));
            obj.add_css_class("hold-button");
            let press = gtk::GestureClick::builder().button(0).propagation_phase(gtk::PropagationPhase::Capture).build();
            press.connect_pressed(glib::clone!(#[weak] obj, move |_, _, _, _| obj.imp().start()));
            press.connect_released(glib::clone!(#[weak] obj, move |_, _, _, _| obj.imp().stop()));
            press.connect_cancel(glib::clone!(#[weak] obj, move |_, _| obj.imp().stop()));
            obj.add_controller(press);
            let keys = gtk::EventControllerKey::new();
            keys.connect_key_pressed(glib::clone!(#[weak] obj, #[upgrade_or] glib::Propagation::Proceed,
                move |_, key, _, _| {
                    if matches!(key, gdk::Key::space | gdk::Key::Return | gdk::Key::KP_Enter) {
                        if obj.imp().t0.get().is_none() {
                            obj.imp().start();
                        }
                        return glib::Propagation::Stop;
                    }
                    glib::Propagation::Proceed
                }));
            keys.connect_key_released(glib::clone!(#[weak] obj, move |_, _, _, _| obj.imp().stop()));
            obj.add_controller(keys);
        }
    }

    impl WidgetImpl for HoldButton {}
    impl ButtonImpl for HoldButton {}

    impl HoldButton {
        fn text(&self) -> String {
            self.label.label().into()
        }

        fn set_text(&self, value: String) {
            self.label.set_label(&value);
            // the child box has no name of its own
            self.obj().update_property(&[gtk::accessible::Property::Label(&value)]);
        }

        fn start(&self) {
            let obj = self.obj();
            if !obj.is_sensitive() || self.t0.get().is_some() {
                return;
            }
            self.t0.set(Some(Instant::now()));
            let id = glib::timeout_add_local(Duration::from_millis(30), glib::clone!(#[weak] obj,
                #[upgrade_or] glib::ControlFlow::Break, move || obj.imp().update()));
            self.tick.replace(Some(id));
        }

        fn update(&self) -> glib::ControlFlow {
            let Some(t0) = self.t0.get() else {
                return glib::ControlFlow::Break;
            };
            let frac = t0.elapsed().as_secs_f64() / self.hold_seconds.get();
            self.bar.set_fraction(frac.min(1.0));
            if frac >= 1.0 {
                self.t0.set(None);
                self.tick.take(); // returning Break removes the source
                self.bar.set_fraction(0.0);
                self.obj().emit_by_name::<()>("held", &[]);
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        }

        fn stop(&self) {
            if let Some(id) = self.tick.take() {
                id.remove();
            }
            self.t0.set(None);
            self.bar.set_fraction(0.0);
        }
    }
}

glib::wrapper! {
    pub struct HoldButton(ObjectSubclass<imp::HoldButton>)
        @extends gtk::Button, gtk::Widget,
        @implements gtk::Accessible, gtk::Actionable, gtk::Buildable, gtk::ConstraintTarget;
}

impl HoldButton {
    pub fn connect_held(&self, f: impl Fn(&Self) + 'static) -> glib::SignalHandlerId {
        self.connect_closure("held", false, glib::closure_local!(move |b: &HoldButton| f(b)))
    }
}
