//! The interface files: ui/<name>.ui (compiled from Blueprint) in the app's resources. Their translatable strings
//! go through GtkBuilder, which uses the app's gettext domain (i18n::init makes it the default one).

use std::path::PathBuf;

use gtk::glib;
use gtk::prelude::*;

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
    let installed = PathBuf::from(option_env!("ALPHALINK_PREFIX").unwrap_or("/app")).join("share/alphalink-cam");
    if installed.join("help").is_dir() {
        return installed;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../app/alphacam_gtk")
}

/// The local path a file dialog chose. Cancel gives None quietly; a failure, or a file with no local path, goes to
/// the log too, so a portal problem shows up there instead of as a dialog that did nothing.
pub fn chosen(res: Result<gtk::gio::File, glib::Error>) -> Option<PathBuf> {
    match res {
        Ok(file) => {
            let path = file.path();
            if path.is_none() {
                glib::g_warning!("alphalink", "the file dialog chose {}, which has no local path", file.uri());
            }
            path
        }
        Err(e) if e.matches(gtk::DialogError::Dismissed) || e.matches(gtk::DialogError::Cancelled) => None,
        Err(e) => {
            glib::g_warning!("alphalink", "the file dialog failed: {e}");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    //! The interface files: each Blueprint source has its compiled .ui (tools/build_ui.py), up to date, loaded by
    //! the code, with its strings in the Ukrainian catalog (tools/update_translations.py).

    use std::path::{Path, PathBuf};

    use sha2::{Digest, Sha256};

    fn ui_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../app/alphacam_gtk/ui")
    }

    fn files(ext: &str) -> Vec<PathBuf> {
        let mut out: Vec<PathBuf> = std::fs::read_dir(ui_dir())
            .unwrap()
            .map(|e| e.unwrap().path())
            .filter(|p| p.extension().is_some_and(|e| e == ext))
            .collect();
        out.sort();
        out
    }

    fn stem(p: &Path) -> String {
        p.file_stem().unwrap().to_string_lossy().into_owned()
    }

    #[test]
    fn every_blueprint_is_compiled() {
        let stamp = regex::Regex::new(r"<!-- blueprint source sha256: (\w+) -->").unwrap();
        for blp in files("blp") {
            let ui = blp.with_extension("ui");
            let text = std::fs::read_to_string(&ui).unwrap_or_else(|_| panic!("run tools/build_ui.py: {ui:?} is missing"));
            let hash: String = Sha256::digest(std::fs::read(&blp).unwrap()).iter().map(|b| format!("{b:02x}")).collect();
            let got = stamp.captures(&text).unwrap_or_else(|| panic!("{ui:?} was not written by tools/build_ui.py"));
            assert_eq!(got[1], hash, "{blp:?} changed: run tools/build_ui.py");
        }
    }

    #[test]
    fn no_ui_without_blueprint() {
        let orphans: Vec<_> = files("ui").into_iter().filter(|ui| !ui.with_extension("blp").exists()).collect();
        assert!(orphans.is_empty(), "{orphans:?}");
    }

    #[test]
    fn every_file_is_used() {
        let src_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut src = String::new();
        let mut dirs = vec![src_dir];
        while let Some(dir) = dirs.pop() {
            for e in std::fs::read_dir(dir).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    dirs.push(p);
                } else if p.extension().is_some_and(|e| e == "rs") {
                    src += &std::fs::read_to_string(p).unwrap();
                }
            }
        }
        for ui in files("ui") {
            let name = stem(&ui);
            assert!(src.contains(&format!("builder(\"{name}\")")) || src.contains(&format!("/ui/{name}.ui")),
                    "{name}.ui is not loaded anywhere");
        }
    }

    #[test]
    fn strings_are_translated() {
        let po = std::fs::read_to_string(ui_dir().join("../../po/uk.po")).unwrap();
        // msgid "…" (continued lines joined) followed by a non-empty msgstr
        let entry = regex::Regex::new(r#"(?m)^msgid ((?:".*"\n)+)msgstr ((?:".*"\n)+)"#).unwrap();
        let part = regex::Regex::new(r#""(.*)""#).unwrap();
        let join = |block: &str| part.captures_iter(block).map(|c| c[1].to_string()).collect::<String>();
        let translated: std::collections::HashSet<String> = entry
            .captures_iter(&po)
            .filter(|c| !join(&c[2]).is_empty())
            .map(|c| join(&c[1]).replace("\\n", "\n").replace("\\\"", "\"").replace("\\\\", "\\"))
            .collect();
        let el = regex::Regex::new(r#"translatable="yes"[^>]*>([^<]+)<"#).unwrap();
        let mut missing = vec![];
        for ui in files("ui") {
            let text = std::fs::read_to_string(&ui).unwrap();
            for c in el.captures_iter(&text) {
                let s = c[1].replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&apos;", "'")
                    .replace("&amp;", "&");
                if !translated.contains(&s) {
                    missing.push(format!("{}: {s}", stem(&ui)));
                }
            }
        }
        assert!(missing.is_empty(), "untranslated:\n{}", missing.join("\n"));
    }
}
