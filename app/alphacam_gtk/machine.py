"""Machine page (DESIGN.md 6/7, TOUCH.md 7.4): port, live modem lines, send the program, back up / restore the
Manual Ops, loopback test. Transfers run in a worker thread; a dialog shows progress and can cancel."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from alphacam.link import serial_link as sl  # noqa: E402

from .hold_button import HoldButton  # noqa: E402
from .i18n import _  # noqa: E402
from .settings import settings  # noqa: E402

LOG_DIR = Path(GLib.get_user_state_dir()) / "alphalink-cam" / "logs"

STEPS_SEND = _("On the lathe: CAM key, 4 “LOAD CAM FILE”, then touch the ← arrow. Then hold “Send to the Lathe” here.")
STEPS_BACKUP = _("Start the backup here first, then on the lathe: in Manual Ops touch the ops file icon, "
                 "1 “OP. SAVE/RESTORE”, then the → arrow.")
STEPS_RESTORE = _("On the lathe: in Manual Ops touch the ops file icon, 1 “OP. SAVE/RESTORE”, then the ← arrow. "
                  "Only Manual Ops files saved from this lathe: programs not made for the Alpha-T can corrupt its "
                  "macros (Alphalink-T manual).")
STEPS_LOOP = _("Unplug the cable from the lathe and bridge pins 2 and 3 of the adapter, then run the test.")


class TransferDialog(Adw.Dialog):
    """Progress of one transfer; Cancel stops the worker at the next byte."""

    def __init__(self, title: str):
        super().__init__(title=title, content_width=420, can_close=False)
        self.cancel = threading.Event()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24)
        self.state = Gtk.Label(label=_("Opening port…"), wrap=True)
        self.state.add_css_class("title-3")
        self.bar = Gtk.ProgressBar(show_text=True)
        self.detail = Gtk.Label(label="", wrap=True)
        self.detail.add_css_class("dim-label")
        self.button = Gtk.Button(label=_("_Cancel"), use_underline=True, halign=Gtk.Align.CENTER)
        self.button.add_css_class("pill")
        self.button.connect("clicked", self._cancel_or_close)
        for w in (self.state, self.bar, self.detail, self.button):
            box.append(w)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        view.set_content(box)
        self.set_child(view)
        self.finished = False

    def _cancel_or_close(self, *_args):
        if self.finished:
            self.set_can_close(True)
            self.close()
        else:
            self.cancel.set()
            self.state.set_label(_("Cancelling…"))

    def finish(self, ok: bool, text: str) -> None:
        self.finished = True
        self.state.set_label(text)
        self.button.set_label(_("_Close"))
        if ok:
            self.bar.set_fraction(1.0)


class MachinePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.data: bytes | None = None
        self.program_info = ""
        self._poll = 0
        self._busy = False

        page = Adw.PreferencesPage()
        # connection
        conn = Adw.PreferencesGroup(title=_("Connection"))
        self.port_model = Gtk.StringList()
        self.port_row = Adw.ComboRow(title=_("Serial port"), model=self.port_model)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Rescan Ports"))
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda *_: self.refresh_ports())
        self.port_row.add_suffix(refresh)
        conn.add(self.port_row)
        self.lines_row = Adw.ActionRow(title=_("Modem lines"), subtitle=_("Shown while this page is open"))
        self.lines_row.set_tooltip_text(_("With the manual's cable (bridges 7-8 and 1-4-6 on the PC side) these only "
                                          "echo the PC's own RTS/DTR: on means the adapter is alive, not the lathe."))
        self.line_labels = {}
        for name in ("CTS", "DSR", "CD", "RI"):
            lbl = Gtk.Label(label=name, valign=Gtk.Align.CENTER)
            lbl.add_css_class("modem-line")
            self.line_labels[name.lower()] = lbl
            self.lines_row.add_suffix(lbl)
        conn.add(self.lines_row)
        self.delay_row = Adw.SpinRow.new_with_range(0, 20, 0.5)
        self.delay_row.set_title(_("Delay per character (ms)"))
        self.delay_row.set_subtitle(_("2 ms works on the 400T; 0 sends at full speed"))
        self.delay_row.set_value(settings()["char_delay_ms"])
        self.delay_row.connect("notify::value", self._delay_changed)
        conn.add(self.delay_row)
        self.wait_row = Adw.SwitchRow(title=_("Wait for the control (DC1)"), active=settings()["wait_xon"],
                                      subtitle=_("Nothing is sent until the lathe asks for data"))
        self.wait_row.connect("notify::active", lambda r, _p: settings().set("wait_xon", r.get_active()))
        conn.add(self.wait_row)
        page.add(conn)

        # send
        send = Adw.PreferencesGroup(title=_("Send CAM program"), description=STEPS_SEND)
        self.send_info = Adw.ActionRow(title=_("No program"), subtitle=_("Open a part first"))
        send.add(self.send_info)
        self.send_button = HoldButton(_("Send to the Lathe"), margin_top=12, height_request=72)
        self.send_button.add_css_class("suggested-action")
        self.send_button.connect("held", lambda *_: self._confirm_send())
        send.add(self.send_button)
        page.add(send)

        # manual ops
        mops = Adw.PreferencesGroup(title=_("Manual Ops"), description=STEPS_BACKUP)
        backup = Adw.ButtonRow(title=_("Back Up Manual Ops from the Lathe…"), start_icon_name="document-save-symbolic")
        backup.connect("activated", lambda *_: self._backup())
        mops.add(backup)
        restore_row = Adw.ActionRow(title=_("Restore Manual Ops"), subtitle=STEPS_RESTORE)
        mops.add(restore_row)
        self.restore_button = HoldButton(_("Restore to the Lathe…"), margin_top=12, height_request=64)
        self.restore_button.add_css_class("destructive-action")
        self.restore_button.connect("held", lambda *_: self._restore_pick())
        mops.add(self.restore_button)
        page.add(mops)

        test = Adw.PreferencesGroup(title=_("Cable Test"), description=STEPS_LOOP)
        loop = Adw.ButtonRow(title=_("Run Loopback Test"), start_icon_name="media-playlist-repeat-symbolic")
        loop.connect("activated", lambda *_: self._loopback())
        test.add(loop)
        page.add(test)

        self.append(page)
        self.refresh_ports()
        self.port_row.connect("notify::selected", lambda *_: (self._restart_poll(), self._remember_port()))
        self.connect("map", lambda *_: self._restart_poll())
        self.connect("unmap", lambda *_: self._stop_poll())

    # --- state ------------------------------------------------------------------------------------------

    def set_program(self, data: bytes | None, info: str) -> None:
        self.data, self.program_info = data, info
        if data:
            self.send_info.set_title(info)
            secs = len(data) * 11 / 9600 + len(data) * self.delay_row.get_value() / 1000
            self.send_info.set_subtitle(_("{size} bytes, about {secs} s").format(size=len(data), secs=f"{secs:.0f}"))
        else:
            self.send_info.set_title(_("No program"))
            self.send_info.set_subtitle(_("Open a part that posts without errors"))
        self.send_button.set_sensitive(bool(data))

    def port(self) -> str | None:
        item = self.port_row.get_selected_item()
        return item.get_string().split("  ")[0] if item is not None else None

    def _remember_port(self) -> None:
        if not getattr(self, "_restoring", False) and self.port():
            settings().set("serial_port", self.port())

    def refresh_ports(self) -> None:
        items = [f"{dev}  {desc}" for dev, desc in sl.list_ports()] or ["/dev/ttyUSB0  " + _("(not present)")]
        self._restoring = True
        self.port_model.splice(0, self.port_model.get_n_items(), items)
        wanted = settings()["serial_port"]
        for i, item in enumerate(items):
            if wanted and item.split("  ")[0] == wanted:
                self.port_row.set_selected(i)
        self._restoring = False

    # --- modem lines (5 Hz while visible, not during transfers) ----------------------------------------

    def _restart_poll(self) -> None:
        self._stop_poll()
        if self.get_mapped():
            self._poll = GLib.timeout_add(200, self._poll_lines)

    def _stop_poll(self) -> None:
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0
        if getattr(self, "_poll_port", None) is not None:
            self._poll_port.close()
            self._poll_port = None

    def _poll_lines(self) -> bool:
        if self._busy:
            return True
        try:
            if getattr(self, "_poll_port", None) is None or self._poll_port.port != self.port():
                if getattr(self, "_poll_port", None) is not None:
                    self._poll_port.close()
                self._poll_port = sl.open_port(self.port())
            lines = sl.modem_lines(self._poll_port)
            self.lines_row.set_subtitle(self.port() or "")
        except Exception as e:  # noqa: BLE001 - shown to the user, any port error
            self._poll_port = None
            lines = {}
            self.lines_row.set_subtitle(_port_error(e))
        for name, lbl in self.line_labels.items():
            on = lines.get(name)
            lbl.remove_css_class("success")
            lbl.remove_css_class("dim-label")
            lbl.add_css_class("success" if on else "dim-label")
        return True

    # --- transfers --------------------------------------------------------------------------------------

    def _run(self, title: str, work, done) -> None:
        """Run work(ser, dialog, log) in a thread with the port open; done(result) on the main loop."""
        dlg = TransferDialog(title)
        dlg.present(self.window)
        self._busy = True
        self._stop_poll()
        self.window.input_mode.set_locked(True)
        log = sl.TransferLog()
        port = self.port()

        def worker():
            result, error = None, None
            try:
                ser = sl.open_port(port)
                try:
                    result = work(ser, dlg, log)
                finally:
                    ser.close()
            except sl.Cancelled:
                error = _("Cancelled")
            except Exception as e:  # noqa: BLE001
                error = _port_error(e)
            log.add(f"result: {error or 'ok'}")
            GLib.idle_add(finish, result, error)

        def finish(result, error):
            self._busy = False
            self.window.input_mode.set_locked(False)
            self._restart_poll()
            try:
                path = _write_log(title, port, log)
            except OSError as e:  # the transfer is over either way; never leave the dialog stuck open
                path = _("not written ({error})").format(error=e)
            if error:
                dlg.finish(False, error)
                dlg.detail.set_label(_("Log: {path}").format(path=path))
            else:
                done(dlg, result)
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _progress(self, dlg, text):
        def cb(done, total=None):
            def ui():
                if total:
                    dlg.bar.set_fraction(done / total)
                    dlg.bar.set_text(_("{done} / {total} bytes").format(done=done, total=total))
                else:
                    dlg.bar.pulse()
                    dlg.bar.set_text(_("{done} bytes").format(done=done))
                dlg.state.set_label(text)
                return False
            GLib.idle_add(ui)
        return cb

    def _confirm_send(self) -> None:
        if not self.data:
            return
        alert = Adw.AlertDialog(heading=_("Send this program?"),
                                body=_("{info}\n{size} bytes to {port}.\n\n{steps}").format(
                                    info=self.program_info, size=len(self.data), port=self.port(),
                                    steps=_("The lathe must be on CAM, 4 “LOAD CAM FILE”, with the ← arrow touched. "
                                            "Then press Send.")))
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("send", _("_Send"))
        alert.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")
        data = self.data  # exactly the program this dialog describes, even if the part changes meanwhile
        alert.connect("response", lambda d, r: r == "send" and self._send(data))
        alert.present(self.window)

    def _send(self, data: bytes) -> None:
        opts = sl.SendOptions(wait_xon=self.wait_row.get_active(), char_delay_ms=self.delay_row.get_value())

        def work(ser, dlg, log):
            GLib.idle_add(lambda: dlg.state.set_label(_("Waiting for the lathe (DC1)…")) and False)
            return sl.send(ser, data, opts, self._progress(dlg, _("Sending…")), dlg.cancel, log)

        self._run(_("Send CAM Program"), work,
                  lambda dlg, n: dlg.finish(True, _("Sent {n} bytes. Check the program list on the control.").format(n=n)))

    def _backup(self) -> None:
        def work(ser, dlg, log):
            GLib.idle_add(lambda: dlg.state.set_label(_("Waiting for data: on the lathe touch the → arrow under “OP. SAVE/RESTORE”")) and False)
            try:
                data, complete = sl.receive(ser, self._progress(dlg, _("Receiving…")), dlg.cancel, None, 10.0, log), True
            except sl.TransferTimeout as e:
                if not e.data:
                    raise
                data, complete = e.data, False  # the end mark never came: keep what arrived, marked partial
            try:  # the raw bytes next to the log, whatever happens to the save dialog
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                raw = LOG_DIR / datetime.now().strftime("%Y%m%d-%H%M%S-manops.raw")
                raw.write_bytes(data)
                log.add(f"raw bytes kept in {raw}")
            except OSError as err:
                log.add(f"raw bytes not kept: {err}")
            return data, complete

        def done(dlg, result):
            data, complete = result
            if complete:
                dlg.finish(True, _("Received {n} bytes.").format(n=len(data)))
            else:
                dlg.finish(False, _("Received {n} bytes, but the end mark never came, so the backup may be incomplete. "
                                    "Save it anyway, and keep the lathe's ops until a full backup works.").format(n=len(data)))
            name = datetime.now().strftime("MANOPS-%Y%m%d-%H%M.MOP" if complete else "MANOPS-%Y%m%d-%H%M-PARTIAL.MOP")
            fd = Gtk.FileDialog(title=_("Save Manual Ops Backup"), initial_name=name)

            def saved(d, res):
                try:
                    f = d.save_finish(res)
                except GLib.Error:
                    f = None
                target = Path(f.get_path()) if f and f.get_path() else LOG_DIR.parent / name
                for where in (target, LOG_DIR.parent / name):  # never lose a backup: fall back to the app folder
                    try:
                        where.parent.mkdir(parents=True, exist_ok=True)
                        where.write_bytes(data)  # raw bytes, before any decoding
                    except OSError as e:
                        self.window.toast(_("Could not save {path}: {error}").format(path=where, error=e))
                        continue
                    self.window.toast(_("Backup saved to {path}").format(path=where))
                    return

            fd.save(self.window, None, saved)

        self._run(_("Back Up Manual Ops"), work, done)

    def _restore_pick(self) -> None:
        fd = Gtk.FileDialog(title=_("Restore Manual Ops From"))

        def picked(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            try:
                data = Path(f.get_path()).read_bytes()
            except (OSError, TypeError) as e:
                self.window.toast(_("Could not read the file: {error}").format(error=e))
                return
            if not (data.lstrip().startswith(b"%") and data.rstrip().endswith(b"%")):
                self.window.toast(_("This is not a Manual Ops backup (it must start and end with %)."))
                return
            alert = Adw.AlertDialog(heading=_("Restore Manual Ops?"),
                                    body=_("{name}, {size} bytes.\n\n{steps}").format(
                                        name=Path(f.get_path()).name, size=len(data), steps=STEPS_RESTORE))
            alert.add_response("cancel", _("_Cancel"))
            alert.add_response("restore", _("_Restore"))
            alert.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
            alert.set_default_response("cancel")
            alert.set_close_response("cancel")
            alert.connect("response", lambda _d, r: r == "restore" and self._restore(data))
            alert.present(self.window)

        fd.open(self.window, None, picked)

    def _restore(self, data: bytes) -> None:
        payload = sl.manops_load_bytes(data)
        opts = sl.SendOptions(wait_xon=self.wait_row.get_active(), char_delay_ms=self.delay_row.get_value())
        self._run(_("Restore Manual Ops"),
                  lambda ser, dlg, log: sl.send(ser, payload, opts, self._progress(dlg, _("Sending…")),
                                                dlg.cancel, log),
                  lambda dlg, n: dlg.finish(True, _("Sent {n} bytes.").format(n=n)))

    def _delay_changed(self, row, _p) -> None:
        """Save the delay once the spin row settles, not on every step."""
        if getattr(self, "_delay_id", 0):
            GLib.source_remove(self._delay_id)

        def save():
            self._delay_id = 0
            settings().set("char_delay_ms", row.get_value())
            return False

        self._delay_id = GLib.timeout_add(500, save)

    def _loopback(self) -> None:
        """Writes a short test text to the port: only with the cable off the lathe, so ask first."""
        alert = Adw.AlertDialog(heading=_("Run the loopback test?"),
                                body=_("Unplug the cable from the lathe and bridge pins 2 and 3 of the adapter first: "
                                       "the test sends a short text to the port."))
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("run", _("_Run Test"))
        alert.set_default_response("run")
        alert.set_close_response("cancel")
        alert.connect("response", lambda _d, r: r == "run" and self._run_loopback())
        alert.present(self.window)

    def _run_loopback(self) -> None:
        self._run(_("Loopback Test"), lambda ser, dlg, log: sl.loopback(ser),
                  lambda dlg, r: dlg.finish(r[0], _("Loopback OK: the adapter and cable work.") if r[0] else
                                            _("Loopback failed: got {data}. Check the 2-3 bridge.").format(
                                                data=repr(r[1]))))


def _port_error(e: Exception) -> str:
    text = str(e)
    if "Permission denied" in text or getattr(e, "errno", None) == 13:
        return _("Permission denied: add yourself to the dialout group (sudo usermod -aG dialout $USER), then log in again")
    if "No such file" in text or "could not open port" in text:
        return _("Port not found: is the USB adapter plugged in?")
    return text


def _write_log(title: str, port: str | None, log: sl.TransferLog) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S-") + title.lower().replace(" ", "-") + ".log")
    path.write_text(f"{title} on {port}\n{log.text()}")
    return path
