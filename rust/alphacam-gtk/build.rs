//! The interface files (Blueprint sources app/alphacam_gtk/ui/*.blp, compiled to the committed .ui by
//! tools/build_ui.py) and the style sheet go into the binary as a GResource; the commit it is built from goes into
//! ALPHALINK_COMMIT for the About dialog.

use std::path::Path;
use std::process::Command;

/// Where the Flatpak build finds the commit: it builds a copy of the tree without .git, so the workflow (or whoever
/// builds) writes the commit id here first (`git rev-parse --short=12 HEAD > rust/build-aux/commit`).
const COMMIT_FILE: &str = "../build-aux/commit";

fn commit() -> String {
    let git = Command::new("git").args(["rev-parse", "--short=12", "HEAD"]).output().ok().filter(|o| o.status.success());
    let id = match git {
        Some(out) => String::from_utf8_lossy(&out.stdout).into_owned(),
        None => std::fs::read_to_string(COMMIT_FILE).unwrap_or_default(),
    };
    let id = id.trim();
    if id.is_empty() { "unknown".into() } else { id.chars().take(12).collect() }
}

fn main() {
    glib_build_tools::compile_resources(
        &["../../app/alphacam_gtk"],
        "resources.gresource.xml",
        "alphalink-cam.gresource",
    );
    println!("cargo:rustc-env=ALPHALINK_COMMIT={}", commit());
    for head in [COMMIT_FILE, "../../.git/HEAD", "../../.git/refs/heads", "../../.git/packed-refs"] {
        if Path::new(head).exists() {
            println!("cargo:rerun-if-changed={head}");
        }
    }
}
