//! User preferences (ALPHA-T's ATSETUP), kept in ~/.config/alphalink-cam/settings.json.
//!
//! Read before the widgets are built (the language must be known before the first string is translated), written
//! whenever a value changes. The file is shared with the Python app.

use std::cell::RefCell;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// "" = first port found
    pub serial_port: String,
    pub char_delay_ms: f64,
    pub wait_xon: bool,
    /// auto | pointer | touch
    pub input_layout: String,
    pub units: String,
    pub billet_diameter: f64,
    pub billet_length: f64,
    pub first_tool: i32,
    /// "" = follow the desktop; "en"; "uk"
    pub language: String,
    pub show_grid: bool,
    /// Export PRG also writes the ALPHA-T drawing (.SDG + .SDT) next to the program
    pub export_sdg: bool,
    /// where the last program was exported (a USB stick, say): the next export starts there
    pub export_dir: String,
    /// rpm, for the cycle time estimate
    pub spindle_max: f64,
    /// mm/min
    pub rapid_rate: f64,
    /// seconds the operator needs to change a tool
    pub tool_change_s: f64,
    /// KB of CAM program memory on the control (the Alphalink manual: about 16 KB on the standard control); Send asks above it
    pub lathe_memory_kb: f64,
    /// Bluetooth adapters with 8-bit framing only: 8N2 on the adapter, parity made by the app
    pub soft_parity: bool,
    /// the Program page's font size in points (pinch, Ctrl+= / Ctrl+− / Ctrl+0, Ctrl+wheel)
    pub program_font_pt: f64,
}

/// The Program page's font size before any change (Ctrl+0 returns to it).
pub const PROGRAM_FONT_PT: f64 = 11.0;

impl Default for Settings {
    fn default() -> Self {
        Settings {
            serial_port: String::new(),
            char_delay_ms: 2.0,
            wait_xon: true,
            input_layout: "auto".into(),
            units: "mm".into(),
            billet_diameter: 50.0,
            billet_length: 80.0,
            first_tool: 3,
            language: String::new(),
            show_grid: true,
            export_sdg: false,
            export_dir: String::new(),
            spindle_max: 2500.0,
            rapid_rate: 6000.0,
            tool_change_s: 30.0,
            lathe_memory_kb: 16.0,
            soft_parity: false,
            program_font_pt: PROGRAM_FONT_PT,
        }
    }
}

fn path() -> PathBuf {
    gtk::glib::user_config_dir().join("alphalink-cam").join("settings.json")
}

impl Settings {
    /// The saved preferences; missing, damaged, or not an object: the defaults (unknown keys are ignored).
    pub fn load() -> Self {
        std::fs::read_to_string(path()).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or_default()
    }

    /// Preferences are a convenience: never fail an action because they could not be saved.
    fn save(&self) {
        let path = path();
        let text = serde_json::to_string_pretty(self).unwrap_or_default() + "\n";
        if std::fs::create_dir_all(path.parent().unwrap_or(&path)).is_ok() {
            let _ = gtk::glib::file_set_contents(&path, text.as_bytes());
        }
    }
}

thread_local! {
    static SETTINGS: RefCell<Option<Settings>> = const { RefCell::new(None) };
}

/// A copy of the current preferences.
pub fn settings() -> Settings {
    SETTINGS.with(|s| s.borrow_mut().get_or_insert_with(Settings::load).clone())
}

/// Change preferences and save them when anything changed.
pub fn update(change: impl FnOnce(&mut Settings)) {
    SETTINGS.with(|s| {
        let mut s = s.borrow_mut();
        let current = s.get_or_insert_with(Settings::load);
        let before = current.clone();
        change(current);
        if *current != before {
            current.save();
        }
    });
}
