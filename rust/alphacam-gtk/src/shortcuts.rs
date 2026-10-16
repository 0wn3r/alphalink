//! The Keyboard Shortcuts dialog (Ctrl+?): ui/shortcuts.blp.

pub fn shortcuts_dialog() -> adw::ShortcutsDialog {
    crate::ui::object(&crate::ui::builder("shortcuts"), "dialog")
}
