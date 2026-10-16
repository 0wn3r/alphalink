//! The operation editor (op_editor.py, ui/op-editor.blp). STUB: being ported; the API below is what the window uses.

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct OpEditor {}

    #[glib::object_subclass]
    impl ObjectSubclass for OpEditor {
        const NAME: &'static str = "AlphaLinkOpEditor";
        type Type = super::OpEditor;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for OpEditor {}
    impl WidgetImpl for OpEditor {}
    impl BoxImpl for OpEditor {}
}

glib::wrapper! {
    pub struct OpEditor(ObjectSubclass<imp::OpEditor>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

use alphacam::model::Project;

/// A change to the open part, made as one undoable step (the window's apply_edit).
pub type ApplyFn = Box<dyn FnOnce(&mut Project) -> alphacam::error::Result<()>>;

/// Set by the window.
pub struct EditorHooks {
    /// (undo label, change): an edit from the editor's own rows (must not rebuild the editor)
    pub apply: Box<dyn Fn(&str, ApplyFn)>,
    /// move the operation up (-1) or down (+1)
    pub on_move: Box<dyn Fn(i32)>,
    pub on_delete: Box<dyn Fn()>,
    pub on_close: Box<dyn Fn()>,
    /// start contour picking for operation n
    pub on_pick: Box<dyn Fn(usize)>,
    pub on_duplicate: Box<dyn Fn()>,
    /// the tool's name for its number
    pub tool_name: Box<dyn Fn(i32) -> String>,
}

impl OpEditor {
    pub fn set_hooks(&self, _hooks: EditorHooks) {}

    pub fn show_op(&self, _project: &Project, _index: usize, _title: &str) {}
}

/// Reserve room for the widest value, so the number is never clipped when a long (e.g. Ukrainian) title takes the
/// row's width.
pub fn fit_spin(row: &adw::SpinRow, lo: f64, hi: f64, digits: u32) {
    let d = digits as usize;
    let chars = format!("{lo:.d$}").len().max(format!("{hi:.d$}").len()) as i32;
    let mut stack: Vec<gtk::Widget> = vec![row.clone().upcast()];
    while let Some(w) = stack.pop() {
        if let Some(spin) = w.downcast_ref::<gtk::SpinButton>() {
            spin.set_width_chars(chars);
            spin.set_max_width_chars(chars);
            break;
        }
        let mut c = w.first_child();
        while let Some(child) = c {
            c = child.next_sibling();
            stack.push(child);
        }
    }
}
