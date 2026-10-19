//! Machine page (DESIGN.md 6/7, TOUCH.md 7.4): port, live modem lines, send the program, back up / restore the
//! Manual Ops, loopback test. Transfers run in a worker thread; a dialog shows progress and can cancel (machine.py,
//! ui/machine.blp, ui/machine-dialogs.blp, ui/transfer-dialog.blp).

use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::error::{Error, Result};
use alphacam::link::serial_link::{
    LOOPBACK_MESSAGE, Port, SendOptions, TransferLog, is_bluetooth, list_ports, loopback, manops_load_bytes, open_port,
    receive, send,
};
use gtk::{gio, glib};

use crate::hold_button::HoldButton;
use crate::i18n::{fill, gettext, n_, tr};
use crate::settings::{settings, update};
use crate::ui::{builder, chosen, object};
use crate::window::MainWindow;

const STEPS_SEND: &str =
    "On the lathe: CAM key, 4 “LOAD CAM FILE”, then touch the ← arrow. Then hold “Send to the Lathe” here.";
const STEPS_BACKUP: &str = "Start the backup here first, then on the lathe: in Manual Ops touch the ops file icon, 1 \
                            “OP. SAVE/RESTORE”, then the → arrow.";
const STEPS_RESTORE: &str = "On the lathe: in Manual Ops touch the ops file icon, 1 “OP. SAVE/RESTORE”, then the ← \
                             arrow. Only Manual Ops files saved from this lathe: programs not made for the Alpha-T can \
                             corrupt its macros (Alphalink-T manual).";
const STEPS_LOOP: &str = "Unplug the cable from the lathe and bridge pins 2 and 3 of the adapter, then run the test.";

/// Where the transfer logs (and the raw bytes of each backup) are kept: GLib's user state dir (XDG_STATE_HOME,
/// else ~/.local/state; glib::user_state_dir needs GLib 2.72 bindings the crate does not enable).
pub fn log_dir() -> PathBuf {
    let state = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| glib::home_dir().join(".local").join("state"));
    state.join("alphalink-cam").join("logs")
}

/// The local time in strftime format (GLib's, which has the same fields used here).
fn now(format: &str) -> String {
    glib::DateTime::now_local().and_then(|d| d.format(format)).map(|s| s.to_string()).unwrap_or_default()
}

// --- the transfer dialog ------------------------------------------------------------------------------------

mod dialog_imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/transfer-dialog.ui")]
    pub struct TransferDialog {
        #[template_child]
        pub state: TemplateChild<gtk::Label>,
        #[template_child]
        pub bar: TemplateChild<gtk::ProgressBar>,
        #[template_child]
        pub detail: TemplateChild<gtk::Label>,
        #[template_child]
        pub button: TemplateChild<gtk::Button>,
        pub cancel: Arc<AtomicBool>,
        pub finished: Cell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for TransferDialog {
        const NAME: &'static str = "AlphaLinkTransferDialog";
        type Type = super::TransferDialog;
        type ParentType = adw::Dialog;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            super::bind_dialog_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for TransferDialog {}
    impl WidgetImpl for TransferDialog {}
    impl AdwDialogImpl for TransferDialog {}
}

glib::wrapper! {
    /// Progress of one transfer; Cancel stops the worker at the next byte.
    pub struct TransferDialog(ObjectSubclass<dialog_imp::TransferDialog>)
        @extends adw::Dialog, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

/// Template handlers are not swapped: the template object is the last value.
fn bind_dialog_callbacks(klass: &mut <dialog_imp::TransferDialog as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&TransferDialog));
    let handlers: [Handler; 1] = [
        ("_cancel_or_close", |page| { page.cancel_or_close(); }),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            if let Some(page) = values.last().and_then(|v| v.get::<TransferDialog>().ok()) {
                handler(&page);
            }
            None
        });
    }
}

impl TransferDialog {
    pub fn new(title: &str) -> Self {
        glib::Object::builder().property("title", title).build()
    }

    fn cancel_or_close(&self) {
        let imp = self.imp();
        if imp.finished.get() {
            self.set_can_close(true);
            self.close();
        } else {
            imp.cancel.store(true, Ordering::SeqCst);
            imp.state.set_label(&gettext("Cancelling…"));
        }
    }

    pub fn finish(&self, ok: bool, text: &str) {
        let imp = self.imp();
        imp.finished.set(true);
        imp.state.set_label(text);
        imp.button.set_label(&gettext("_Close"));
        if ok {
            imp.bar.set_fraction(1.0);
        }
    }

    fn apply(&self, ui: Ui) {
        let imp = self.imp();
        match ui {
            Ui::State(text) => imp.state.set_label(&text),
            Ui::Button(text) => imp.button.set_label(&text),
            Ui::Bytes(done, total, text) => {
                match total.filter(|t| *t > 0) {
                    Some(total) => {
                        imp.bar.set_fraction(done as f64 / total as f64);
                        imp.bar.set_text(Some(&tr("{done} / {total} bytes", &[("done", &done), ("total", &total)])));
                    }
                    None => {
                        imp.bar.pulse();
                        imp.bar.set_text(Some(&tr("{done} bytes", &[("done", &done)])));
                    }
                }
                imp.state.set_label(&text);
            }
        }
    }
}

/// What the worker shows in the dialog, in order.
enum Ui {
    State(String),
    Button(String),
    /// bytes done, of total (None: not known), and the state text
    Bytes(usize, Option<usize>, String),
}

/// The worker's side of the dialog: its Cancel, and the updates the main loop shows.
#[derive(Clone)]
struct Ctx {
    cancel: Arc<AtomicBool>,
    events: Arc<Mutex<Vec<Ui>>>,
}

impl Ctx {
    fn post(&self, ui: Ui) {
        let mut events = self.events.lock().unwrap_or_else(|e| e.into_inner());
        // a newer count replaces one not shown yet (a pulse is a step of its own)
        if matches!((&ui, events.last()), (Ui::Bytes(_, Some(_), _), Some(Ui::Bytes(_, Some(_), _)))) {
            events.pop();
        }
        events.push(ui);
    }

    fn progress(&self, text: String) -> impl FnMut(usize, Option<usize>) + '_ {
        move |done, total| self.post(Ui::Bytes(done, total, text.clone()))
    }
}

// --- the page -----------------------------------------------------------------------------------------------

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/machine.ui")]
    pub struct MachinePage {
        #[template_child]
        pub port_row: TemplateChild<adw::ComboRow>,
        #[template_child]
        pub port_model: TemplateChild<gtk::StringList>,
        #[template_child]
        pub lines_row: TemplateChild<adw::ActionRow>,
        #[template_child]
        pub line_cts: TemplateChild<gtk::Label>,
        #[template_child]
        pub line_dsr: TemplateChild<gtk::Label>,
        #[template_child]
        pub line_cd: TemplateChild<gtk::Label>,
        #[template_child]
        pub line_ri: TemplateChild<gtk::Label>,
        #[template_child]
        pub delay_row: TemplateChild<adw::SpinRow>,
        #[template_child]
        pub wait_row: TemplateChild<adw::SwitchRow>,
        #[template_child]
        pub parity_row: TemplateChild<adw::SwitchRow>,
        #[template_child]
        pub send_group: TemplateChild<adw::PreferencesGroup>,
        #[template_child]
        pub send_info: TemplateChild<adw::ActionRow>,
        #[template_child]
        pub send_button: TemplateChild<HoldButton>,
        #[template_child]
        pub mops_group: TemplateChild<adw::PreferencesGroup>,
        #[template_child]
        pub restore_row: TemplateChild<adw::ActionRow>,
        #[template_child]
        pub restore_button: TemplateChild<HoldButton>,
        #[template_child]
        pub test_group: TemplateChild<adw::PreferencesGroup>,
        pub window: glib::WeakRef<MainWindow>,
        pub data: RefCell<Option<Vec<u8>>>,
        pub program_info: RefCell<String>,
        pub poll: RefCell<Option<glib::SourceId>>,
        /// the port the modem lines are read from, and its path
        pub poll_port: RefCell<Option<(String, Box<dyn Port>)>>,
        pub busy: Cell<bool>,
        pub restoring: Cell<bool>,
        pub delay_id: RefCell<Option<glib::SourceId>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MachinePage {
        const NAME: &'static str = "AlphaLinkMachinePage";
        type Type = super::MachinePage;
        type ParentType = gtk::Box;

        fn class_init(klass: &mut Self::Class) {
            HoldButton::ensure_type();
            klass.bind_template();
            super::bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for MachinePage {}
    impl WidgetImpl for MachinePage {}
    impl BoxImpl for MachinePage {}
}

glib::wrapper! {
    pub struct MachinePage(ObjectSubclass<imp::MachinePage>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}


/// Template handlers are not swapped: the template object is the last value.
fn bind_callbacks(klass: &mut <imp::MachinePage as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&MachinePage));
    let handlers: [Handler; 5] = [
        ("_rescan", |page| { page.refresh_ports(); }),
        ("_confirm_send", |page| { page.confirm_send(); }),
        ("_backup", |page| { page.backup(); }),
        ("_restore_pick", |page| { page.restore_pick(); }),
        ("_loopback", |page| { page.loopback_ask(); }),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            if let Some(page) = values.last().and_then(|v| v.get::<MachinePage>().ok()) {
                handler(&page);
            }
            None
        });
    }
}

impl MachinePage {
    pub fn new(window: &MainWindow) -> Self {
        let page: Self = glib::Object::new();
        let imp = page.imp();
        imp.window.set(Some(window));
        imp.send_group.set_description(Some(&gettext(STEPS_SEND)));
        imp.mops_group.set_description(Some(&gettext(STEPS_BACKUP)));
        imp.restore_row.set_subtitle(&gettext(STEPS_RESTORE));
        imp.test_group.set_description(Some(&gettext(STEPS_LOOP)));
        // the program's line carries the part's name, which is not markup
        imp.send_info.set_use_markup(false);
        let st = settings();
        imp.delay_row.set_value(st.char_delay_ms);
        crate::op_editor::keypad_on_touch(&*imp.delay_row);
        imp.delay_row.connect_value_notify(glib::clone!(#[weak] page, move |row| page.delay_changed(row)));
        imp.wait_row.set_active(st.wait_xon);
        imp.wait_row.connect_active_notify(|r| update(|s| s.wait_xon = r.is_active()));
        imp.parity_row.set_active(st.soft_parity);
        imp.parity_row.connect_active_notify(|r| update(|s| s.soft_parity = r.is_active()));
        page.refresh_ports();
        imp.port_row.connect_selected_notify(glib::clone!(#[weak] page, move |_| {
            page.restart_poll();
            page.remember_port();
            page.port_kind();
        }));
        page.port_kind();
        page.connect_map(|page| page.restart_poll());
        page.connect_unmap(|page| page.stop_poll());
        page
    }

    fn window(&self) -> Option<MainWindow> {
        self.imp().window.upgrade()
    }

    // --- state ------------------------------------------------------------------------------------------

    /// The program to send (None: the part does not post) and its one-line description.
    pub fn set_program(&self, data: Option<Vec<u8>>, info: &str) {
        let imp = self.imp();
        let size = data.as_ref().filter(|d| !d.is_empty()).map(Vec::len);
        imp.data.replace(data);
        imp.program_info.replace(info.to_string());
        if let Some(size) = size {
            imp.send_info.set_title(info);
            let secs = size as f64 * 11.0 / 9600.0 + size as f64 * imp.delay_row.value() / 1000.0;
            imp.send_info
                .set_subtitle(&tr("{size} bytes, about {secs} s", &[("size", &size), ("secs", &format!("{secs:.0}"))]));
        } else {
            imp.send_info.set_title(&gettext("No program"));
            imp.send_info.set_subtitle(&gettext("Open a part that posts without errors"));
        }
        imp.send_button.set_sensitive(size.is_some());
    }

    /// The selected port's device path (or bt:// address).
    pub fn port(&self) -> Option<String> {
        let item = self.imp().port_row.selected_item().and_downcast::<gtk::StringObject>()?;
        Some(item.string().split("  ").next().unwrap_or_default().to_string())
    }

    /// Bluetooth ports: the adapter carries the line settings, has no modem lines, and adds latency, so the delay
    /// per character should be longer (the lathe must stop within 10 characters of its DC3).
    fn port_kind(&self) {
        let imp = self.imp();
        let bt = is_bluetooth(&self.port().unwrap_or_default());
        imp.parity_row.set_visible(bt);
        imp.lines_row.set_visible(!bt);
        imp.delay_row.set_subtitle(&if bt {
            gettext("Over Bluetooth use 5 ms or more: the lathe must stop within 10 characters of its pause (DC3)")
        } else {
            gettext("2 ms works on the 400T; 0 sends at full speed")
        });
    }

    fn remember_port(&self) {
        if let Some(port) = self.port().filter(|p| !self.imp().restoring.get() && !p.is_empty()) {
            update(|s| s.serial_port = port);
        }
    }

    pub fn refresh_ports(&self) {
        let imp = self.imp();
        let mut items: Vec<String> = list_ports().into_iter().map(|(dev, desc)| format!("{dev}  {desc}")).collect();
        if items.is_empty() {
            items.push(format!("/dev/ttyUSB0  {}", gettext("(not present)")));
        }
        imp.restoring.set(true);
        let refs: Vec<&str> = items.iter().map(String::as_str).collect();
        imp.port_model.splice(0, imp.port_model.n_items(), &refs);
        let wanted = settings().serial_port;
        for (i, item) in items.iter().enumerate() {
            if !wanted.is_empty() && item.split("  ").next() == Some(wanted.as_str()) {
                imp.port_row.set_selected(i as u32);
            }
        }
        imp.restoring.set(false);
    }

    // --- modem lines (5 Hz while visible, not during transfers) ----------------------------------------

    fn restart_poll(&self) {
        self.stop_poll();
        // opening a Bluetooth port connects: no polling
        if self.is_mapped() && !is_bluetooth(&self.port().unwrap_or_default()) {
            let page = self.downgrade();
            let id = glib::timeout_add_local(Duration::from_millis(200), move || match page.upgrade() {
                Some(page) => {
                    page.poll_lines();
                    glib::ControlFlow::Continue
                }
                None => glib::ControlFlow::Break,
            });
            self.imp().poll.replace(Some(id));
        }
    }

    fn stop_poll(&self) {
        let imp = self.imp();
        if let Some(id) = imp.poll.take() {
            id.remove();
        }
        imp.poll_port.take(); // closes it
    }

    fn poll_lines(&self) {
        let imp = self.imp();
        if imp.busy.get() {
            return;
        }
        let port = self.port().unwrap_or_default();
        let lines = {
            let mut slot = imp.poll_port.borrow_mut();
            if slot.as_ref().is_none_or(|(path, _)| *path != port) {
                *slot = None;
                match open_port(&port, false) {
                    Ok(p) => *slot = Some((port.clone(), p)),
                    Err(e) => {
                        drop(slot);
                        imp.lines_row.set_subtitle(&port_error(&e.to_string()));
                        self.show_lines([None; 4]);
                        return;
                    }
                }
            }
            slot.as_mut().map(|(_, p)| p.modem_lines()).unwrap_or_default()
        };
        imp.lines_row.set_subtitle(&port);
        self.show_lines([lines.cts, lines.dsr, lines.cd, lines.ri]);
    }

    fn show_lines(&self, lines: [Option<bool>; 4]) {
        let imp = self.imp();
        for (lbl, on) in [&imp.line_cts, &imp.line_dsr, &imp.line_cd, &imp.line_ri].into_iter().zip(lines) {
            lbl.remove_css_class("success");
            lbl.remove_css_class("dim-label");
            lbl.add_css_class(if on == Some(true) { "success" } else { "dim-label" });
        }
    }

    // --- transfers --------------------------------------------------------------------------------------

    /// Run work(port, ctx, log) in a thread with the port open; done(dialog, result) on the main loop.
    fn run<R: Send + 'static>(
        &self,
        title: String,
        work: impl FnOnce(&mut dyn Port, &Ctx, &mut TransferLog) -> Result<R> + Send + 'static,
        done: impl FnOnce(&TransferDialog, R) + 'static,
    ) {
        let Some(window) = self.window() else { return };
        let imp = self.imp();
        let dlg = TransferDialog::new(&title);
        dlg.present(Some(&window));
        imp.busy.set(true);
        self.stop_poll();
        window.parts().input_mode.set_locked(true);
        let port = self.port().unwrap_or_default();
        let soft_parity = is_bluetooth(&port) && imp.parity_row.is_active();
        let ctx = Ctx { cancel: dlg.imp().cancel.clone(), events: Default::default() };
        type Outcome<R> = Option<(std::result::Result<R, String>, TransferLog)>;
        let outcome: Arc<Mutex<Outcome<R>>> = Arc::default();

        let (worker_ctx, worker_outcome, worker_port) = (ctx.clone(), outcome.clone(), port.clone());
        let cancelled = gettext("Cancelled");
        std::thread::spawn(move || {
            let mut log = TransferLog::new();
            // the port closes when it is dropped, whatever the work did
            let result = open_port(&worker_port, soft_parity)
                .and_then(|mut ser| work(&mut *ser, &worker_ctx, &mut log))
                .map_err(|e| match e {
                    Error::Cancelled(_) => cancelled,
                    e => port_error(&e.to_string()),
                });
            log.add(format!("result: {}", result.as_ref().err().map_or("ok", String::as_str)));
            *worker_outcome.lock().unwrap_or_else(|e| e.into_inner()) = Some((result, log));
        });

        let (page, window) = (self.downgrade(), window.downgrade());
        let mut done = Some(done);
        glib::timeout_add_local(Duration::from_millis(50), move || {
            let events: Vec<Ui> = std::mem::take(&mut *ctx.events.lock().unwrap_or_else(|e| e.into_inner()));
            for ui in events {
                dlg.apply(ui);
            }
            let Some((result, log)) = outcome.lock().unwrap_or_else(|e| e.into_inner()).take() else {
                return glib::ControlFlow::Continue;
            };
            if let Some(page) = page.upgrade() {
                page.imp().busy.set(false);
                page.restart_poll();
            }
            if let Some(window) = window.upgrade() {
                window.parts().input_mode.set_locked(false);
            }
            // the transfer is over either way; never leave the dialog stuck open
            let path = match write_log(&title, &port, &log) {
                Ok(path) => path.display().to_string(),
                Err(e) => tr("not written ({error})", &[("error", &e)]),
            };
            match result {
                Err(error) => {
                    dlg.finish(false, &error);
                    dlg.imp().detail.set_label(&tr("Log: {path}", &[("path", &path)]));
                }
                Ok(result) => {
                    if let Some(done) = done.take() {
                        done(&dlg, result);
                    }
                }
            }
            glib::ControlFlow::Break
        });
    }

    fn send_options(&self) -> SendOptions {
        let imp = self.imp();
        SendOptions { wait_xon: imp.wait_row.is_active(), char_delay_ms: imp.delay_row.value(), ..Default::default() }
    }

    fn confirm_send(&self) {
        let imp = self.imp();
        let (Some(window), Some(data)) = (self.window(), imp.data.borrow().clone().filter(|d| !d.is_empty())) else {
            return;
        };
        let steps = gettext("The lathe must be on CAM, 4 “LOAD CAM FILE”, with the ← arrow touched. Then press Send.");
        let alert: adw::AlertDialog = object(&builder("machine-dialogs"), "send_alert");
        alert.set_body(&tr(
            "{info}\n{size} bytes to {port}.\n\n{steps}",
            &[
                ("info", &*imp.program_info.borrow()),
                ("size", &data.len()),
                ("port", &self.port().unwrap_or_default()),
                ("steps", &steps),
            ],
        ));
        let memory_kb = settings().lathe_memory_kb;
        if data.len() as f64 > memory_kb * 1024.0 {
            // ATSEND's "File too big for standard memory" warning: the lathe may have extra memory, so it only asks
            alert.set_heading(Some(&gettext("Program too big for the lathe?")));
            alert.set_body(&format!("{}\n\n{}", tr(
                "The lathe holds {memory} KB of CAM programs (Preferences) and this program is {size} bytes. Send \
                 it only if the lathe has more memory; otherwise split the part into two programs.",
                &[("memory", &memory_kb), ("size", &data.len())],
            ), alert.body()));
            alert.set_response_appearance("send", adw::ResponseAppearance::Destructive);
        }
        // exactly the program this dialog describes, even if the part changes meanwhile
        alert.connect_response(None, glib::clone!(#[weak(rename_to = this)] self, move |_, r| {
            if r == "send" {
                this.send(data.clone());
            }
        }));
        alert.present(Some(&window));
    }

    fn send(&self, data: Vec<u8>) {
        let opts = self.send_options();
        let (waiting, sending) = (gettext("Waiting for the lathe (DC1)…"), gettext("Sending…"));
        self.run(
            gettext("Send CAM Program"),
            move |ser, ctx, log| {
                ctx.post(Ui::State(waiting));
                let mut progress = ctx.progress(sending);
                let mut sent = 0;
                let result = send(ser, &data, &opts, Some(&mut |d, t| {
                    sent = d;
                    progress(d, Some(t))
                }), Some(&ctx.cancel), Some(&mut *log));
                result.map_err(|e| lost_while_sending(e, sent, data.len(), log, n_!(
                    "The connection was lost after {done} of {total} bytes. The program on the lathe is incomplete: \
                     check the cable and send it again."
                )))
            },
            |dlg, n| dlg.finish(true, &tr("Sent {n} bytes. Check the program list on the control.", &[("n", &n)])),
        );
    }

    fn backup(&self) {
        let waiting = gettext("Waiting for data: on the lathe touch the → arrow under “OP. SAVE/RESTORE”");
        let paused = gettext("The lathe paused {s} s ago; waiting. Press Stop and Keep when it has finished.");
        let (stop_keep, receiving) = (gettext("_Stop and Keep"), gettext("Receiving…"));
        let logs = log_dir();
        let work = move |ser: &mut dyn Port, ctx: &Ctx, log: &mut TransferLog| {
            ctx.post(Ui::State(waiting));
            ctx.post(Ui::Button(stop_keep));
            let mut quiet = |seconds: f64| ctx.post(Ui::State(fill(&paused, &[("s", &(seconds as i64))])));
            let mut progress = ctx.progress(receiving);
            // no time limit: the control pauses between operations; the user stops it, and keeps what arrived
            let received = receive(
                ser,
                Some(&mut |d| progress(d, None)),
                Some(&ctx.cancel),
                None,
                None,
                Some(&mut *log),
                Some(&mut quiet),
            );
            let (data, ended) = match received {
                Ok(data) => (data, Ended::Complete),
                // the end mark never came: keep what arrived, marked partial
                Err(Error::TransferTimeout { data, .. } | Error::Cancelled(data)) if !data.is_empty() => {
                    (data, Ended::NoEndMark)
                }
                Err(Error::ConnectionLost { data, .. }) => (data, Ended::Lost),
                Err(e) => return Err(e),
            };
            // the raw bytes next to the log, whatever happens to the save dialog
            let raw = logs.join(now("%Y%m%d-%H%M%S-manops.raw"));
            match std::fs::create_dir_all(&logs).and_then(|_| std::fs::write(&raw, &data)) {
                Ok(()) => log.add(format!("raw bytes kept in {}", raw.display())),
                Err(err) => log.add(format!("raw bytes not kept: {err}")),
            }
            Ok((data, ended))
        };
        let page = self.downgrade();
        self.run(gettext("Back Up Manual Ops"), work, move |dlg, (data, ended): (Vec<u8>, Ended)| {
            let n = data.len();
            match ended {
                Ended::Complete => dlg.finish(true, &tr("Received {n} bytes.", &[("n", &n)])),
                Ended::NoEndMark => dlg.finish(
                    false,
                    &tr(
                        "Received {n} bytes without the end mark. If the lathe had finished, the backup is probably \
                         whole; it is saved marked PARTIAL. Keep the lathe's ops until a restore of it has been \
                         checked.",
                        &[("n", &n)],
                    ),
                ),
                Ended::Lost => dlg.finish(
                    false,
                    &tr(
                        "The connection was lost after {n} bytes. The backup is incomplete; it is saved marked \
                         PARTIAL. Check the cable and back up again.",
                        &[("n", &n)],
                    ),
                ),
            }
            let complete = ended == Ended::Complete;
            let Some(window) = page.upgrade().and_then(|p| p.window()) else { return };
            let name = now(if complete { "MANOPS-%Y%m%d-%H%M.MOP" } else { "MANOPS-%Y%m%d-%H%M-PARTIAL.MOP" });
            let fd = gtk::FileDialog::builder().title(gettext("Save Manual Ops Backup")).initial_name(&name).build();
            fd.save(Some(&window), gio::Cancellable::NONE, glib::clone!(#[weak] window, move |res| {
                let fallback = log_dir().parent().map(Path::to_path_buf).unwrap_or_default().join(&name);
                let target = chosen(res).unwrap_or_else(|| fallback.clone());
                // never lose a backup: fall back to the app folder
                for place in [target, fallback] {
                    let written = std::fs::create_dir_all(place.parent().unwrap_or(Path::new(".")))
                        .and_then(|_| std::fs::write(&place, &data)); // raw bytes, before any decoding
                    let shown = place.display().to_string();
                    if let Err(e) = written {
                        window.toast(&tr("Could not save {path}: {error}", &[("path", &shown), ("error", &e)]));
                        continue;
                    }
                    window.toast(&tr("Backup saved to {path}", &[("path", &shown)]));
                    return;
                }
            }));
        });
    }

    fn restore_pick(&self) {
        let Some(window) = self.window() else { return };
        let fd = gtk::FileDialog::builder().title(gettext("Restore Manual Ops From")).build();
        fd.open(Some(&window), gio::Cancellable::NONE, glib::clone!(#[weak(rename_to = this)] self, #[weak] window,
            move |res| {
                let Some(path) = chosen(res) else { return };
                let data = match std::fs::read(&path) {
                    Ok(data) => data,
                    Err(e) => {
                        window.toast(&tr("Could not read the file: {error}", &[("error", &e)]));
                        return;
                    }
                };
                if !is_manops(&data) {
                    window.toast(&gettext("This is not a Manual Ops backup (it must start and end with %)."));
                    return;
                }
                let alert: adw::AlertDialog = object(&builder("machine-dialogs"), "restore_alert");
                let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                alert.set_body(&tr(
                    "{name}, {size} bytes.\n\n{steps}",
                    &[("name", &name), ("size", &data.len()), ("steps", &gettext(STEPS_RESTORE))],
                ));
                alert.connect_response(None, glib::clone!(#[weak] this, move |_, r| {
                    if r == "restore" {
                        this.restore(&data);
                    }
                }));
                alert.present(Some(&window));
            }));
    }

    fn restore(&self, data: &[u8]) {
        let payload = manops_load_bytes(data);
        let opts = self.send_options();
        let sending = gettext("Sending…");
        self.run(
            gettext("Restore Manual Ops"),
            move |ser, ctx, log| {
                let mut progress = ctx.progress(sending);
                let mut sent = 0;
                let result = send(ser, &payload, &opts, Some(&mut |d, t| {
                    sent = d;
                    progress(d, Some(t))
                }), Some(&ctx.cancel), Some(&mut *log));
                result.map_err(|e| lost_while_sending(e, sent, payload.len(), log, n_!(
                    "The connection was lost after {done} of {total} bytes. The Manual Ops on the lathe are \
                     incomplete: check the cable and restore them again."
                )))
            },
            |dlg, n| dlg.finish(true, &tr("Sent {n} bytes.", &[("n", &n)])),
        );
    }

    /// Save the delay once the spin row settles, not on every step.
    fn delay_changed(&self, row: &adw::SpinRow) {
        let imp = self.imp();
        if let Some(id) = imp.delay_id.take() {
            id.remove();
        }
        let (page, row) = (self.downgrade(), row.downgrade());
        let id = glib::timeout_add_local_once(Duration::from_millis(500), move || {
            if let Some(page) = page.upgrade() {
                page.imp().delay_id.take();
            }
            if let Some(row) = row.upgrade() {
                update(|s| s.char_delay_ms = row.value());
            }
        });
        imp.delay_id.replace(Some(id));
    }

    /// Writes a short test text to the port: only with the cable off the lathe, so ask first.
    fn loopback_ask(&self) {
        let Some(window) = self.window() else { return };
        let alert: adw::AlertDialog = object(&builder("machine-dialogs"), "loopback_alert");
        alert.connect_response(None, glib::clone!(#[weak(rename_to = this)] self, move |_, r| {
            if r == "run" {
                this.run_loopback();
            }
        }));
        alert.present(Some(&window));
    }

    fn run_loopback(&self) {
        self.run(
            gettext("Loopback Test"),
            |ser, _ctx, _log| loopback(ser, LOOPBACK_MESSAGE, Duration::from_millis(500)),
            |dlg, (ok, got)| {
                dlg.finish(
                    ok,
                    &if ok {
                        gettext("Loopback OK: the adapter and cable work.")
                    } else {
                        tr("Loopback failed: got {data}. Check the 2-3 bridge.", &[("data", &bytes_repr(&got))])
                    },
                )
            },
        );
    }
}

/// The port error as the user can act on it.
/// How a Manual Ops backup ended.
#[derive(Clone, Copy, PartialEq)]
enum Ended {
    Complete,
    /// the lathe stopped (or the user did) before the end mark
    NoEndMark,
    /// the port failed: a cable pulled, an adapter unplugged
    Lost,
}

/// A send whose port failed after some bytes went out: say what that leaves on the lathe (message: an n_! text with
/// {done} and {total}); the system's own words go to the log.
fn lost_while_sending(e: Error, sent: usize, total: usize, log: &mut TransferLog, message: &str) -> Error {
    match e {
        Error::Io(system) if sent > 0 => {
            log.add(format!("port failed after {sent} of {total} bytes: {system}"));
            Error::Io(tr(message, &[("done", &sent), ("total", &total)]))
        }
        e => e,
    }
}

fn port_error(text: &str) -> String {
    if text.contains("Permission denied") {
        return gettext(
            "Permission denied: add yourself to the dialout group (sudo usermod -aG dialout $USER), then log in again",
        );
    }
    if text.contains("No such file") || text.contains("could not open port") {
        return gettext("Port not found: is the USB adapter plugged in?");
    }
    text.to_string()
}

/// Python's bytes.strip() whitespace.
fn is_py_space(b: &u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | b'\x0b' | b'\x0c')
}

/// A Manual Ops backup starts and ends with % (whitespace around it allowed).
fn is_manops(data: &[u8]) -> bool {
    let start = data.iter().position(|b| !is_py_space(b));
    let end = data.iter().rposition(|b| !is_py_space(b));
    matches!((start, end), (Some(s), Some(e)) if data[s] == b'%' && data[e] == b'%')
}

/// Python's repr() of bytes: b'...', with \r, \n, \t and \xNN escapes.
fn bytes_repr(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') { '"' } else { '\'' };
    let mut out = format!("b{quote}");
    for &b in data {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b'\t' => out.push_str("\\t"),
            b if b as char == quote => {
                out.push('\\');
                out.push(quote);
            }
            0x20..0x7F => out.push(b as char),
            b => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote);
    out
}

fn write_log(title: &str, port: &str, log: &TransferLog) -> std::io::Result<PathBuf> {
    let dir = log_dir();
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("{}{}.log", now("%Y%m%d-%H%M%S-"), title.to_lowercase().replace(' ', "-")));
    std::fs::write(&path, format!("{title} on {port}\n{}", log.text()))?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manops_check_and_repr() {
        assert!(is_manops(b"\r\n%\r\nO1\r\n%\x0b\n"));
        assert!(!is_manops(b"O1\r\n%"));
        assert!(!is_manops(b"  "));
        assert_eq!(bytes_repr(b"%\r\nO1'\x93\\"), r#"b"%\r\nO1'\x93\\""#);
        assert_eq!(bytes_repr(b""), "b''");
    }

    #[test]
    fn lost_mid_send_says_what_is_left_on_the_lathe() {
        let mut log = TransferLog::new();
        let text = "lost after {done} of {total}";
        let e = lost_while_sending(Error::Io("Input/output error".into()), 601, 4166, &mut log, text);
        assert_eq!(e.to_string(), "lost after 601 of 4166");
        assert!(log.text().contains("port failed after 601 of 4166 bytes: Input/output error"));
        // before the first byte the port's own message stays (it may be "Permission denied" or similar)
        let e = lost_while_sending(Error::Io("Permission denied".into()), 0, 4166, &mut log, text);
        assert_eq!(e.to_string(), "Permission denied");
    }

    #[test]
    fn port_errors() {
        assert!(port_error("Permission denied").starts_with("Permission denied: add yourself"));
        assert_eq!(port_error("No such file or directory"), "Port not found: is the USB adapter plugged in?");
        assert_eq!(port_error("Write timeout"), "Write timeout");
    }

    /// A whole send over a pty pair, as the page's worker runs it: the control asks with DC1, the bytes arrive
    /// unchanged, progress reaches the end.
    #[test]
    fn send_over_pty() {
        use serialport::SerialPort;
        use std::io::{Read, Write};
        let (mut control, pc) = serialport::TTYPort::pair().expect("pty pair");
        let pc_path = pc.name().expect("pty name");
        let data = b"%\r\nO0001\r\nM30\r\n%\r\n".to_vec();
        let ctx = Ctx { cancel: Arc::default(), events: Default::default() };
        let worker = {
            let (data, ctx) = (data.clone(), ctx.clone());
            std::thread::spawn(move || {
                let mut ser = open_port(&pc_path, false).expect("open the pty");
                let opts = SendOptions { char_delay_ms: 0.0, ..Default::default() };
                let mut log = TransferLog::new();
                let mut progress = ctx.progress("Sending…".into());
                send(&mut *ser, &data, &opts, Some(&mut |d, t| progress(d, Some(t))), Some(&ctx.cancel), Some(&mut log))
            })
        };
        std::thread::sleep(Duration::from_millis(200));
        control.write_all(&[alphacam::link::serial_link::XON]).unwrap();
        control.set_timeout(Duration::from_secs(5)).unwrap();
        let mut got = vec![0u8; data.len()];
        control.read_exact(&mut got).unwrap();
        assert_eq!(worker.join().unwrap().unwrap(), data.len());
        assert_eq!(got.iter().map(|b| b & 0x7F).collect::<Vec<_>>(), data);
        let events = ctx.events.lock().unwrap();
        assert!(matches!(events.last(), Some(Ui::Bytes(n, Some(t), _)) if *n == data.len() && *t == data.len()));
    }
}
