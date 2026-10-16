//! adw::Application: opens files given on the command line, app-level actions.

use adw::prelude::*;
use gtk::{gdk, gio};

use crate::i18n::gettext;

pub const APP_ID: &str = "io.github._0wn3r.AlphaLinkCAM";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Debugging / screenshots: --select N (1-based operation), --page part|program, --touch.
#[derive(Debug, Default, Clone)]
pub struct StartOptions {
    pub select_op: Option<usize>,
    pub page: Option<String>,
    pub touch: bool,
}

impl StartOptions {
    /// Take the app's own options out of argv (GApplication sees the rest).
    pub fn take(argv: &mut Vec<String>) -> Self {
        let mut options = StartOptions::default();
        let value = |name: &str, argv: &mut Vec<String>| {
            let i = argv.iter().position(|a| a == name)?;
            let v = argv.get(i + 1).cloned();
            argv.drain(i..(i + 2).min(argv.len()));
            v
        };
        options.select_op = value("--select", argv).and_then(|v| v.parse::<usize>().ok()).map(|n| n.saturating_sub(1));
        options.page = value("--page", argv);
        if let Some(i) = argv.iter().position(|a| a == "--touch") {
            argv.remove(i);
            options.touch = true;
        }
        options
    }
}

pub fn build(options: StartOptions) -> adw::Application {
    let app = adw::Application::builder()
        .application_id(APP_ID)
        .flags(gio::ApplicationFlags::HANDLES_OPEN | gio::ApplicationFlags::NON_UNIQUE)
        .build();
    app.connect_startup(|app| {
        let css = gtk::CssProvider::new();
        css.load_from_resource("/io/github/_0wn3r/AlphaLinkCAM/style.css");
        gtk::style_context_add_provider_for_display(
            &gdk::Display::default().expect("a display"),
            &css,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
        let quit = gio::ActionEntry::builder("quit").activate(|app: &adw::Application, _, _| app.quit()).build();
        let about = gio::ActionEntry::builder("about").activate(|app: &adw::Application, _, _| about(app)).build();
        app.add_action_entries([quit, about]);
        app.set_accels_for_action("app.quit", &["<Control>q"]);
    });
    app.connect_activate(|app| window(app).present());
    app.connect_open(move |app, files, _hint| {
        let win = window(app);
        win.present();
        if let Some(path) = files.first().and_then(|f| f.path()) {
            crate::window::open(&win, &path, &options);
        }
    });
    app
}

fn window(app: &adw::Application) -> gtk::Window {
    app.active_window().unwrap_or_else(|| crate::window::new(app))
}

fn about(app: &adw::Application) {
    let dialog = adw::AboutDialog::builder()
        .application_name("AlphaLink CAM")
        .application_icon("applications-engineering-symbolic")
        .version(VERSION)
        .developer_name("Eduard")
        .comments(gettext("Lathe CAM for the Harrison Alpha 400T, writing ALPHA-T programs."))
        .translator_credits(gettext("translator-credits"))
        .website("https://github.com/0wn3r/alphalink")
        .license_type(gtk::License::Unknown)
        .build();
    dialog.present(app.active_window().as_ref());
}

