//! Setup sheet (ALPHA-T's ATPRINT / APRINT, without printing): per operation the tool, start, limits, offsets,
//! passes and depth of cut, and optionally every tool move pass by pass. Built from the preview, so the numbers are
//! the ones in the program (unscaled). (report.py, ui/sheet-page.blp)

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::path::Path;

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::cycletime::{Estimate, Machine, estimate, mmss};
use alphacam::model::tools::{ToolLibrary, standard_library};
use alphacam::model::{Operation, Project};
use alphacam::preview::{Block, Preview};
use alphacam::py::fmt_g;
use alphacam::toolpath::MoveKind;
use gtk::{cairo, gio, glib, pango};

use crate::i18n::{fill, gettext, ngettext, tr};
use crate::settings::settings;
use crate::ui::chosen;
use crate::window::MainWindow;

/// Month abbreviations, translated (strftime's %b is always English here: C locale).
const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// The sheet's name for a block kind (msgids), as ALPHA-T prints it.
fn kind_name(name: &str) -> Option<&'static str> {
    Some(match name {
        "tool_change" => "TOOL CHANGE",
        "area_clear" | "area_clear_internal" => "AREA CLEARANCE",
        "face_clear" => "FACE CLEAR",
        "groove" => "GROOVING",
        "finish" | "finish_internal" => "FINISH PASS",
        "area_prefinish" => "PRE-FINISH PASS",
        "drill" => "DRILLING",
        "thread" => "THREADING",
        _ => return None,
    })
}

/// The kind's name translated; kinds without one shown upper-cased.
fn kind_label(name: &str) -> String {
    gettext(&kind_name(name).map_or_else(|| name.to_uppercase(), str::to_string))
}

fn rule() -> String {
    format!(" {}", "-".repeat(78))
}

/// The tool's name: the part's own tools first, then the library, then ALPHA-T's standard ones.
pub fn tool_name(project: &Project, number: i32, library: Option<&ToolLibrary>) -> String {
    project
        .tools
        .iter()
        .chain(library.map(|l| l.tools.iter()).into_iter().flatten())
        .chain(standard_library().get(number))
        .find(|t| t.number == number)
        .map(|t| t.name.clone())
        .unwrap_or_default()
}

/// The cycle time settings (Preferences).
pub fn machine() -> Machine {
    let st = settings();
    Machine { spindle_max: st.spindle_max, rapid: st.rapid_rate, tool_change: st.tool_change_s }
}

/// "About 6:52 (5:52 cutting, 2 tool changes)": the estimate in one line.
pub fn time_summary(est: &Estimate) -> String {
    let n = est.tool_changes;
    tr(
        "About {total} ({cutting} cutting, {changes})",
        &[
            ("total", &mmss(est.total())),
            ("cutting", &mmss(est.cutting())),
            ("changes", &fill(&ngettext("{n} tool change", "{n} tool changes", n as u32), &[("n", &n)])),
        ],
    )
}

/// (offset X, offset Z, depth of cut) of the operation, where it has them.
fn op_offsets(op: Option<&Operation>) -> (f64, f64, Option<f64>) {
    match op {
        Some(Operation::AreaClear(o)) => (o.offset_x, o.offset_z, Some(o.depth_of_cut)),
        Some(Operation::FaceClear(o)) => (o.offset_x, o.offset_z, Some(o.depth_of_cut)),
        Some(Operation::FinishPass(o)) => (o.offset_x, o.offset_z, None),
        _ => (0.0, 0.0, None),
    }
}

/// The program's tool number as the library's.
fn tool_no(block: &Block) -> i64 {
    block.tool().unwrap_or(0)
}

/// The setup sheet as monospaced text, laid out like ALPHA-T's print, with the cycle time estimate.
pub fn sheet_text(
    project: &Project,
    pv: Option<&Preview>,
    title: &str,
    moves: bool,
    library: Option<&ToolLibrary>,
    est: Option<&Estimate>,
) -> String {
    let today = glib::DateTime::now_local().expect("local time");
    let month = gettext(MONTHS[today.month() as usize - 1]);
    let mut out = vec![
        " ".to_string(),
        format!("  {} : {title:<40} {} : {:02} {month} {}", gettext("TITLE"), gettext("DATE"), today.day_of_month(),
                today.year()),
        " ".to_string(),
    ];
    let Some(pv) = pv.filter(|pv| pv.program.is_some()) else {
        out.push(gettext("  (no program)"));
        return out.join("\n");
    };
    let own;
    let est = match est {
        Some(e) => e,
        None => {
            own = estimate(project, pv, library, Some(machine()));
            &own
        }
    };
    out.push(format!("  {} : {}     {} : {}", gettext("Program"), project.program_number, gettext("Cycle time"),
                     time_summary(est)));
    out.push(" ".into());
    let mut op_no = 0;
    let mut warnings: BTreeMap<usize, Vec<String>> = alphacam::checks::tool_warnings(project, library);
    if pv.data.is_some() {
        for f in alphacam::safety::check(project, pv, library) {
            if let Some(i) = f.op_index {
                warnings.entry(i).or_default().push(f.message);
            }
        }
    }
    let (rule, tool_no_label) = (rule(), gettext("Tool No."));
    for op in &pv.ops {
        for (bi, block) in op.blocks.iter().enumerate() {
            let name = block.op_name();
            if name == "empty" || name == "end" {
                continue;
            }
            let tool = tool_no(block);
            let tname = tool_name(project, tool as i32, library);
            let (sx, sz) = block.start().unwrap_or((0.0, 0.0));
            let feed = fmt_g(block.feed().unwrap_or(0.0));
            if name == "tool_change" {
                out.push(rule.clone());
                out.push(format!("  {}  : {:<18} {tool_no_label} : {tool:3}    {tname}", gettext("Operation"),
                                 kind_label(&name)));
                out.push(format!("  {} X : {sx:8.3}      Z : {sz:8.3}      {} : {feed}", gettext("Tool change"),
                                 gettext("Feed")));
            } else {
                op_no += 1;
                let (lx, lz) = block.limit().unwrap_or((0.0, 0.0));
                let src = op.op_index.and_then(|i| project.operations.get(i));
                let (ox, oz, depth) = op_offsets(src);
                out.push(rule.clone());
                out.push(format!("  {}  : {:<18} {tool_no_label} : {tool:3}    {tname}", gettext("Operation"),
                                 kind_label(&name)));
                out.push(format!("  {} : {sx:8.3}      {} : {lx:8.3}       {} : {ox:7.3}", gettext("Tool Start X"),
                                 gettext("Maximum X"), gettext("Offset X")));
                out.push(format!("  {} : {sz:8.3}      {} : {lz:8.3}       {} : {oz:7.3}", gettext("Tool Start Z"),
                                 gettext("Maximum Z"), gettext("Offset Z")));
                let mut line = format!("  {} : {op_no:2}     {} : {:2}", gettext("Op. No."), gettext("Total Passes"),
                                       block.declared_passes().unwrap_or(0));
                if let Some(depth) = depth {
                    line += &format!("     {} : {depth:7.3}", gettext("Depth Of Cut"));
                }
                let feed_label = if name == "thread" { gettext("Pitch") } else { gettext("Feed") };
                line += &format!("     {feed_label} : {feed}");
                out.push(line);
                if let Some(seconds) = est.seconds.get(&op.op_index).filter(|_| bi == 0) {
                    out.push(format!("  {} : {}", gettext("Time"), mmss(*seconds)));
                }
                for msg in op.op_index.and_then(|i| warnings.get(&i)).into_iter().flatten() {
                    out.push(format!("  ! {msg}"));
                }
            }
            // where the control can pick the program up again (a broken tool replaced, say)
            let restarts: Vec<i64> = block.passes.iter().filter_map(|ps| ps.restart.map(|r| r.0)).collect();
            if let (Some(first), Some(last)) = (restarts.first(), restarts.last()) {
                let span = if restarts.len() == 1 { first.to_string() } else { format!("{first}–{last}") };
                let each = if restarts.len() == 1 { String::new() } else { format!("  ({})", gettext("one per pass")) };
                out.push(format!("  {} : N{:<5} {} : {span}{each}", gettext("Block"), block.label,
                                 gettext("Restart #140")));
            }
            out.push(rule.clone());
            if moves {
                out.extend(move_lines(block));
            }
            out.push(" ".into());
        }
    }
    out.join("\n")
}

/// Every move of the block, pass by pass.
fn move_lines(block: &Block) -> Vec<String> {
    let mut lines = vec![];
    for p in &block.passes {
        if p.number > 1 {
            lines.push(format!("  {} : {:2}", gettext("Pass No."), p.number));
        }
        for m in &p.moves {
            let (Some(x), Some(z)) = (m.x, m.z) else { continue };
            let xz = format!("X {x:8.3}, Z {z:8.3}");
            match m.kind {
                MoveKind::Rapid => lines.push(format!("  {:<11}: {xz}", gettext("RAPID TO"))),
                MoveKind::Feed => lines.push(format!("  {:<11}: {xz}", gettext("FEED  TO"))),
                MoveKind::ArcCw | MoveKind::ArcCcw => {
                    let label = gettext(if m.kind == MoveKind::ArcCw { "ARC CW TO" } else { "ARC CCW TO" });
                    lines.push(format!("  {label:<11}: {xz}   {} X {:8.3}, Z {:8.3}", gettext("centre"),
                                       m.centre_x.unwrap_or(0.0), m.centre_z.unwrap_or(0.0)));
                }
                MoveKind::Thread => lines.push(format!("  {:<11}: {xz}", gettext("THREAD TO"))),
                MoveKind::Dwell => lines.push(format!("  {:<11}: {} {}", gettext("DWELL"),
                                                      fmt_g(m.dwell_revs.unwrap_or(0.0)), gettext("revs"))),
            }
        }
    }
    lines
}

/// Python's str.capitalize: the first letter upper case, the rest lower.
fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    chars.next().map_or_else(String::new, |c| c.to_uppercase().chain(chars.flat_map(char::to_lowercase)).collect())
}

/// The tools in the order they are needed: (number, name, the operations it does).
pub fn tool_order(project: &Project, pv: Option<&Preview>, library: Option<&ToolLibrary>)
                  -> Vec<(i64, String, Vec<String>)> {
    let mut out: Vec<(i64, String, Vec<String>)> = vec![];
    let Some(pv) = pv.filter(|pv| pv.program.is_some()) else { return out };
    for op in &pv.ops {
        for block in &op.blocks {
            let name = block.op_name();
            let Some(tool) = block.tool() else { continue };
            if matches!(name.as_str(), "empty" | "end" | "tool_change") {
                continue;
            }
            let what = capitalize(&kind_label(&name));
            match out.last_mut() {
                Some(last) if last.0 == tool => {
                    if !last.2.contains(&what) {
                        last.2.push(what);
                    }
                }
                _ => out.push((tool, tool_name(project, tool as i32, library), vec![what])),
            }
        }
    }
    out
}

/// The setup sheet as a printable A4 PDF: first page the part with its toolpaths, the program, billet and cycle
/// time, the tools in loading order and the operator's notes; then the sheet itself. Returns the page count.
pub fn write_pdf(
    path: &Path,
    project: &Project,
    pv: &Preview,
    title: &str,
    canvas: &crate::canvas::LatheCanvas,
    library: Option<&ToolLibrary>,
    moves: bool,
) -> Result<usize, cairo::Error> {
    const W: f64 = 595.0; // A4 in points
    const H: f64 = 842.0;
    const M: f64 = 40.0; // margin
    let est = estimate(project, pv, library, Some(machine()));
    let surface = cairo::PdfSurface::new(W, H, path)?;
    let cr = cairo::Context::new(&surface)?;
    let mut pages = 1;

    let text = |x: f64, y: f64, markup: &str, size: f64, mono: bool| -> f64 {
        let layout = pangocairo::functions::create_layout(&cr);
        let font = format!("{} {}", if mono { "Monospace" } else { "Sans" }, fmt_g(size));
        layout.set_font_description(Some(&pango::FontDescription::from_string(&font)));
        layout.set_width(((W - 2.0 * M) * pango::SCALE as f64) as i32);
        layout.set_wrap(pango::WrapMode::WordChar);
        layout.set_markup(markup);
        cr.set_source_rgb(0.1, 0.1, 0.12);
        cr.move_to(x, y);
        pangocairo::functions::show_layout(&cr, &layout);
        layout.pixel_extents().1.height() as f64
    };
    let esc = |s: &str| glib::markup_escape_text(s).to_string();

    let unit = if project.units == "mm" { "mm" } else { "inch" };
    let b = &project.billet;
    let bore = if b.bore != 0.0 { format!(", {} Ø{}", gettext("bore"), fmt_g(b.bore)) } else { String::new() };
    let date = glib::DateTime::now_local().and_then(|d| d.format("%Y-%m-%d")).map(|s| s.to_string());
    let facts = [
        (gettext("Program"), project.program_number.to_string()),
        (gettext("Units"), unit.to_string()),
        (gettext("Billet"), format!("Ø{} × {}{bore}", fmt_g(b.diameter), fmt_g(b.length))),
        (gettext("Cycle time"), time_summary(&est)),
        (gettext("Date"), date.unwrap_or_default()),
    ];
    let mut y = M;
    y += text(M, y, &format!("<b>{}</b>", esc(title)), 18.0, false) + 6.0;
    let facts: Vec<String> = facts.iter().map(|(k, v)| format!("{}: <b>{}</b>", esc(k), esc(v))).collect();
    y += text(M, y, &facts.join("   "), 9.0, false) + 10.0;
    let pic_h = 300.0;
    cr.save()?;
    cr.rectangle(M, y, W - 2.0 * M, pic_h);
    cr.clip();
    cr.translate(M, y);
    canvas.render_to(&cr, W - 2.0 * M, pic_h);
    cr.restore()?;
    cr.set_source_rgb(0.7, 0.7, 0.72);
    cr.set_line_width(0.5);
    cr.rectangle(M, y, W - 2.0 * M, pic_h);
    cr.stroke()?;
    y += pic_h + 14.0;
    y += text(M, y, &format!("<b>{}</b>", esc(&gettext("Tools, in loading order"))), 11.0, false) + 4.0;
    for (n, (number, name, what)) in tool_order(project, Some(pv), library).iter().enumerate() {
        let line = format!("{}.  <b>T{number}</b>  {}  —  {}", n + 1, esc(name), esc(&what.join(", ")));
        y += text(M, y, &line, 9.0, false) + 2.0;
    }
    let notes = project.notes.trim();
    if !notes.is_empty() {
        y += 10.0;
        y += text(M, y, &format!("<b>{}</b>", esc(&gettext("Notes"))), 11.0, false) + 4.0;
        text(M, y, &esc(notes), 9.0, false);
    }
    cr.show_page()?;
    let sheet = sheet_text(project, Some(pv), title, moves, library, Some(&est));
    let lines: Vec<&str> = sheet.lines().collect();
    let per_page = ((H - 2.0 * M) / 10.5) as usize;
    for chunk in lines.chunks(per_page) {
        pages += 1;
        text(M, M, &esc(&chunk.join("\n")), 7.5, true);
        cr.show_page()?;
    }
    drop(cr);
    surface.finish();
    surface.status()?;
    Ok(pages)
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/sheet-page.ui")]
    pub struct SheetPage {
        #[template_child]
        pub moves_switch: TemplateChild<gtk::Switch>,
        #[template_child]
        pub time_label: TemplateChild<gtk::Label>,
        #[template_child]
        pub notes: TemplateChild<gtk::TextView>,
        #[template_child]
        pub view: TemplateChild<gtk::TextView>,

        pub window: RefCell<glib::WeakRef<MainWindow>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SheetPage {
        const NAME: &'static str = "AlphaLinkSheetPage";
        type Type = super::SheetPage;
        type ParentType = gtk::Box;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            super::bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for SheetPage {}
    impl WidgetImpl for SheetPage {}
    impl BoxImpl for SheetPage {}
}

glib::wrapper! {
    /// Shows the setup sheet; a switch adds the tool moves; Save as Text writes it to a file.
    pub struct SheetPage(ObjectSubclass<imp::SheetPage>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

/// Binds the handlers ui/sheet-page.blp names. The template scope passes (emitter, signal arguments..., template
/// object); the handlers here need only the last, the page itself.
fn bind_callbacks(klass: &mut <imp::SheetPage as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&SheetPage));
    let handlers: [Handler; 4] = [
        ("_moves_toggled", |page| page.refresh()),
        ("_notes_changed", |page| page.notes_changed()),
        ("_save_pdf", |page| page.save_pdf()),
        ("_save", |page| page.save_text()),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            if let Some(page) = values.last().and_then(|v| v.get::<SheetPage>().ok()) {
                handler(&page);
            }
            None
        });
    }
}

fn buffer_text(view: &gtk::TextView) -> String {
    let buf = view.buffer();
    buf.text(&buf.start_iter(), &buf.end_iter(), false).to_string()
}

impl SheetPage {
    pub fn new(window: &MainWindow) -> Self {
        let page: Self = glib::Object::new();
        page.imp().window.borrow().set(Some(window));
        page
    }

    fn window(&self) -> Option<MainWindow> {
        self.imp().window.borrow().upgrade()
    }

    pub fn refresh(&self) {
        let Some(w) = self.window() else { return };
        let Some(project) = w.project() else { return };
        let imp = self.imp();
        let pv = w.preview();
        let library = w.library();
        let pv = pv.as_deref().filter(|pv| pv.program.is_some());
        let est = pv.map(|pv| estimate(&project, pv, Some(&library), Some(machine())));
        imp.time_label.set_label(&est.as_ref().map(time_summary).unwrap_or_default());
        let text = sheet_text(&project, pv, &self.title(&w), imp.moves_switch.is_active(), Some(&library),
                              est.as_ref());
        imp.view.buffer().set_text(&text);
        if buffer_text(&imp.notes) != project.notes {
            imp.notes.buffer().set_text(&project.notes);
        }
    }

    fn title(&self, w: &MainWindow) -> String {
        let mut title = match w.path() {
            Some(p) => p.file_stem().map(|s| s.to_string_lossy().to_uppercase()).unwrap_or_default(),
            None => w.import_name().unwrap_or_else(|| gettext("UNTITLED")).to_uppercase(),
        };
        if w.setup_count() > 1 {
            title += &format!(" {}", tr("SETUP {n}", &[("n", &(w.setup_index() + 1))]));
        }
        title
    }

    fn notes_changed(&self) {
        let Some(w) = self.window() else { return };
        let Some(project) = w.project() else { return };
        let text = buffer_text(&self.imp().notes);
        if text != project.notes {
            w.apply_edit(&gettext("Notes"), move |p| {
                p.notes = text;
                Ok(())
            });
        }
    }

    fn save_pdf(&self) {
        let Some(w) = self.window() else { return };
        if w.project().is_none() || w.preview().is_none_or(|pv| pv.program.is_none()) {
            w.toast(&gettext("There is no program to print yet"));
            return;
        }
        self.notes_changed();
        let stem = w
            .path()
            .and_then(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
            .or_else(|| w.import_name())
            .unwrap_or_else(|| "part".into());
        let dialog = gtk::FileDialog::builder()
            .title(gettext("Save Setup Sheet as PDF"))
            .initial_name(format!("{stem}-sheet.pdf"))
            .build();
        let page = self.downgrade();
        dialog.save(Some(&w), gio::Cancellable::NONE, move |res| {
            let (Some(path), Some(page)) = (chosen(res), page.upgrade()) else { return };
            let Some(w) = page.window() else { return };
            let name = path.file_name().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
            // the project may have changed while the dialog was open (the notes edit above re-posts)
            let (Some(project), Some(pv)) = (w.project(), w.preview()) else { return };
            let library = w.library();
            let result = write_pdf(&path, &project, &pv, &page.title(&w), &w.canvas(), Some(&library),
                                   page.imp().moves_switch.is_active());
            match result {
                Ok(_) => w.toast(&tr("Saved {name}", &[("name", &name)])),
                Err(e) => w.toast(&tr("Could not save {name}: {error}", &[("name", &name), ("error", &e)])),
            }
        });
    }

    fn save_text(&self) {
        let Some(w) = self.window() else { return };
        let stem = w.path().and_then(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()));
        let dialog = gtk::FileDialog::builder()
            .title(gettext("Save Setup Sheet"))
            .initial_name(format!("{}-sheet.txt", stem.as_deref().unwrap_or("part")))
            .build();
        let page = self.downgrade();
        dialog.save(Some(&w), gio::Cancellable::NONE, move |res| {
            let (Some(path), Some(page)) = (chosen(res), page.upgrade()) else { return };
            let Some(w) = page.window() else { return };
            let name = path.file_name().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
            let text = buffer_text(&page.imp().view) + "\n";
            let result = std::fs::write(path, text).map_err(|e| e.to_string());
            match result {
                Ok(()) => w.toast(&tr("Saved {name}", &[("name", &name)])),
                Err(e) => w.toast(&tr("Could not save {name}: {error}", &[("name", &name), ("error", &e)])),
            }
        });
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub fn example(stem: &str) -> Project {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app/examples");
        Project::load(dir.join(format!("{stem}.alcam"))).expect("example loads")
    }

    /// test_preview.py Sheet.test_setup_sheet_like_aprint (rough_and_finish.alcam posts as
    /// TOOLCHG_rough1_finish3_metric).
    #[test]
    fn setup_sheet_like_aprint() {
        let p = example("rough_and_finish");
        let pv = alphacam::preview::preview(&p, None, None).expect("posts");
        let text = sheet_text(&p, Some(&pv), "TOOLCHG", false, None, None);
        assert!(text.contains("Operation  : AREA CLEARANCE     Tool No. :   1    80 Deg Rougher 0.8R"), "{text}");
        assert!(text.contains("Maximum X :   -3.200"), "{text}"); // = #125, as APRINT shows it
        assert!(text.contains("Total Passes : 26     Depth Of Cut :   2.000"), "{text}");
        let moves = sheet_text(&p, Some(&pv), "TOOLCHG", true, None, None);
        assert!(moves.contains("  Pass No. : 26"));
        assert!(moves.contains("RAPID TO   : X   96.000, Z    5.000"));
    }

    #[test]
    fn no_program() {
        let text = sheet_text(&Project::default(), None, "PART", false, None, None);
        assert!(text.ends_with("  (no program)"));
        assert!(text.contains("  TITLE : PART"));
    }

    #[test]
    fn tools_in_loading_order() {
        let p = example("rough_and_finish");
        let pv = alphacam::preview::preview(&p, None, None).expect("posts");
        let order = tool_order(&p, Some(&pv), None);
        assert!(!order.is_empty());
        assert_eq!(order[0].0, 1);
        assert_eq!(order[0].2[0], "Area clearance");
    }

    /// The printable sheet: the part page, then the sheet text. Needs GTK (the canvas draws the part), so
    /// simulation.rs's GTK test runs it: GTK lives on one test thread.
    pub fn writes_pdf(canvas: &crate::canvas::LatheCanvas) {
        let p = std::rc::Rc::new(example("rough_and_finish"));
        let pv = std::rc::Rc::new(alphacam::preview::preview(&p, None, None).expect("posts"));
        canvas.set_project(p.clone(), pv.clone());
        let path = std::env::temp_dir().join(format!("alphalink-sheet-{}.pdf", std::process::id()));
        let pages = write_pdf(&path, &p, &pv, "ROUGH_AND_FINISH", canvas, None, true).expect("written");
        let data = std::fs::read(&path).expect("read back");
        let _ = std::fs::remove_file(&path);
        assert!(data.starts_with(b"%PDF"));
        assert!(pages > 2, "{pages}"); // with the moves the sheet runs over more than one page
        assert!(write_pdf(Path::new("/nonexistent/dir/x.pdf"), &p, &pv, "X", canvas, None, false).is_err());
    }

    #[test]
    fn capitalizes_like_python() {
        assert_eq!(capitalize("AREA CLEARANCE"), "Area clearance");
        assert_eq!(capitalize(""), "");
    }
}
