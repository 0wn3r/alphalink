//! The main window (window.blp): being ported from app/alphacam_gtk/window.py. For now an empty window, so the
//! application, resources and translations can be checked.

use std::path::Path;

use adw::prelude::*;

use crate::application::StartOptions;

pub fn new(app: &adw::Application) -> gtk::Window {
    let win = adw::ApplicationWindow::builder().application(app).title("AlphaLink CAM").build();
    win.upcast()
}

pub fn open(_win: &gtk::Window, _path: &Path, _options: &StartOptions) {}
