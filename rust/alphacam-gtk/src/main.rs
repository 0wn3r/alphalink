//! alphalink-cam [PART.alcam | DRAWING.SDG | DRAWING.DXF] [--select N] [--page NAME] [--touch]

mod application;
mod i18n;
mod settings;
mod window;

use gtk::prelude::*;
use gtk::{gio, glib};

fn main() -> glib::ExitCode {
    gio::resources_register_include!("alphalink-cam.gresource").expect("the app's resources");
    i18n::init(&settings::settings().language);
    let mut argv: Vec<String> = std::env::args().collect();
    let options = application::StartOptions::take(&mut argv);
    application::build(options).run_with_args(&argv)
}
