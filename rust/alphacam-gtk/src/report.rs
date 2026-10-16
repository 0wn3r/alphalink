//! The Sheet page: setup sheet, cycle time, notes, PDF (report.py, ui/sheet-page.blp). STUB: being ported.

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SheetPage {}

    #[glib::object_subclass]
    impl ObjectSubclass for SheetPage {
        const NAME: &'static str = "AlphaLinkSheetPage";
        type Type = super::SheetPage;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for SheetPage {}
    impl WidgetImpl for SheetPage {}
    impl BoxImpl for SheetPage {}
}

glib::wrapper! {
    pub struct SheetPage(ObjectSubclass<imp::SheetPage>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

use alphacam::model::Project;
use alphacam::model::tools::{ToolLibrary, standard_library};

use crate::window::MainWindow;

impl SheetPage {
    pub fn new(_window: &MainWindow) -> Self {
        glib::Object::new()
    }

    pub fn refresh(&self) {}
}

/// The tool's name: the part's own tools first, then the library, then ALPHA-T's standard ones.
pub fn tool_name(project: &Project, number: i32, library: Option<&ToolLibrary>) -> String {
    project
        .tools
        .iter()
        .chain(library.map(|l| l.tools.iter()).into_iter().flatten())
        .chain(standard_library().get(number))
        .find(|t| t.number == number)
        .map(|t| t.name.clone())
        .unwrap_or_default()
}
