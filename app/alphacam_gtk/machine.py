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

from .hold_button import HoldButton  # noqa: E402, F401  (the type ui/machine.blp uses)
from .i18n import _  # noqa: E402
from .ui import builder, template  # noqa: E402
from .settings import settings  # noqa: E402

LOG_DIR = Path(GLib.get_user_state_dir()) / "alphalink-cam" / "logs"

STEPS_SEND = _("On the lathe: CAM key, 4 “LOAD CAM FILE”, then touch the ← arrow. Then hold “Send to the Lathe” here.")
STEPS_BACKUP = _("Start the backup here first, then on the lathe: in Manual Ops touch the ops file icon, "
                 "1 “OP. SAVE/RESTORE”, then the → arrow.")
STEPS_RESTORE = _("On the lathe: in Manual Ops touch the ops file icon, 1 “OP. SAVE/RESTORE”, then the ← arrow. "
                  "Only Manual Ops files saved from this lathe: programs not made for the Alpha-T can corrupt its "
                  "macros (Alphalink-T manual).")
STEPS_LOOP = _("Unplug the cable from the lathe and bridge pins 2 and 3 of the adapter, then run the test.")


@template("transfer-dialog")
class TransferDialog(Adw.Dialog):
    """Progress of one transfer; Cancel stops the worker at the next byte."""

    __gtype_name__ = "AlphaLinkTransferDialog"

    state = Gtk.Template.Child()
    bar = Gtk.Template.Child()
    detail = Gtk.Template.Child()
    button = Gtk.Template.Child()

    def __init__(self, title: str):
        super().__init__(title=title)
        self.cancel = threading.Event()
        self.finished = False

    @Gtk.Template.Callback()
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


@template("machine")
class MachinePage(Gtk.Box):
    __gtype_name__ = "AlphaLinkMachinePage"

    port_row = Gtk.Template.Child()
    port_model = Gtk.Template.Child()
    lines_row = Gtk.Template.Child()
    line_cts = Gtk.Template.Child()
    line_dsr = Gtk.Template.Child()
    line_cd = Gtk.Template.Child()
    line_ri = Gtk.Template.Child()
    delay_row = Gtk.Template.Child()
    wait_row = Gtk.Template.Child()
    parity_row = Gtk.Template.Child()
    send_group = Gtk.Template.Child()
    send_info = Gtk.Template.Child()
    send_button = Gtk.Template.Child()
    mops_group = Gtk.Template.Child()
    restore_row = Gtk.Template.Child()
    restore_button = Gtk.Template.Child()
    test_group = Gtk.Template.Child()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.data: bytes | None = None
        self.program_info = ""
        self._poll = 0
        self._busy = False
        self.send_group.set_description(STEPS_SEND)
        self.mops_group.set_description(STEPS_BACKUP)
        self.restore_row.set_subtitle(STEPS_RESTORE)
        self.test_group.set_description(STEPS_LOOP)
        self.line_labels = {"cts": self.line_cts, "dsr": self.line_dsr, "cd": self.line_cd, "ri": self.line_ri}
        st = settings()
        self.delay_row.set_value(st["char_delay_ms"])
        self.delay_row.connect("notify::value", self._delay_changed)
        self.wait_row.set_active(st["wait_xon"])
        self.wait_row.connect("notify::active", lambda r, _p: settings().set("wait_xon", r.get_active()))
        self.parity_row.set_active(bool(st["soft_parity"]))
        self.parity_row.connect("notify::active", lambda r, _p: settings().set("soft_parity", r.get_active()))
        self.refresh_ports()
        self.port_row.connect("notify::selected", lambda *_: (self._restart_poll(), self._remember_port(),
                                                              self._port_kind()))
        self._port_kind()
        self.connect("map", lambda *_: self._restart_poll())
        self.connect("unmap", lambda *_: self._stop_poll())

    @Gtk.Template.Callback()
    def _rescan(self, *_a) -> None:
        self.refresh_ports()

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

    def _port_kind(self) -> None:
        """Bluetooth ports: the adapter carries the line settings, has no modem lines, and adds latency, so the
        delay per character should be longer (the lathe must stop within 10 characters of its DC3)."""
        bt = sl.is_bluetooth(self.port())
        self.parity_row.set_visible(bt)
        self.lines_row.set_visible(not bt)
        self.delay_row.set_subtitle(_("Over Bluetooth use 5 ms or more: the lathe must stop within 10 characters of "
                                      "its pause (DC3)") if bt else _("2 ms works on the 400T; 0 sends at full speed"))

    def _open(self, port: str):
        return sl.open_port(port, soft_parity=sl.is_bluetooth(port) and self.parity_row.get_active())

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
        if self.get_mapped() and not sl.is_bluetooth(self.port()):  # opening a Bluetooth port connects: no polling
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
                ser = self._open(port)
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

    @Gtk.Template.Callback()
    def _confirm_send(self, *_a) -> None:
        if not self.data:
            return
        alert = builder("machine-dialogs").get_object("send_alert")
        alert.set_body(_("{info}\n{size} bytes to {port}.\n\n{steps}").format(
            info=self.program_info, size=len(self.data), port=self.port(),
            steps=_("The lathe must be on CAM, 4 “LOAD CAM FILE”, with the ← arrow touched. Then press Send.")))
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

    @Gtk.Template.Callback()
    def _backup(self, *_a) -> None:
        def work(ser, dlg, log):
            GLib.idle_add(lambda: dlg.state.set_label(_("Waiting for data: on the lathe touch the → arrow under “OP. SAVE/RESTORE”")) and False)
            def quiet(seconds):
                GLib.idle_add(lambda: dlg.state.set_label(_(
                    "The lathe paused {s} s ago; waiting. Press Stop and Keep when it has finished.").format(
                        s=int(seconds))) and False)

            GLib.idle_add(lambda: dlg.button.set_label(_("_Stop and Keep")) and False)
            try:
                # no time limit: the control pauses between operations; the user stops it, and keeps what arrived
                data, complete = sl.receive(ser, self._progress(dlg, _("Receiving…")), dlg.cancel, None, None, log,
                                            quiet), True
            except (sl.TransferTimeout, sl.Cancelled) as e:
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
                dlg.finish(False, _("Received {n} bytes without the end mark. If the lathe had finished, the backup is "
                                    "probably whole; it is saved marked PARTIAL. Keep the lathe's ops until a "
                                    "restore of it has been checked.").format(n=len(data)))
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

    @Gtk.Template.Callback()
    def _restore_pick(self, *_a) -> None:
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
            alert = builder("machine-dialogs").get_object("restore_alert")
            alert.set_body(_("{name}, {size} bytes.\n\n{steps}").format(
                name=Path(f.get_path()).name, size=len(data), steps=STEPS_RESTORE))
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

    @Gtk.Template.Callback()
    def _loopback(self, *_a) -> None:
        """Writes a short test text to the port: only with the cable off the lathe, so ask first."""
        alert = builder("machine-dialogs").get_object("loopback_alert")
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
