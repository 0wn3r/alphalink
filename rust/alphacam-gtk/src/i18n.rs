//! Translations (gettext, domain "alphalink-cam"). The language follows the desktop (LANGUAGE / LC_MESSAGES /
//! LANG); the Language preference or `ALPHALINK_LANG=uk` overrides it. Catalogs are the Python app's compiled
//! ones, app/alphacam_gtk/locale/<lang>/LC_MESSAGES/ (installed to <prefix>/share/locale in the Flatpak).
//!
//! GtkBuilder translates the interface files through the same catalog, and the core's messages (errors in the
//! banner and toasts) through alphacam::i18n::set_translator.

use std::path::PathBuf;

use gettextrs::{LocaleCategory, bind_textdomain_codeset, bindtextdomain, setlocale, textdomain};

pub const DOMAIN: &str = "alphalink-cam";

fn locale_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("ALPHALINK_LOCALEDIR") {
        return dir.into();
    }
    // the installed location first, then the source tree (cargo run)
    let installed = PathBuf::from(option_env!("ALPHALINK_PREFIX").unwrap_or("/app")).join("share/locale");
    if installed.join("uk/LC_MESSAGES").join(format!("{DOMAIN}.mo")).exists() {
        return installed;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../app/alphacam_gtk/locale")
}

/// Set up gettext before any widget is built.
pub fn init(language: &str) {
    let forced = std::env::var("ALPHALINK_LANG").ok().filter(|l| !l.is_empty()).unwrap_or_else(|| language.into());
    // SAFETY: called at startup before any other thread exists (setlocale is not thread safe)
    unsafe { setlocale(LocaleCategory::LcAll, "") };
    if !forced.is_empty() {
        // GTK and libadwaita's own texts (About, file chooser) follow the chosen language too
        // SAFETY: called at startup before any other thread exists
        unsafe { std::env::set_var("LANGUAGE", &forced) };
        // gettext ignores LANGUAGE in the plain C locale
        let current = unsafe { setlocale(LocaleCategory::LcMessages, "") }.map(|l| String::from_utf8_lossy(&l).into_owned());
        if matches!(current.as_deref(), None | Some("C") | Some("POSIX")) {
            unsafe { setlocale(LocaleCategory::LcMessages, "C.UTF-8") };
        }
    }
    let _ = bindtextdomain(DOMAIN, locale_dir());
    let _ = bind_textdomain_codeset(DOMAIN, "UTF-8");
    let _ = textdomain(DOMAIN);
    alphacam::i18n::set_translator(|text| gettextrs::gettext(text));
}

pub fn gettext(text: &str) -> String {
    gettextrs::gettext(text)
}

pub fn pgettext(context: &str, text: &str) -> String {
    gettextrs::pgettext(context, text)
}

pub fn ngettext(singular: &str, plural: &str, n: u32) -> String {
    gettextrs::ngettext(singular, plural, n)
}

/// The language the catalog is in ("uk", or "en" for the untranslated texts).
pub fn language() -> String {
    // the header's Language field, translated through the catalog like any other string
    let header = gettextrs::gettext("");
    header
        .lines()
        .find_map(|l| l.strip_prefix("Language: "))
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .unwrap_or_else(|| "en".into())
}
