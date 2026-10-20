//! Tools page (ALPHA-T's ATTOOLS): the tool library the posts use, saved per user (tools_page.py,
//! ui/tools-page.blp).
//!
//! The numbers are the lathe's tool memory slots (manual turret with tool memory, DESIGN.md C8). The nose radius
//! changes the toolpath (imaginary tool tip), so every edit re-posts the open part. Import reads ALPHA-T's
//! ATOOLIB.TL1, with the tool pictures from the ATOOLIB.TL2 next to it; Reset goes back to ALPHA-T's standard 32 tools.

use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::io::toollib::{load_tl1, load_tl2, sibling};
use alphacam::model::tools::{TOOL_TYPES, Tool, ToolLibrary, standard_library};
use alphacam::py::{fmt_g, round};
use gtk::{gio, glib};

use crate::i18n::{gettext, tr};
use crate::op_editor::fit_spin;
use crate::ui::{builder, chosen, object};
use crate::window::MainWindow;

/// The tool types' labels (msgids), in TOOL_TYPES order.
const TYPE_LABELS: [(&str, &str); 5] = [
    ("turn", "Turning"),
    ("groove", "Grooving / parting"),
    ("thread", "Threading"),
    ("drill", "Drill"),
    ("bore", "Boring bar"),
];
const HANDS: [(&str, &str); 3] = [("R", "Right hand"), ("L", "Left hand"), ("N", "Neutral")];

fn type_label(kind: &str) -> String {
    TYPE_LABELS.iter().find(|(k, _)| *k == kind).map_or_else(|| kind.to_string(), |(_, lab)| gettext(lab))
}

pub fn library_path() -> PathBuf {
    glib::user_config_dir().join("alphalink-cam").join("tools.json")
}

/// The user's tool library, or ALPHA-T's standard one when there is none (or it is damaged).
pub fn load_library() -> ToolLibrary {
    load_library_from(&library_path())
}

pub fn load_library_from(path: &Path) -> ToolLibrary {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .and_then(|data| ToolLibrary::from_json(&data).ok())
        .unwrap_or_else(|| standard_library().clone())
}

/// A library file to import: an exported AlphaLink CAM library (.json, as [`save_library`] writes it) or ALPHA-T's
/// ATOOLIB.TL1 with the tool pictures from the .TL2 next to it. Unlike [`load_library_from`], a damaged file is an
/// error, not ALPHA-T's standard tools.
pub fn read_library_file(path: &Path) -> Result<ToolLibrary, String> {
    let is_json = path.extension().is_some_and(|e| e.eq_ignore_ascii_case("json"));
    if is_json {
        let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
        let data: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        return ToolLibrary::from_json(&data).map_err(|e| e.to_string());
    }
    let mut lib = load_tl1(path).map_err(|e| e.to_string())?;
    // ALPHA-T keeps the tool pictures next to the library
    let pictures = sibling(path, ".TL2").and_then(|tl2| load_tl2(tl2).ok()).unwrap_or_default();
    for tool in &mut lib.tools {
        tool.outline = pictures.get(&tool.number).cloned().unwrap_or_default();
    }
    Ok(lib)
}

/// Write the library as tools.json has always been written (indent 1, UTF-8 kept), in one go (glib renames a
/// temporary file into place).
pub fn save_library(lib: &ToolLibrary, path: &Path) -> std::io::Result<()> {
    use serde::Serialize;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut text = Vec::new();
    let indent_1 = serde_json::ser::PrettyFormatter::with_indent(b" ");
    let mut ser = serde_json::Serializer::with_formatter(&mut text, indent_1);
    lib.to_json().serialize(&mut ser).map_err(std::io::Error::other)?;
    text.push(b'\n');
    glib::file_set_contents(path, &text).map_err(std::io::Error::other)
}

/// The number fields of the editor: (field, label, subtitle, lo, hi, step, digits).
type NumberRow = (&'static str, &'static str, &'static str, f64, f64, f64, u32);
const NUMBER_ROWS: [NumberRow; 7] = [
    ("nose_radius", "Nose radius", "mm; changes the toolpath", 0.0, 10.0, 0.1, 3),
    ("front_angle", "Front clearance angle", "degrees from Z; negative = internal", -180.0, 180.0, 1.0, 0),
    ("rear_angle", "Rear clearance angle", "degrees from Z", -180.0, 180.0, 1.0, 0),
    ("width", "Width", "grooving tip or drill diameter, mm", 0.0, 100.0, 0.5, 2),
    ("default_feed", "Default feed", "per revolution, for new operations", 0.001, 5.0, 0.01, 3),
    ("surface_speed", "Surface speed", "m/min, for the cycle time; 0 = a default for the type", 0.0, 1000.0, 10.0, 0),
    ("max_rpm", "Spindle limit", "rpm for this tool; 0 = the machine's", 0.0, 10000.0, 100.0, 0),
];

fn number_field<'a>(t: &'a mut Tool, field: &str) -> &'a mut f64 {
    match field {
        "nose_radius" => &mut t.nose_radius,
        "front_angle" => &mut t.front_angle,
        "rear_angle" => &mut t.rear_angle,
        "width" => &mut t.width,
        "default_feed" => &mut t.default_feed,
        "surface_speed" => &mut t.surface_speed,
        _ => &mut t.max_rpm,
    }
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/tools-page.ui")]
    pub struct ToolsPage {
        #[template_child]
        pub note: TemplateChild<gtk::Label>,
        #[template_child]
        pub listbox: TemplateChild<gtk::ListBox>,
        #[template_child]
        pub editor: TemplateChild<gtk::ScrolledWindow>,
        pub window: glib::WeakRef<MainWindow>,
        /// the tool number of each row, in list order
        pub numbers: RefCell<Vec<i32>>,
        pub building: Cell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ToolsPage {
        const NAME: &'static str = "AlphaLinkToolsPage";
        type Type = super::ToolsPage;
        type ParentType = gtk::Box;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            super::bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for ToolsPage {}
    impl WidgetImpl for ToolsPage {}
    impl BoxImpl for ToolsPage {}
}

glib::wrapper! {
    pub struct ToolsPage(ObjectSubclass<imp::ToolsPage>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}


/// Template handlers are not swapped: the template object is the last value.
fn bind_callbacks(klass: &mut <imp::ToolsPage as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&ToolsPage));
    let handlers: [Handler; 3] = [
        ("_show", |page| { page.show_current(); }),
        ("_add", |page| { page.add_tool(); }),
        ("_import", |page| { page.import_dialog(); }),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            if let Some(page) = values.last().and_then(|v| v.get::<ToolsPage>().ok()) {
                handler(&page);
            }
            None
        });
    }
}

impl ToolsPage {
    pub fn new(window: &MainWindow) -> Self {
        let page: Self = glib::Object::new();
        page.imp().window.set(Some(window));
        // the ⋮ menu's Reset to ALPHA-T Tools
        let actions = gio::SimpleActionGroup::new();
        let reset = gio::SimpleAction::new("reset", None);
        reset.connect_activate(glib::clone!(#[weak] page, move |_, _| page.reset()));
        actions.add_action(&reset);
        // and Export Tool Library…: the library as a file, for another install (Import reads it back)
        let export = gio::SimpleAction::new("export", None);
        export.connect_activate(glib::clone!(#[weak] page, move |_, _| page.export_dialog()));
        actions.add_action(&export);
        page.insert_action_group("tools", Some(&actions));
        page.fill(None);
        page
    }

    fn window(&self) -> Option<MainWindow> {
        self.imp().window.upgrade()
    }

    fn lib(&self) -> ToolLibrary {
        self.window().map(|w| (*w.library()).clone()).unwrap_or_default()
    }

    /// List the tools by number and select `select` (else the first).
    pub fn fill(&self, select: Option<i32>) {
        let imp = self.imp();
        imp.listbox.remove_all();
        let mut tools = self.lib().tools;
        tools.sort_by_key(|t| t.number);
        for t in &tools {
            let row = adw::ActionRow::builder()
                .title(format!("T{}  {}", t.number, t.name))
                .subtitle(format!("R{} · {} · {}", fmt_g(t.nose_radius), type_label(&t.kind), t.hand))
                .use_markup(false)
                .build();
            imp.listbox.append(&row);
        }
        imp.numbers.replace(tools.iter().map(|t| t.number).collect());
        if !tools.is_empty() {
            let idx = tools.iter().position(|t| Some(t.number) == select).unwrap_or(0);
            imp.listbox.select_row(imp.listbox.row_at_index(idx as i32).as_ref());
        }
    }

    /// The selected tool's number.
    fn current(&self) -> Option<i32> {
        let row = self.imp().listbox.selected_row()?;
        self.imp().numbers.borrow().get(usize::try_from(row.index()).ok()?).copied()
    }

    // --- editor -----------------------------------------------------------------------------------------

    fn show_current(&self) {
        let imp = self.imp();
        let Some(mut t) = self.current().and_then(|n| self.lib().get(n).cloned()) else {
            imp.editor.set_child(gtk::Widget::NONE);
            return;
        };
        imp.building.set(true);
        let page = adw::PreferencesPage::new();
        let g = adw::PreferencesGroup::builder().title(format!("T{}", t.number)).build();
        let delete = gtk::Button::builder()
            .icon_name("user-trash-symbolic")
            .tooltip_text(gettext("Delete Tool"))
            .valign(gtk::Align::Center)
            .css_classes(["flat"])
            .build();
        delete.connect_clicked(glib::clone!(#[weak(rename_to = this)] self, move |_| this.delete_tool()));
        g.set_header_suffix(Some(&delete));
        page.add(&g);

        let name = adw::EntryRow::builder().title(gettext("Name")).text(&t.name).show_apply_button(true).build();
        let fallback = t.name.clone();
        name.connect_apply(glib::clone!(#[weak(rename_to = this)] self, move |r| {
            let text: String = r.text().trim().chars().take(20).collect();
            let value = if text.is_empty() { fallback.clone() } else { text };
            this.set(true, move |t| std::mem::replace(&mut t.name, value.clone()) != value);
        }));
        g.add(&name);

        let labels: Vec<String> = TOOL_TYPES.iter().map(|k| type_label(k)).collect();
        let type_row = adw::ComboRow::builder()
            .title(gettext("Type"))
            .model(&gtk::StringList::new(&labels.iter().map(String::as_str).collect::<Vec<_>>()))
            .selected(TOOL_TYPES.iter().position(|k| *k == t.kind).unwrap_or(0) as u32)
            .build();
        type_row.connect_selected_notify(glib::clone!(#[weak(rename_to = this)] self, move |r| {
            let kind = TOOL_TYPES[(r.selected() as usize).min(TOOL_TYPES.len() - 1)];
            this.set(true, move |t| std::mem::replace(&mut t.kind, kind.into()) != kind);
        }));
        g.add(&type_row);

        let hands: Vec<String> = HANDS.iter().map(|(_, lab)| gettext(lab)).collect();
        let hand_row = adw::ComboRow::builder()
            .title(gettext("Hand"))
            .model(&gtk::StringList::new(&hands.iter().map(String::as_str).collect::<Vec<_>>()))
            .selected(HANDS.iter().position(|(k, _)| *k == t.hand).unwrap_or(0) as u32)
            .build();
        hand_row.connect_selected_notify(glib::clone!(#[weak(rename_to = this)] self, move |r| {
            let hand = HANDS[(r.selected() as usize).min(HANDS.len() - 1)].0;
            this.set(true, move |t| std::mem::replace(&mut t.hand, hand.into()) != hand);
        }));
        g.add(&hand_row);

        for (field, label, sub, lo, hi, step, digits) in NUMBER_ROWS {
            let row = adw::SpinRow::with_range(lo, hi, step);
            row.set_title(&gettext(label));
            row.set_subtitle(&gettext(sub));
            row.set_digits(digits);
            row.set_value(*number_field(&mut t, field));
            fit_spin(&row, lo, hi, digits);
            crate::op_editor::keypad_on_touch(&row);
            row.connect_value_notify(glib::clone!(#[weak(rename_to = this)] self, move |r| {
                let value = round(r.value(), digits as usize);
                // the list shows the nose radius
                this.set(field == "nose_radius", move |t| {
                    std::mem::replace(number_field(t, field), value) != value
                });
            }));
            g.add(&row);
        }

        let desc =
            adw::EntryRow::builder().title(gettext("Description")).text(&t.description).show_apply_button(true).build();
        desc.connect_apply(glib::clone!(#[weak(rename_to = this)] self, move |r| {
            let value = r.text().to_string();
            this.set(false, move |t| std::mem::replace(&mut t.description, value.clone()) != value);
        }));
        g.add(&desc);
        imp.editor.set_child(Some(&page));
        imp.building.set(false);
    }

    /// Change the selected tool; `change` returns whether anything changed. `rebuild_list`: the change shows in
    /// the list (name, nose radius, type, hand).
    fn set(&self, rebuild_list: bool, change: impl Fn(&mut Tool) -> bool) {
        let Some(number) = self.current() else { return };
        if self.imp().building.get() {
            return;
        }
        let mut lib = self.lib();
        let Some(t) = lib.tools.iter_mut().find(|t| t.number == number) else { return };
        if !change(t) {
            return;
        }
        self.changed(lib, Some(number), rebuild_list);
    }

    fn changed(&self, lib: ToolLibrary, select: Option<i32>, rebuild_list: bool) {
        let Some(w) = self.window() else { return };
        if let Err(e) = save_library(&lib, &library_path()) {
            w.toast(&tr("The tool library could not be saved: {error}", &[("error", &e)]));
        }
        w.set_library(lib);
        if rebuild_list {
            self.fill(select);
        }
        if w.has_project() {
            w.refresh(false);
        }
    }

    // --- actions ----------------------------------------------------------------------------------------

    fn add_tool(&self) {
        let mut lib = self.lib();
        let Some(n) = (1..1000).find(|i| lib.get(*i).is_none()) else { return };
        lib.tools.push(Tool { number: n, name: gettext("New tool"), ..Default::default() });
        self.changed(lib, Some(n), true);
    }

    fn delete_tool(&self) {
        let (Some(w), Some(t)) = (self.window(), self.current().and_then(|n| self.lib().get(n).cloned())) else {
            return;
        };
        let alert: adw::AlertDialog = object(&builder("tools-dialogs"), "delete_alert");
        alert.set_heading(Some(&tr("Delete T{n}?", &[("n", &t.number)])));
        alert.set_body(&tr(
            "{name} is removed from your tool library. Parts that use it fall back to ALPHA-T's standard tool with \
             that number.",
            &[("name", &t.name)],
        ));
        alert.connect_response(None, glib::clone!(#[weak(rename_to = this)] self, move |_, r| {
            if r == "delete" {
                let mut lib = this.lib();
                if let Some(i) = lib.tools.iter().position(|x| *x == t) {
                    lib.tools.remove(i);
                }
                this.changed(lib, None, true);
            }
        }));
        alert.present(Some(&w));
    }

    fn import_dialog(&self) {
        let Some(w) = self.window() else { return };
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let both = gtk::FileFilter::new();
        both.set_name(Some(&gettext("Tool libraries")));
        let json = gtk::FileFilter::new();
        json.set_name(Some(&gettext("AlphaLink CAM tool library (.json)")));
        let tl1 = gtk::FileFilter::new();
        tl1.set_name(Some(&gettext("ALPHA-T tool library (ATOOLIB.TL1)")));
        for pat in ["*.json", "*.JSON"] {
            both.add_pattern(pat);
            json.add_pattern(pat);
        }
        for pat in ["*.TL1", "*.tl1"] {
            both.add_pattern(pat);
            tl1.add_pattern(pat);
        }
        for f in [&both, &json, &tl1] {
            filters.append(f);
        }
        let dlg = gtk::FileDialog::builder().title(gettext("Import Tool Library")).filters(&filters).build();
        dlg.open(Some(&w), gio::Cancellable::NONE, glib::clone!(#[weak(rename_to = this)] self, move |res| {
            if let Some(path) = chosen(res) {
                this.import(&path);
            }
        }));
    }

    /// Replace the library with the file's; the toast's Undo brings the one before back.
    fn import(&self, path: &Path) {
        let Some(w) = self.window() else { return };
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let lib = match read_library_file(path) {
            Ok(lib) => lib,
            Err(e) => {
                w.toast(&tr("Could not import {name}: {error}", &[("name", &name), ("error", &e)]));
                return;
            }
        };
        let before = self.lib();
        let n = lib.tools.len();
        let pictures = lib.tools.iter().any(|t| !t.outline.is_empty());
        self.changed(lib, None, true);
        let msg = if pictures {
            "Imported {n} tools and their pictures from {name}"
        } else {
            "Imported {n} tools from {name}"
        };
        let toast = adw::Toast::builder()
            .title(glib::markup_escape_text(&tr(msg, &[("n", &n), ("name", &name)])))
            .button_label(gettext("_Undo"))
            .timeout(8)
            .build();
        toast.connect_button_clicked(glib::clone!(#[weak(rename_to = this)] self, move |_| {
            this.changed(before.clone(), None, true);
        }));
        w.add_toast(toast);
    }

    fn export_dialog(&self) {
        let Some(w) = self.window() else { return };
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let json = gtk::FileFilter::new();
        json.set_name(Some(&gettext("AlphaLink CAM tool library (.json)")));
        json.add_pattern("*.json");
        filters.append(&json);
        let dlg = gtk::FileDialog::builder()
            .title(gettext("Export Tool Library"))
            .initial_name("alphalink-tools.json")
            .filters(&filters)
            .build();
        dlg.save(Some(&w), gio::Cancellable::NONE, glib::clone!(#[weak(rename_to = this)] self, move |res| {
            let (Some(path), Some(w)) = (chosen(res), this.window()) else { return };
            let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            match save_library(&this.lib(), &path) {
                Ok(()) => w.toast(&tr("Saved {name}", &[("name", &name)])),
                Err(e) => w.toast(&tr("Could not save {name}: {error}", &[("name", &name), ("error", &e)])),
            }
        }));
    }

    fn reset(&self) {
        self.changed(standard_library().clone(), None, true);
        if let Some(w) = self.window() {
            w.toast(&gettext("Tool library reset to ALPHA-T's 32 standard tools"));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn library_round_trip() {
        let dir = std::env::temp_dir().join(format!("alphalink-tools-test-{}", std::process::id()));
        let path = dir.join("tools.json");
        let mut lib = standard_library().clone();
        lib.tools[0].name = "Ступінь".into();
        lib.tools[0].nose_radius = 0.6;
        save_library(&lib, &path).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.starts_with("{\n \"format\": \"alcam-tools/1\",\n \"tools\": [\n  {\n   \"type\": "));
        assert!(text.contains("Ступінь") && text.ends_with("}\n"));
        assert_eq!(load_library_from(&path), lib);
        std::fs::write(&path, "not json").unwrap();
        assert_eq!(load_library_from(&path), *standard_library());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// Export on one install, import on another (Eduard 2026-10-20): the same library comes back, and a damaged file is
    /// refused instead of silently giving ALPHA-T's standard tools.
    #[test]
    fn exported_library_imports_on_another_install() {
        let dir = std::env::temp_dir().join(format!("alphalink-tools-export-{}", std::process::id()));
        let path = dir.join("my-tools.json");
        let mut lib = standard_library().clone();
        lib.tools[2].name = "Чистовий 0.2R".into();
        lib.tools[2].nose_radius = 0.2;
        lib.tools.retain(|t| t.number != 7);
        save_library(&lib, &path).unwrap();
        assert_eq!(read_library_file(&path).unwrap(), lib);
        std::fs::write(&path, "{\"format\": \"alcam-tools/1\", \"tools\": [{\"number\": \"x\"}]}").unwrap();
        assert!(read_library_file(&path).is_err());
        std::fs::write(&path, "not json").unwrap();
        assert!(read_library_file(&path).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
