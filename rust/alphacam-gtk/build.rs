//! The interface files (Blueprint sources app/alphacam_gtk/ui/*.blp, compiled to the committed .ui by
//! tools/build_ui.py) and the style sheet go into the binary as a GResource.

fn main() {
    glib_build_tools::compile_resources(
        &["../../app/alphacam_gtk"],
        "resources.gresource.xml",
        "alphalink-cam.gresource",
    );
}
