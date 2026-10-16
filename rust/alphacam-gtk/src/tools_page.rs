//! The Tools page (tools_page.py, ui/tools-page.blp). STUB: being ported.

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct ToolsPage {}

    #[glib::object_subclass]
    impl ObjectSubclass for ToolsPage {
        const NAME: &'static str = "AlphaLinkToolsPage";
        type Type = super::ToolsPage;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for ToolsPage {}
    impl WidgetImpl for ToolsPage {}
    impl BoxImpl for ToolsPage {}
}

glib::wrapper! {
    pub struct ToolsPage(ObjectSubclass<imp::ToolsPage>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

use std::path::PathBuf;

use alphacam::model::tools::{ToolLibrary, standard_library};

use crate::window::MainWindow;

pub fn library_path() -> PathBuf {
    glib::user_config_dir().join("alphalink-cam").join("tools.json")
}

/// The user's tool library, or ALPHA-T's standard one when there is none (or it is damaged).
pub fn load_library() -> ToolLibrary {
    std::fs::read_to_string(library_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .and_then(|data| ToolLibrary::from_json(&data).ok())
        .unwrap_or_else(|| standard_library().clone())
}

impl ToolsPage {
    pub fn new(_window: &MainWindow) -> Self {
        glib::Object::new()
    }
}
