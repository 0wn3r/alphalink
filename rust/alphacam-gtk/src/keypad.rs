//! On-screen keypad for X,Z entry (keypad.py, ui/keypad.blp; TOUCH.md 6): big keys, ABS/INC like a control, no
//! system keyboard.
//!
//! Returns the same text the command entry understands: `X,Z` or `@dX,dZ`.

use std::cell::RefCell;

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gdk, glib};

use crate::i18n::gettext;

const KEYS: [[&str; 4]; 4] =
    [["7", "8", "9", "⌫"], ["4", "5", "6", "±"], ["1", "2", "3", "ABS"], ["0", ".", "→", "✓"]];

/// What the keypad is filling in.
#[derive(Default)]
pub struct State {
    /// (key, label) of the fields, in order
    fields: Vec<(String, String)>,
    /// (key, text) of the fields, in the order of `fields`
    values: Vec<(String, String)>,
    active: String,
    incremental: bool,
    /// the first digit replaces a given value instead of extending it
    fresh: bool,
    field_buttons: Vec<(String, gtk::Button, String)>,
    abs_button: Option<gtk::Button>,
}

impl State {
    fn value_mut(&mut self, key: &str) -> &mut String {
        let i = self.values.iter().position(|(k, _)| k == key).expect("a keypad field");
        &mut self.values[i].1
    }

    fn value(&self, key: &str) -> &str {
        self.values.iter().find(|(k, _)| k == key).map_or("", |(_, v)| v.as_str())
    }

    /// One field: its value alone; a point: `x,z`, `@x,z` when incremental.
    pub fn text(&self) -> String {
        if self.fields.len() == 1 {
            return self.value(&self.fields[0].0).to_string();
        }
        let or0 = |v: &str| if v.is_empty() { "0".to_string() } else { v.to_string() };
        let (x, z) = (or0(self.value("x")), or0(self.value("z")));
        format!("{}{x},{z}", if self.incremental { "@" } else { "" })
    }
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/keypad.ui")]
    pub struct Keypad {
        #[template_child]
        pub field_box: TemplateChild<gtk::Box>,
        #[template_child]
        pub grid: TemplateChild<gtk::Grid>,
        pub state: RefCell<State>,
        #[allow(clippy::type_complexity)]
        pub on_done: RefCell<Option<Box<dyn Fn(String)>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Keypad {
        const NAME: &'static str = "AlphaLinkKeypad";
        type Type = super::Keypad;
        type ParentType = adw::Dialog;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            super::bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for Keypad {}
    impl WidgetImpl for Keypad {}
    impl AdwDialogImpl for Keypad {}
}

glib::wrapper! {
    pub struct Keypad(ObjectSubclass<imp::Keypad>)
        @extends adw::Dialog, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

/// The hardware keys. The template scope passes (key controller, keyval, keycode, state, template object).
fn bind_callbacks(klass: &mut <imp::Keypad as ObjectSubclass>::Class) {
    klass.rust_template_scope().add_callback("_hw_key", |values| {
        let keypad = values.last().and_then(|v| v.get::<Keypad>().ok());
        let key = values.get(1).and_then(|v| v.get::<gdk::Key>().ok());
        let handled = match (keypad, key) {
            (Some(k), Some(key)) => k.hw_key(key),
            _ => false,
        };
        Some(handled.to_value())
    });
}

impl Keypad {
    /// `fields`: (key, label) to fill, X Ø and Z when empty (a point, "x,z" to `on_done`); one field gives `on_done`
    /// that value alone (the editor's numbers). `values`: what the fields start with.
    pub fn new(
        title: Option<&str>,
        values: &[(&str, String)],
        fields: &[(&str, &str)],
        on_done: impl Fn(String) + 'static,
    ) -> Self {
        let dlg: Self = glib::Object::builder()
            .property("title", title.map(str::to_string).unwrap_or_else(|| gettext("Point")))
            .build();
        let imp = dlg.imp();
        *imp.on_done.borrow_mut() = Some(Box::new(on_done));
        let fields: Vec<(String, String)> = if fields.is_empty() {
            vec![("x".into(), "X Ø".into()), ("z".into(), "Z".into())]
        } else {
            fields.iter().map(|(k, l)| (k.to_string(), l.to_string())).collect()
        };
        let mut st = State {
            values: fields.iter().map(|(k, _)| (k.clone(), String::new())).collect(),
            active: fields[0].0.clone(),
            fresh: !values.is_empty(),
            ..Default::default()
        };
        for (k, v) in values {
            match st.values.iter_mut().find(|(key, _)| key == k) {
                Some(slot) => slot.1 = v.clone(),
                None => st.values.push((k.to_string(), v.clone())),
            }
        }
        for (key, label) in &fields {
            let b = gtk::Button::new();
            b.add_css_class("keypad-field");
            let (weak, k) = (dlg.downgrade(), key.clone());
            b.connect_clicked(move |_| {
                if let Some(dlg) = weak.upgrade() {
                    dlg.activate_field(&k);
                }
            });
            imp.field_box.append(&b);
            st.field_buttons.push((key.clone(), b, label.clone()));
        }
        for (r, row) in KEYS.iter().enumerate() {
            for (c, &k) in row.iter().enumerate() {
                let b = gtk::Button::with_label(k);
                b.add_css_class("keypad-key");
                if k == "✓" {
                    b.add_css_class("suggested-action");
                }
                if k == "ABS" {
                    if fields.len() == 1 {
                        b.set_sensitive(false); // one number: nothing to be incremental from
                    }
                    st.abs_button = Some(b.clone());
                }
                let weak = dlg.downgrade();
                b.connect_clicked(move |_| {
                    if let Some(dlg) = weak.upgrade() {
                        dlg.key(k);
                    }
                });
                imp.grid.attach(&b, c as i32, r as i32, 1, 1);
            }
        }
        st.fields = fields;
        *imp.state.borrow_mut() = st;
        dlg.refresh();
        dlg
    }

    fn activate_field(&self, key: &str) {
        {
            let mut st = self.imp().state.borrow_mut();
            st.active = key.to_string();
            st.fresh = false;
        }
        self.refresh();
    }

    fn key(&self, k: &str) {
        let digit = k.len() == 1 && k.chars().all(|c| c.is_ascii_digit());
        {
            let mut st = self.imp().state.borrow_mut();
            let active = st.active.clone();
            if st.fresh && (digit || k == ".") {
                st.value_mut(&active).clear(); // typing over the value shown, as in a selected field
            }
            st.fresh = false;
            let v = st.value_mut(&active);
            match k {
                _ if digit => v.push_str(k),
                "." => {
                    if !v.contains('.') {
                        v.push('.');
                    }
                }
                "⌫" => {
                    v.pop();
                }
                "±" => {
                    *v = match v.strip_prefix('-') {
                        Some(rest) => rest.to_string(),
                        None => format!("-{v}"),
                    }
                }
                "ABS" => st.incremental = !st.incremental,
                "→" => {
                    let keys: Vec<String> = st.fields.iter().map(|(f, _)| f.clone()).collect();
                    let i = keys.iter().position(|f| *f == active).unwrap_or(0);
                    st.active = keys[(i + 1) % keys.len()].clone();
                }
                "✓" => {
                    drop(st);
                    self.done();
                    return;
                }
                _ => {}
            }
        }
        self.refresh();
    }

    fn hw_key(&self, keyval: gdk::Key) -> bool {
        let ch = keyval.to_unicode().filter(|c| *c != '\0');
        match ch {
            Some(c) if c.is_ascii_digit() || c == '.' => self.key(&c.to_string()),
            Some('-') => self.key("±"),
            _ if keyval == gdk::Key::BackSpace => self.key("⌫"),
            Some(',') => self.key("→"),
            _ if keyval == gdk::Key::Tab => self.key("→"),
            _ if keyval == gdk::Key::Return || keyval == gdk::Key::KP_Enter => self.done(),
            _ => return false,
        }
        true
    }

    /// The text for `on_done`: see [`State::text`].
    pub fn text(&self) -> String {
        self.imp().state.borrow().text()
    }

    fn done(&self) {
        if self.imp().state.borrow().values.iter().all(|(_, v)| matches!(v.as_str(), "" | "-" | ".")) {
            return;
        }
        self.close();
        let text = self.text();
        if let Some(f) = self.imp().on_done.borrow().as_ref() {
            f(text);
        }
    }

    fn refresh(&self) {
        let st = self.imp().state.borrow();
        for (key, b, label) in &st.field_buttons {
            let prefix = if st.incremental { "Δ" } else { "" };
            let v = st.value(key);
            b.set_label(&format!("{prefix}{label}  {}", if v.is_empty() { "—" } else { v }));
            if *key == st.active {
                b.add_css_class("accent");
            } else {
                b.remove_css_class("accent");
            }
        }
        if let Some(b) = &st.abs_button {
            b.set_label(&gettext(if st.incremental { "INC" } else { "ABS" }));
        }
    }
}

/// `fields`: (key, label) to fill, X Ø and Z when empty (a point, "x,z" to `on_done`); one field gives `on_done` that
/// value alone (the editor's numbers). `values`: (key, text) the fields start with. Present it on `parent`.
pub fn keypad(
    parent: &impl IsA<gtk::Widget>,
    title: Option<&str>,
    values: &[(&str, String)],
    fields: &[(&str, &str)],
    on_done: impl Fn(String) + 'static,
) {
    Keypad::new(title, values, fields, on_done).present(Some(parent));
}

#[cfg(test)]
mod tests {
    use super::State;

    #[test]
    fn text_of_a_point_and_a_number() {
        let mut st = State {
            fields: vec![("x".into(), "X Ø".into()), ("z".into(), "Z".into())],
            values: vec![("x".into(), "12.5".into()), ("z".into(), String::new())],
            ..Default::default()
        };
        assert_eq!(st.text(), "12.5,0");
        st.incremental = true;
        assert_eq!(st.text(), "@12.5,0");
        st.fields.truncate(1);
        assert_eq!(st.text(), "12.5");
    }
}
