//! The Machine page: port, send, backup/restore (machine.py, ui/machine.blp). STUB: being ported.

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct MachinePage {}

    #[glib::object_subclass]
    impl ObjectSubclass for MachinePage {
        const NAME: &'static str = "AlphaLinkMachinePage";
        type Type = super::MachinePage;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for MachinePage {}
    impl WidgetImpl for MachinePage {}
    impl BoxImpl for MachinePage {}
}

glib::wrapper! {
    pub struct MachinePage(ObjectSubclass<imp::MachinePage>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

use crate::window::MainWindow;

impl MachinePage {
    pub fn new(_window: &MainWindow) -> Self {
        glib::Object::new()
    }

    /// The program to send (None: the part does not post) and its one-line description.
    pub fn set_program(&self, _data: Option<Vec<u8>>, _info: &str) {}
}
