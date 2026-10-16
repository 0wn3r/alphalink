//! The interface files: ui/<name>.ui (compiled from Blueprint) in the app's resources. Their translatable strings
//! go through GtkBuilder, which uses the app's gettext domain (i18n::init makes it the default one).

use std::path::PathBuf;

use gtk::glib;

pub const RESOURCE: &str = "/io/github/_0wn3r/AlphaLinkCAM";

/// The objects of ui/<name>.ui: dialogs and menus that are not a widget class of their own.
pub fn builder(name: &str) -> gtk::Builder {
    gtk::Builder::from_resource(&format!("{RESOURCE}/ui/{name}.ui"))
}

/// One object of ui/<name>.ui; panics when the file does not have it (a packaging error).
pub fn object<T: glib::object::IsA<glib::Object>>(b: &gtk::Builder, id: &str) -> T {
    b.object(id).unwrap_or_else(|| panic!("ui object {id}"))
}

/// Files that ship next to the program (help pages, manuals): the installed data folder, or the source tree.
pub fn data_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("ALPHALINK_DATA_DIR") {
        return dir.into();
    }
    let installed = PathBuf::from(option_env!("ALPHALINK_DATA_DIR").unwrap_or("/app/share/alphalink-cam"));
    if installed.join("help").is_dir() {
        return installed;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../app/alphacam_gtk")
}
