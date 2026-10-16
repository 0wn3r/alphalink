//! Translation hook for messages the core shows to people (errors that the app puts in its banner or toasts).
//!
//! The core stays free of GTK and locale files: messages are written in English with `{name}` placeholders, and the
//! app installs its gettext function with [`set_translator`], so the same catalog (domain "alphalink-cam")
//! translates them. Without a translator the English text is used, e.g. on the command line.

use std::sync::OnceLock;

static TRANSLATE: OnceLock<fn(&str) -> String> = OnceLock::new();

pub fn set_translator(f: fn(&str) -> String) {
    let _ = TRANSLATE.set(f);
}

/// The translated message with its `{name}` placeholders filled in.
pub fn tr(message: &str, values: &[(&str, &dyn std::fmt::Display)]) -> String {
    let mut text = match TRANSLATE.get() {
        Some(f) => f(message),
        None => message.to_string(),
    };
    for (name, value) in values {
        text = text.replace(&format!("{{{name}}}"), &value.to_string());
    }
    text
}
