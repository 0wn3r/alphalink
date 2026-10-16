"""In-app help (F1): help/help.<lang>.md shown in a dialog. The help is plain Markdown so it also reads well on
GitHub; this renders the small subset it uses (# headings, paragraphs, "- " lists two levels deep, **bold**,
`code`) as Pango markup, so it works offline and inside the Flatpak without a browser.

The Training and Reference Manuals ship as PDFs (manuals/<kind>.<lang>.pdf) and open in the desktop's PDF viewer."""

from __future__ import annotations

import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .i18n import _, language  # noqa: E402
from .ui import template  # noqa: E402

HELP_DIR = Path(__file__).with_name("help")
MANUAL_DIR = Path(__file__).with_name("manuals")
MANUALS = ("training", "reference")


def help_path(lang: str | None = None) -> Path:
    lang = (lang or language() or "en").split("_")[0]
    p = HELP_DIR / f"help.{lang}.md"
    return p if p.exists() else HELP_DIR / "help.en.md"


def manual_path(kind: str, lang: str | None = None) -> Path:
    lang = (lang or language() or "en").split("_")[0]
    p = MANUAL_DIR / f"{kind}.{lang}.pdf"
    return p if p.exists() else MANUAL_DIR / f"{kind}.en.pdf"


def open_manual(window, kind: str) -> None:
    """Open a manual in the PDF viewer (through the portal inside the Flatpak); failures become a toast."""
    def done(launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                window.toast(_("Could not open the manual: {error}").format(error=e.message))

    Gtk.FileLauncher(file=Gio.File.new_for_path(str(manual_path(kind)))).launch(window, None, done)


def inline(text: str) -> str:
    """Escape for Pango, then **bold** and `code`."""
    s = GLib.markup_escape_text(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`(.+?)`", r"<tt>\1</tt>", s)


def blocks(md: str) -> list[tuple[str, int, str]]:
    """(kind, level, markup): kind is 'h' (level 1..3), 'p', or 'li' (level 0/1 = nesting)."""
    out: list[tuple[str, int, str]] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(("p", 0, inline(" ".join(para))))
            para.clear()

    for line in md.splitlines():
        m = re.match(r"(#{1,3}) (.*)", line)
        li = re.match(r"( *)- (.*)", line)
        if m:
            flush()
            out.append(("h", len(m.group(1)), inline(m.group(2))))
        elif li:
            flush()
            out.append(("li", min(len(li.group(1)) // 2, 1), inline(li.group(2))))
        elif not line.strip():
            flush()
        else:
            para.append(line.strip())
    flush()
    return out


@template("help-dialog")
class HelpDialog(Adw.Dialog):
    __gtype_name__ = "AlphaLinkHelpDialog"

    box = Gtk.Template.Child()

    def __init__(self, lang: str | None = None):
        super().__init__()
        for kind, level, markup in blocks(help_path(lang).read_text(encoding="utf-8")):
            lbl = Gtk.Label(use_markup=True, wrap=True, xalign=0)
            if kind == "h":
                lbl.set_markup(markup)
                lbl.add_css_class({1: "title-1", 2: "title-3", 3: "heading"}[level])
                lbl.set_margin_top({1: 0, 2: 14, 3: 8}[level])
            elif kind == "li":
                lbl.set_markup(("◦ " if level else "• ") + markup)
                lbl.set_margin_start(12 + 20 * level)
            else:
                lbl.set_markup(markup)
            self.box.append(lbl)
