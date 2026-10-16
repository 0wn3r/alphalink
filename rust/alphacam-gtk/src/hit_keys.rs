//! ALPHA-T's single-key "hit codes" on the Part page, so an ALPHA-T user's fingers keep working (hit_keys.py).
//!
//! The table is ALPHA-T's own (HELP/ENG/SD503.HLP), checked in ALPHA-T under DOSBox-X:
//!
//! ```text
//! A find nearest arc (- erases it)   L find nearest line (- erases it)   P find nearest point
//! I find nearest intersection        G X,Z absolute move                 H X,Z incremental move
//! Y L,A move                          S start point at the cursor         X draw line X,Z
//! D draw line D,A                     + draw line freehand                F radius / chamfer
//! O 2 entity radius                   K trim corner                       M X,Z measure
//! N L,A measure                       T input text                        U erase text
//! Q erase all the drawing             Z zoom                              V recall view
//! R redraw                            C CAD / CAM menus                   E exit (not mapped: E edits, below)
//! cursor keys move the cursor, PgUp / PgDn step x10 / :10, Home screen centre, End reference point (0,0)
//! ```
//!
//! The ALPHA-T cursor is the pointer's last position on the drawing, or where these keys put it. Typed numbers go to
//! the command entry, as typing a coordinate did in ALPHA-T. Letters work while no text field has the focus, and in
//! the command entry too (a coordinate never has letters), without Ctrl or Alt, so they never clash with the Ctrl
//! shortcuts or with typing. Added here (not in ALPHA-T): E edits the selected element, B is the Chamfer tool, W the
//! Arc tool. They follow the key's place on the keyboard, so they also work with the Ukrainian layout active (the key
//! marked A/Ф is A). F1 stays this app's help.

/// What a key does (handled by `DrawingTools::hit_key`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Hit {
    FindArc,
    Cam,
    Chamfer,
    LineLa,
    Edit,
    Radius,
    MoveAbs,
    MoveInc,
    FindIntersection,
    Corner,
    FindLine,
    MeasureXz,
    MeasureLa,
    Blend,
    FindPoint,
    EraseDrawing,
    Fit,
    Start,
    Text,
    EraseText,
    RecallView,
    Arc,
    Line,
    MoveLa,
    ZoomIn,
    Freehand,
    /// picking a contour: Enter or 3 finishes it
    PickDone,
    /// Enter: the cursor is the next point
    Accept,
    /// minus with an element selected: erase it
    Erase,
    /// a digit or a coordinate's sign: typed into the command entry
    Type,
}

/// letter -> action
pub const HIT_KEYS: &[(char, Hit)] = &[
    ('a', Hit::FindArc),
    ('c', Hit::Cam),
    ('b', Hit::Chamfer),
    ('d', Hit::LineLa),
    ('e', Hit::Edit),
    ('f', Hit::Radius),
    ('g', Hit::MoveAbs),
    ('h', Hit::MoveInc),
    ('i', Hit::FindIntersection),
    ('k', Hit::Corner),
    ('l', Hit::FindLine),
    ('m', Hit::MeasureXz),
    ('n', Hit::MeasureLa),
    ('o', Hit::Blend),
    ('p', Hit::FindPoint),
    ('q', Hit::EraseDrawing),
    ('r', Hit::Fit),
    ('s', Hit::Start),
    ('t', Hit::Text),
    ('u', Hit::EraseText),
    ('v', Hit::RecallView),
    ('w', Hit::Arc),
    ('x', Hit::Line),
    ('y', Hit::MoveLa),
    ('z', Hit::ZoomIn),
    ('+', Hit::Freehand),
];

/// Hardware key codes (evdev + 8, the same under X11 and Wayland) of the letter keys a..z on a QWERTY keyboard; used
/// when the active layout gives a non-Latin letter, e.g. Ukrainian "ф" on the A key.
const LATIN_KEYCODES: [u32; 26] =
    [38, 56, 54, 40, 26, 41, 42, 43, 31, 44, 45, 46, 58, 57, 32, 33, 24, 27, 39, 28, 30, 55, 25, 53, 29, 52];

/// Keys that move the cursor (Gdk key names) by (dX, dZ) steps; they act only while the drawing has the focus.
pub fn cursor_key(name: &str) -> Option<(f64, f64)> {
    match name {
        "Left" => Some((0.0, -1.0)),
        "Right" => Some((0.0, 1.0)),
        "Up" => Some((1.0, 0.0)),
        "Down" => Some((-1.0, 0.0)),
        _ => None,
    }
}

/// PgUp / PgDn: the cursor step's factor.
pub fn step_key(name: &str) -> Option<f64> {
    match name {
        "Page_Up" => Some(10.0),
        "Page_Down" => Some(0.1),
        _ => None,
    }
}

/// keys that start a typed coordinate in the command entry
const ENTRY_CHARS: &str = "0123456789.-@,";

/// The character a key stands for in ALPHA-T: a non-Latin letter becomes the Latin letter on that key.
pub fn key_char(c: char, keycode: u32) -> char {
    if c.is_alphabetic() && !c.is_ascii() {
        if let Some(i) = LATIN_KEYCODES.iter().position(|&k| k == keycode) {
            return (b'a' + i as u8) as char;
        }
    }
    c
}

/// The action for a typed character (Enter is '\r'), or None. `picking`: a contour is being picked; `selected`: a
/// drawing element is selected, so '-' erases it as in ALPHA-T.
pub fn hit_action(c: char, picking: bool, selected: bool) -> Option<Hit> {
    if picking {
        // ALPHA-T: Enter accepts each point, 3 finishes the contour
        return matches!(c, '3' | '\r' | '\n').then_some(Hit::PickDone);
    }
    if matches!(c, '\r' | '\n') {
        return Some(Hit::Accept);
    }
    if c == '-' && selected {
        return Some(Hit::Erase);
    }
    if ENTRY_CHARS.contains(c) {
        return Some(Hit::Type);
    }
    let lower = c.to_lowercase().next()?;
    HIT_KEYS.iter().find(|(k, _)| *k == lower).map(|(_, h)| *h)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn letters_and_layouts() {
        assert_eq!(hit_action('L', false, false), Some(Hit::FindLine));
        assert_eq!(hit_action('+', false, false), Some(Hit::Freehand));
        assert_eq!(hit_action('-', false, false), Some(Hit::Type));
        assert_eq!(hit_action('-', false, true), Some(Hit::Erase));
        assert_eq!(hit_action('\r', false, false), Some(Hit::Accept));
        assert_eq!(hit_action('3', true, false), Some(Hit::PickDone));
        assert_eq!(hit_action('a', true, false), None);
        assert_eq!(hit_action('j', false, false), None);
        // Ukrainian "ф" on the A key
        assert_eq!(key_char('ф', 38), 'a');
        assert_eq!(key_char('a', 56), 'a');
        assert_eq!(hit_action(key_char('Ф', 38), false, false), Some(Hit::FindArc));
    }
}
