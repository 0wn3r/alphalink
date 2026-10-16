//! The AlphaLink keypad (keypad.py, ui/keypad.blp): numbers by touch without the system keyboard. STUB: being
//! ported; the API below is what the drawing tools and the operation editor use.

use adw::prelude::*;

/// `fields`: (key, label) to fill, X Ø and Z when empty (a point, "x,z" to `on_done`); one field gives `on_done` that
/// value alone (the editor's numbers). `values`: (key, text) the fields start with. Present it on `parent`.
pub fn keypad(
    parent: &impl IsA<gtk::Widget>,
    title: Option<&str>,
    values: &[(&str, String)],
    fields: &[(&str, &str)],
    on_done: impl Fn(String) + 'static,
) {
    let _ = (parent, title, values, fields, on_done);
}
