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

/// The app's own locale, uk_UA.UTF-8's LC_MESSAGES alone (the Flatpak builds it with localedef): enough for
/// gettext to honour LANGUAGE where the system has only the C locales.
fn bundled_locales() -> PathBuf {
    if let Ok(dir) = std::env::var("ALPHALINK_LOCPATH") {
        return dir.into();
    }
    PathBuf::from(option_env!("ALPHALINK_PREFIX").unwrap_or("/app")).join("lib/alphalink-cam/locale")
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
        // glibc's gettext ignores LANGUAGE in the C locales (C, POSIX and C.UTF-8): messages need a real locale.
        // Take the chosen language's own one, or any common one that is installed (only messages change).
        let current = unsafe { setlocale(LocaleCategory::LcMessages, "") }.map(|l| String::from_utf8_lossy(&l).into_owned());
        if matches!(current.as_deref(), None | Some("C") | Some("POSIX") | Some("C.UTF-8") | Some("C.utf8")) {
            let lang = forced.split(['_', '.', ':']).next().unwrap_or("");
            let own = format!("{lang}_{}.UTF-8", if lang == "uk" { "UA".to_string() } else { lang.to_uppercase() });
            let found = [own.as_str(), "en_US.UTF-8", "en_GB.UTF-8"].into_iter().any(|candidate| {
                // SAFETY: still at startup, before any other thread exists
                unsafe { setlocale(LocaleCategory::LcMessages, candidate) }.is_some()
            });
            // none installed (a system or runtime with only the C locales): the app's own, messages only
            if !found && bundled_locales().join("uk_UA.UTF-8").exists() {
                // SAFETY: still at startup, before any other thread exists
                unsafe { std::env::set_var("LOCPATH", bundled_locales()) };
                unsafe { setlocale(LocaleCategory::LcMessages, "uk_UA.UTF-8") };
            }
        }
        // gtk_init's own setlocale(LC_ALL, "") would undo the messages locale chosen here
        gtk::disable_setlocale();
    }
    let _ = bindtextdomain(DOMAIN, locale_dir());
    let _ = bind_textdomain_codeset(DOMAIN, "UTF-8");
    let _ = textdomain(DOMAIN);
    alphacam::i18n::set_translator(|text| gettextrs::gettext(text));
}

pub fn gettext(text: &str) -> String {
    gettextrs::gettext(text)
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

/// Fill a translated text's {name} fields (Python's str.format with names).
pub fn fill(text: &str, args: &[(&str, &dyn std::fmt::Display)]) -> String {
    let mut out = text.to_string();
    for (name, value) in args {
        out = out.replace(&format!("{{{name}}}"), &value.to_string());
    }
    out
}

/// gettext, then fill its {name} fields.
pub fn tr(text: &str, args: &[(&str, &dyn std::fmt::Display)]) -> String {
    fill(&gettext(text), args)
}
