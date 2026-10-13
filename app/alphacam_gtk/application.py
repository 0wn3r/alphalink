"""Adw.Application: opens files given on the command line, app-level actions."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import APP_ID  # noqa: E402
from .window import MainWindow  # noqa: E402

VERSION = "0.1.0"


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE)
        self.select_op: int | None = None
        self.page: str | None = None
        self.touch = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb, accels in (("quit", lambda *_: self.quit(), ["<Control>q"]),
                                 ("about", lambda *_: self._about(), [])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def _window(self) -> MainWindow:
        win = self.get_active_window()
        if win is None:
            win = MainWindow(self)
        return win

    def do_activate(self):
        self._window().present()

    def do_open(self, files, n_files, hint):
        win = self._window()
        win.present()
        if files:
            win.load(Path(files[0].get_path()))
            if self.select_op is not None:
                win._select_op(self.select_op)
            if self.page:
                win.stack.set_visible_child_name(self.page)
            if self.touch:
                win.input_mode.set_preference("touch")

    def _about(self):
        Adw.AboutDialog(application_name="AlphaLink CAM", application_icon="applications-engineering-symbolic",
                        version=VERSION, developer_name="Eduard",
                        comments="Lathe CAM for the Harrison Alpha 400T, writing ALPHA-T programs.",
                        website="https://github.com/0wn3r/alphalink",
                        license_type=Gtk.License.UNKNOWN).present(self.get_active_window())


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = Application()
    if "--select" in argv:  # debugging / screenshots: preselect operation N (1-based)
        i = argv.index("--select")
        app.select_op = int(argv[i + 1]) - 1
        del argv[i:i + 2]
    if "--page" in argv:  # part | program
        i = argv.index("--page")
        app.page = argv[i + 1]
        del argv[i:i + 2]
    if "--touch" in argv:
        argv.remove("--touch")
        app.touch = True
    return app.run(argv)
