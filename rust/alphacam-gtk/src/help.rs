//! In-app help (F1): help/help.<lang>.md shown in a dialog. The help is plain Markdown so it also reads well on
//! GitHub; pulldown-cmark reads it and the headings, paragraphs, lists (two levels deep), **bold** and `code` it
//! uses become Pango markup in labels, so it works offline without a browser.
//!
//! The Training and Reference Manuals ship as PDFs (manuals/<kind>.<lang>.pdf) and open in the desktop's PDF viewer.

use std::path::PathBuf;

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};

use crate::i18n::gettext;
use crate::ui::data_dir;

pub const MANUALS: [&str; 2] = ["training", "reference"];

fn lang(lang: Option<&str>) -> String {
    let l = lang.map(str::to_string).unwrap_or_else(crate::i18n::language);
    let l = l.split('_').next().unwrap_or("").to_string();
    if l.is_empty() { "en".into() } else { l }
}

pub fn help_path(language: Option<&str>) -> PathBuf {
    let dir = data_dir().join("help");
    let p = dir.join(format!("help.{}.md", lang(language)));
    if p.exists() { p } else { dir.join("help.en.md") }
}

pub fn manual_path(kind: &str, language: Option<&str>) -> PathBuf {
    let dir = data_dir().join("manuals");
    let p = dir.join(format!("{kind}.{}.pdf", lang(language)));
    if p.exists() { p } else { dir.join(format!("{kind}.en.pdf")) }
}

/// Open a manual in the PDF viewer (through the portal inside the Flatpak); failures become a toast.
pub fn open_manual(window: &crate::window::MainWindow, kind: &str) {
    let launcher = gtk::FileLauncher::new(Some(&gio::File::for_path(manual_path(kind, None))));
    let win = window.downgrade();
    launcher.launch(Some(window), gio::Cancellable::NONE, move |res| {
        if let (Err(e), Some(win)) = (res, win.upgrade())
            && !e.matches(gtk::DialogError::Dismissed) {
                win.toast(&gettext("Could not open the manual: {error}").replace("{error}", e.message()));
            }
    });
}

/// (kind, level, markup): kind is 'h' (level 1..3), 'p', or 'l' (level 0/1 = nesting). The Markdown is read by
/// pulldown-cmark; its text is escaped for Pango, **bold** becomes <b>, *italic* <i> and `code` <tt>.
pub fn blocks(md: &str) -> Vec<(char, usize, String)> {
    use pulldown_cmark::{Event, Parser, Tag, TagEnd};
    let mut out = vec![];
    // the block being filled: kind, level and markup so far
    let mut cur: Option<(char, usize, String)> = None;
    let mut depth = 0usize;
    let flush = |cur: &mut Option<(char, usize, String)>, out: &mut Vec<(char, usize, String)>| {
        if let Some((kind, level, text)) = cur.take() {
            let text = text.trim().to_string();
            if !text.is_empty() {
                out.push((kind, level, text));
            }
        }
    };
    for event in Parser::new(md) {
        match event {
            Event::Start(Tag::Heading { level, .. }) => {
                flush(&mut cur, &mut out);
                cur = Some(('h', level as usize, String::new()));
            }
            Event::Start(Tag::Paragraph) if depth == 0 => {
                flush(&mut cur, &mut out);
                cur = Some(('p', 0, String::new()));
            }
            Event::Start(Tag::List(_)) => {
                flush(&mut cur, &mut out);
                depth += 1;
            }
            Event::End(TagEnd::List(_)) => {
                flush(&mut cur, &mut out);
                depth -= 1;
            }
            Event::Start(Tag::Item) => {
                flush(&mut cur, &mut out);
                cur = Some(('l', (depth - 1).min(1), String::new()));
            }
            Event::End(TagEnd::Heading(_) | TagEnd::Item) => flush(&mut cur, &mut out),
            Event::End(TagEnd::Paragraph) if depth == 0 => flush(&mut cur, &mut out),
            event => {
                let piece = match event {
                    Event::Text(t) => glib::markup_escape_text(&t).to_string(),
                    Event::Code(t) => format!("<tt>{}</tt>", glib::markup_escape_text(&t)),
                    Event::SoftBreak | Event::HardBreak => " ".into(),
                    Event::Start(Tag::Strong) => "<b>".into(),
                    Event::End(TagEnd::Strong) => "</b>".into(),
                    Event::Start(Tag::Emphasis) => "<i>".into(),
                    Event::End(TagEnd::Emphasis) => "</i>".into(),
                    _ => continue,
                };
                cur.get_or_insert_with(|| ('p', 0, String::new())).2.push_str(&piece);
            }
        }
    }
    flush(&mut cur, &mut out);
    out
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/help-dialog.ui")]
    pub struct HelpDialog {
        #[template_child(id = "box")]
        pub content: TemplateChild<gtk::Box>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for HelpDialog {
        const NAME: &'static str = "AlphaLinkHelpDialog";
        type Type = super::HelpDialog;
        type ParentType = adw::Dialog;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for HelpDialog {}
    impl WidgetImpl for HelpDialog {}
    impl AdwDialogImpl for HelpDialog {}
}

glib::wrapper! {
    pub struct HelpDialog(ObjectSubclass<imp::HelpDialog>)
        @extends adw::Dialog, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl HelpDialog {
    pub fn new(language: Option<&str>) -> Self {
        let dlg: Self = glib::Object::new();
        let text = std::fs::read_to_string(help_path(language)).unwrap_or_default();
        for (kind, level, markup) in blocks(&text) {
            let lbl = gtk::Label::builder().use_markup(true).wrap(true).xalign(0.0).build();
            match kind {
                'h' => {
                    lbl.set_markup(&markup);
                    lbl.add_css_class(["title-1", "title-3", "heading"][level.clamp(1, 3) - 1]);
                    lbl.set_margin_top([0, 14, 8][level.clamp(1, 3) - 1]);
                }
                'l' => {
                    lbl.set_markup(&format!("{}{markup}", if level > 0 { "◦ " } else { "• " }));
                    lbl.set_margin_start(12 + 20 * level as i32);
                }
                _ => lbl.set_markup(&markup),
            }
            dlg.imp().content.append(&lbl);
        }
        dlg
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markdown_subset() {
        let b = blocks("# Title\n\nSome **bold** and `x<1`\ntext.\n\n- one\n  - two\n");
        assert_eq!(b[0], ('h', 1, "Title".into()));
        assert_eq!(b[1], ('p', 0, "Some <b>bold</b> and <tt>x&lt;1</tt> text.".into()));
        assert_eq!(b[2], ('l', 0, "one".into()));
        assert_eq!(b[3], ('l', 1, "two".into()));
    }
}
