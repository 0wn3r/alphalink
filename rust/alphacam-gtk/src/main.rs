//! alphalink-cam [PART.alcam | DRAWING.SDG | DRAWING.DXF] [--select N] [--page NAME] [--touch]

mod application;
mod billet;
mod canvas;
mod drawing_tools;
mod help;
mod hit_keys;
mod hold_button;
mod i18n;
mod input_mode;
mod keypad;
mod machine;
mod op_editor;
mod op_factory;
mod report;
mod settings;
mod shortcuts;
mod simulation;
mod templates;
mod test_bus;
mod tools_page;
mod ui;
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
