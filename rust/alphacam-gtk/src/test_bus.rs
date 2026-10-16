//! The end-to-end tests' view of the window (ALPHALINK_TEST=1 only; tests/e2e drive the app with real pointer and
//! touch events through libei and need to know where to put them).
//!
//! In test mode the window is fullscreen, so its surface starts at the monitor's top left with no shadow or top
//! bar in between: widget bounds in surface coordinates are screen coordinates (logical pixels). The session bus
//! name io.github._0wn3r.AlphaLinkCAM.Test, object /io/github/_0wn3r/AlphaLinkCAM/Test, gives them:
//!
//!   Widgets() -> a(ssssdddd)   every mapped widget: buildable id, widget name, CSS name, its text (label,
//!                              tooltip or title), x, y, width, height
//!   Bounds(s key) -> (bdddd)   the first mapped widget whose buildable id, widget name or text is `key`
//!   Widgets2() -> a(ssssddddbbbbs)  the same, then sensitive, active (toggle, check, switch, switch row),
//!                              selected (list row), has focus, and value (an entry's or editable row's text, a
//!                              text view's buffer, a spin row's value as shown)
//!   Title() -> s               the active window's title
//!   State() -> s               JSON: the page, the view (scale, ox, oy), the tool, the simulation position, the
//!                              number of elements, the operations' titles and the selected one, the read-out, the
//!                              Program page's font size and caret line, and
//!                              the drawing's selected element, ALPHA-T cursor, picking and construction
//!   Screenshot(s path) -> b    the active window rendered to a PNG (for failed tests)
//!
//! Popovers (menus) are surfaces of their own: their widgets are placed by the popover's position on its parent.

use adw::prelude::*;
use gtk::{gdk, gio, glib};

pub const BUS_NAME: &str = "io.github._0wn3r.AlphaLinkCAM.Test";
pub const OBJECT_PATH: &str = "/io/github/_0wn3r/AlphaLinkCAM/Test";

const INTERFACE: &str = r#"<node>
  <interface name="io.github._0wn3r.AlphaLinkCAM.Test">
    <method name="Widgets"><arg type="a(ssssdddd)" direction="out"/></method>
    <method name="Bounds"><arg name="key" type="s" direction="in"/><arg type="(bdddd)" direction="out"/></method>
    <method name="Widgets2"><arg type="a(ssssddddbbbbs)" direction="out"/></method>
    <method name="Title"><arg type="s" direction="out"/></method>
    <method name="State"><arg type="s" direction="out"/></method>
    <method name="Screenshot"><arg name="path" type="s" direction="in"/><arg type="b" direction="out"/></method>
  </interface>
</node>"#;

pub fn enabled() -> bool {
    std::env::var("ALPHALINK_TEST").is_ok_and(|v| v == "1")
}

/// What a test can match a widget by: its label, tooltip or title.
fn text(w: &gtk::Widget) -> String {
    if let Some(l) = w.downcast_ref::<gtk::Label>() {
        return l.label().into();
    }
    if let Some(b) = w.downcast_ref::<gtk::Button>()
        && let Some(l) = b.label()
    {
        return l.into();
    }
    if let Some(r) = w.downcast_ref::<adw::PreferencesRow>() {
        return r.title().into();
    }
    w.tooltip_text().map(Into::into).unwrap_or_default()
}

/// One mapped widget as the tests see it.
struct Widget {
    id: String,
    name: String,
    css: String,
    text: String,
    /// x, y, width, height in screen (toplevel surface) coordinates
    rect: (f64, f64, f64, f64),
    sensitive: bool,
    /// a toggle button, check button, switch or switch row that is on
    active: bool,
    /// a selected list row
    selected: bool,
    focus: bool,
    /// what it holds: an entry's or editable row's text, a text view's buffer, a spin row's value
    value: String,
}

type Row = (String, String, String, String, f64, f64, f64, f64);
type Row2 = (String, String, String, String, f64, f64, f64, f64, bool, bool, bool, bool, String);

fn active(w: &gtk::Widget) -> bool {
    if let Some(t) = w.downcast_ref::<gtk::ToggleButton>() {
        return t.is_active();
    }
    if let Some(c) = w.downcast_ref::<gtk::CheckButton>() {
        return c.is_active();
    }
    if let Some(s) = w.downcast_ref::<gtk::Switch>() {
        return s.is_active();
    }
    w.downcast_ref::<adw::SwitchRow>().is_some_and(|r| r.is_active())
}

fn value(w: &gtk::Widget) -> String {
    if let Some(e) = w.dynamic_cast_ref::<gtk::Editable>() {
        return e.text().into();
    }
    if let Some(v) = w.downcast_ref::<gtk::TextView>() {
        let buf = v.buffer();
        return buf.text(&buf.start_iter(), &buf.end_iter(), false).into();
    }
    String::new()
}

/// Where a native's surface lies on its toplevel's surface: popovers are surfaces of their own, placed relative to
/// their parent's surface.
fn surface_offset(native: &gtk::Native) -> (f64, f64) {
    let Some(popup) = native.surface().and_then(|s| s.downcast::<gdk::Popup>().ok()) else {
        return (0.0, 0.0);
    };
    let (px, py) = (popup.position_x() as f64, popup.position_y() as f64);
    let parent = native.upcast_ref::<gtk::Widget>().parent().and_then(|p| p.native());
    let (ox, oy) = parent.map(|p| surface_offset(&p)).unwrap_or((0.0, 0.0));
    (px + ox, py + oy)
}

fn collect(root: &gtk::Widget, out: &mut Vec<Widget>) {
    let mut child = root.first_child();
    while let Some(w) = child {
        if w.is_mapped() {
            if let Some(native) = w.native()
                && let Some(b) = w.compute_bounds(native.upcast_ref::<gtk::Widget>())
            {
                // widget -> its native -> the native's surface (shadow, margins) -> the toplevel's surface
                let (tx, ty) = native.surface_transform();
                let (ox, oy) = surface_offset(&native);
                let id = w.dynamic_cast_ref::<gtk::Buildable>().and_then(|b| b.buildable_id()).unwrap_or_default();
                out.push(Widget {
                    id: id.into(),
                    name: w.widget_name().into(),
                    css: w.css_name().into(),
                    text: text(&w),
                    rect: (b.x() as f64 + tx + ox, b.y() as f64 + ty + oy, b.width() as f64, b.height() as f64),
                    sensitive: w.is_sensitive(),
                    active: active(&w),
                    selected: w.downcast_ref::<gtk::ListBoxRow>().is_some_and(|r| r.is_selected()),
                    focus: w.has_focus(),
                    value: value(&w),
                });
            }
            collect(&w, out);
        }
        child = w.next_sibling();
    }
}

/// Every mapped widget of every window (dialogs are windows of their own until they are attached) and of their
/// popovers, in screen coordinates (the windows are fullscreen in test mode).
fn widgets(app: &gtk::Application) -> Vec<Widget> {
    let mut out = vec![];
    for win in app.windows() {
        if win.is_mapped() {
            collect(win.upcast_ref(), &mut out);
        }
    }
    out
}

fn row(w: &Widget) -> Row {
    (w.id.clone(), w.name.clone(), w.css.clone(), w.text.clone(), w.rect.0, w.rect.1, w.rect.2, w.rect.3)
}

fn row2(w: &Widget) -> Row2 {
    let (id, name, css, text, x, y, wd, h) = row(w);
    (id, name, css, text, x, y, wd, h, w.sensitive, w.active, w.selected, w.focus, w.value.clone())
}

/// Own the test bus name and answer the tests' questions (only when [`enabled`]). The app itself is NON_UNIQUE
/// and has no bus connection of its own.
pub fn register(app: &gtk::Application) {
    let app = app.downgrade();
    let id = gio::bus_own_name(gio::BusType::Session, BUS_NAME, gio::BusNameOwnerFlags::NONE,
        move |connection, _| serve(&connection, app.clone()), |_, _| {}, |_, _| {});
    let _ = id; // never unowned: the name stays for the life of the process
}

fn serve(connection: &gio::DBusConnection, app: glib::WeakRef<gtk::Application>) {
    let info = gio::DBusNodeInfo::for_xml(INTERFACE).expect("valid introspection");
    let _ = connection
        .register_object(OBJECT_PATH, &info.interfaces()[0])
        .method_call(move |_, _, _, _, method, params, invocation| {
            let Some(app) = app.upgrade() else {
                return;
            };
            let rows = widgets(&app);
            match method {
                "Widgets" => invocation.return_value(Some(&(rows.iter().map(row).collect::<Vec<_>>(),).to_variant())),
                "Widgets2" => {
                    invocation.return_value(Some(&(rows.iter().map(row2).collect::<Vec<_>>(),).to_variant()))
                }
                "Bounds" => {
                    let key: String = params.get::<(String,)>().map(|(k,)| k).unwrap_or_default();
                    let hit = rows.iter().find(|r| r.id == key || r.name == key || r.text == key);
                    let answer = match hit {
                        Some(r) => (true, r.rect.0, r.rect.1, r.rect.2, r.rect.3),
                        None => (false, 0.0, 0.0, 0.0, 0.0),
                    };
                    invocation.return_value(Some(&(answer,).to_variant()));
                }
                "Title" => {
                    let title = app.active_window().and_then(|w| w.title()).map(String::from).unwrap_or_default();
                    invocation.return_value(Some(&(title,).to_variant()));
                }
                "State" => {
                    let state = app
                        .active_window()
                        .and_downcast::<crate::window::MainWindow>()
                        .or_else(|| app.windows().into_iter().find_map(|w| w.downcast().ok()))
                        .map(|w| w.test_state().to_string())
                        .unwrap_or_default();
                    invocation.return_value(Some(&(state,).to_variant()));
                }
                "Screenshot" => {
                    let path: String = params.get::<(String,)>().map(|(k,)| k).unwrap_or_default();
                    invocation.return_value(Some(&(screenshot(&app, &path),).to_variant()));
                }
                _ => invocation.return_error(gio::DBusError::UnknownMethod, method),
            }
        })
        .build();
}

/// The active window as GTK draws it, saved as a PNG.
fn screenshot(app: &gtk::Application, path: &str) -> bool {
    let Some(win) = app.active_window() else {
        return false;
    };
    let (w, h) = (win.width() as f64, win.height() as f64);
    let paintable = gtk::WidgetPaintable::new(Some(&win));
    let snapshot = gtk::Snapshot::new();
    paintable.snapshot(&snapshot, w, h);
    let (Some(node), Some(renderer)) = (snapshot.to_node(), win.renderer()) else {
        return false;
    };
    renderer.render_texture(node, None).save_to_png(path).is_ok()
}
