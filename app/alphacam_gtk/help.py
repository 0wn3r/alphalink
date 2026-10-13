"""In-app help (F1): help/help.<lang>.md shown in a dialog. The help is plain Markdown so it also reads well on
GitHub; this renders the small subset it uses (# headings, paragraphs, "- " lists two levels deep, **bold**,
`code`) as Pango markup, so it works offline and inside the Flatpak without a browser."""

from __future__ import annotations

import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .i18n import _, language  # noqa: E402

HELP_DIR = Path(__file__).with_name("help")


def help_path(lang: str | None = None) -> Path:
    lang = (lang or language() or "en").split("_")[0]
    p = HELP_DIR / f"help.{lang}.md"
    return p if p.exists() else HELP_DIR / "help.en.md"


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


class HelpDialog(Adw.Dialog):
    def __init__(self, lang: str | None = None):
        super().__init__(title=_("Help"), content_width=720, content_height=720)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=24, margin_end=24,
                      margin_top=12, margin_bottom=24)
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
            box.append(lbl)
        scroll = Gtk.ScrolledWindow(child=box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(scroll)
        self.set_child(view)
