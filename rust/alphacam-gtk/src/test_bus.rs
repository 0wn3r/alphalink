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
//!   Screenshot(s path) -> b    the active window rendered to a PNG (for failed tests)

use adw::prelude::*;
use gtk::{gio, glib};

pub const BUS_NAME: &str = "io.github._0wn3r.AlphaLinkCAM.Test";
pub const OBJECT_PATH: &str = "/io/github/_0wn3r/AlphaLinkCAM/Test";

const INTERFACE: &str = r#"<node>
  <interface name="io.github._0wn3r.AlphaLinkCAM.Test">
    <method name="Widgets"><arg type="a(ssssdddd)" direction="out"/></method>
    <method name="Bounds"><arg name="key" type="s" direction="in"/><arg type="(bdddd)" direction="out"/></method>
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

type Row = (String, String, String, String, f64, f64, f64, f64);

fn collect(root: &gtk::Widget, out: &mut Vec<Row>) {
    let mut child = root.first_child();
    while let Some(w) = child {
        if w.is_mapped() {
            if let Some(b) = w.compute_bounds(root.root().as_ref().map(|r| r.upcast_ref::<gtk::Widget>()).unwrap_or(root)) {
                let id = w.dynamic_cast_ref::<gtk::Buildable>().and_then(|b| b.buildable_id()).unwrap_or_default();
                out.push((id.into(), w.widget_name().into(), w.css_name().into(), text(&w), b.x() as f64,
                          b.y() as f64, b.width() as f64, b.height() as f64));
            }
            collect(&w, out);
        }
        child = w.next_sibling();
    }
}

/// Every mapped widget of every window (dialogs are windows of their own until they are attached), in surface
/// coordinates of its window.
fn widgets(app: &gtk::Application) -> Vec<Row> {
    let mut out = vec![];
    for win in app.windows() {
        if win.is_mapped() {
            // the surface may be larger than the window (a shadow): shift to the surface's origin
            let (sx, sy) = win.surface_transform();
            let mut rows = vec![];
            collect(win.upcast_ref(), &mut rows);
            out.extend(rows.into_iter().map(|mut r| {
                r.4 += sx;
                r.5 += sy;
                r
            }));
        }
    }
    out
}

/// Own the test bus name and answer the tests' questions (only when [`enabled`]). The app itself is NON_UNIQUE
/// and has no bus connection of its own.
pub fn register(app: &gtk::Application) {
    let app = app.downgrade();
    let id = gio::bus_own_name(gio::BusType::Session, BUS_NAME, gio::BusNameOwnerFlags::NONE,
        move |connection, _| serve(&connection, app.clone()), |_, _| {}, |_, _| {});
    std::mem::forget(id); // owned for the life of the process
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
                "Widgets" => invocation.return_value(Some(&(rows,).to_variant())),
                "Bounds" => {
                    let key: String = params.get::<(String,)>().map(|(k,)| k).unwrap_or_default();
                    let hit = rows.iter().find(|r| r.0 == key || r.1 == key || r.3 == key);
                    let answer = match hit {
                        Some(r) => (true, r.4, r.5, r.6, r.7),
                        None => (false, 0.0, 0.0, 0.0, 0.0),
                    };
                    invocation.return_value(Some(&(answer,).to_variant()));
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
