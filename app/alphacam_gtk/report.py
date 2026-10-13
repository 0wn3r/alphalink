"""Setup sheet (ALPHA-T's ATPRINT / APRINT, without printing): per operation the tool, start, limits, offsets,
passes and depth of cut, and optionally every tool move pass by pass. Built from the preview, so the numbers are
the ones in the program (unscaled)."""

from __future__ import annotations

import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from alphacam.io.prg_parse import MoveKind  # noqa: E402
from alphacam.model.tools import standard_library  # noqa: E402

from .i18n import N_, _  # noqa: E402

KIND_NAMES = {
    "tool_change": N_("TOOL CHANGE"), "area_clear": N_("AREA CLEARANCE"), "area_clear_internal": N_("AREA CLEARANCE"),
    "face_clear": N_("FACE CLEAR"), "groove": N_("GROOVING"), "finish": N_("FINISH PASS"),
    "finish_internal": N_("FINISH PASS"), "area_prefinish": N_("PRE-FINISH PASS"), "drill": N_("DRILLING"),
    "thread": N_("THREADING"),
}
RULE = " " + "-" * 78


def _tool_name(project, number) -> str:
    for t in project.tools:
        if t.number == number:
            return t.name
    try:
        return standard_library().get(number).name
    except KeyError:
        return ""


def sheet_text(project, pv, title: str, moves: bool) -> str:
    """The setup sheet as monospaced text, laid out like ALPHA-T's print."""
    out = [" ", f"  {_('TITLE')} : {title:<40} {_('DATE')} : {datetime.date.today():%d %b %Y}", " "]
    prog = pv.program if pv is not None else None
    if prog is None:
        return "\n".join(out + [_("  (no program)")])
    op_no = 0
    for op in pv.ops:
        for block in op.blocks:
            name = block.op_name
            if name in ("empty", "end"):
                continue
            if name == "tool_change":
                sx, sz = block.start
                out += [RULE, f"  {_('Operation')}  : {_(KIND_NAMES[name]):<18} {_('Tool No.')} : {block.tool:3d}    "
                              f"{_tool_name(project, block.tool)}",
                        f"  {_('Tool change')} X : {sx:8.3f}      Z : {sz:8.3f}      {_('Feed')} : {block.feed:g}"]
            else:
                op_no += 1
                sx, sz = block.start
                lx, lz = block.limit or (0.0, 0.0)
                src = project.operations[op.op_index] if op.op_index is not None else None
                ox, oz = (getattr(src, "offset_x", 0.0), getattr(src, "offset_z", 0.0)) if src else (0.0, 0.0)
                depth = getattr(src, "depth_of_cut", None)
                out += [RULE,
                        f"  {_('Operation')}  : {_(KIND_NAMES.get(name, name.upper())):<18} {_('Tool No.')} : "
                        f"{block.tool:3d}    {_tool_name(project, block.tool)}",
                        f"  {_('Tool Start X')} : {sx:8.3f}      {_('Maximum X')} : {lx:8.3f}       {_('Offset X')} : {ox:7.3f}",
                        f"  {_('Tool Start Z')} : {sz:8.3f}      {_('Maximum Z')} : {lz:8.3f}       {_('Offset Z')} : {oz:7.3f}"]
                line = f"  {_('Op. No.')} : {op_no:2d}     {_('Total Passes')} : {block.declared_passes or 0:2d}"
                if depth is not None:
                    line += f"     {_('Depth Of Cut')} : {depth:7.3f}"
                if name == "thread":
                    line += f"     {_('Pitch')} : {block.feed:g}"
                else:
                    line += f"     {_('Feed')} : {block.feed:g}"
                out.append(line)
            out.append(RULE)
            if moves:
                out += _moves(block)
            out.append(" ")
    return "\n".join(out)


def _moves(block) -> list[str]:
    lines = []
    for p in block.passes:
        if p.number > 1:
            lines.append(f"  {_('Pass No.')} : {p.number:2d}")
        for m in p.moves:
            if m.x is None or m.z is None:
                continue
            xz = f"X {m.x:8.3f}, Z {m.z:8.3f}"
            if m.kind is MoveKind.RAPID:
                lines.append(f"  {_('RAPID TO'):<11}: {xz}")
            elif m.kind is MoveKind.FEED:
                lines.append(f"  {_('FEED  TO'):<11}: {xz}")
            elif m.kind in (MoveKind.ARC_CW, MoveKind.ARC_CCW):
                label = _("ARC CW TO") if m.kind is MoveKind.ARC_CW else _("ARC CCW TO")
                lines.append(f"  {label:<11}: {xz}   {_('centre')} X {m.centre_x:8.3f}, Z {m.centre_z:8.3f}")
            elif m.kind is MoveKind.THREAD:
                lines.append(f"  {_('THREAD TO'):<11}: {xz}")
            elif m.kind is MoveKind.DWELL:
                lines.append(f"  {_('DWELL'):<11}: {m.dwell_revs:g} {_('revs')}")
    return lines


class SheetPage(Gtk.Box):
    """Shows the setup sheet; a switch adds the tool moves; Save as Text writes it to a file."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        bar = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.moves_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.moves_switch.connect("notify::active", lambda *a: self.refresh())
        lbl = Gtk.Label(label=_("Tool moves"))
        save = Gtk.Button(label=_("_Save as Text…"), use_underline=True, halign=Gtk.Align.END, hexpand=True)
        save.connect("clicked", lambda *a: self._save())
        for wdg in (lbl, self.moves_switch, save):
            bar.append(wdg)
        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, top_margin=12,
                                 bottom_margin=12, left_margin=12, right_margin=12)
        self.view.add_css_class("program-text")
        self.append(bar)
        self.append(Gtk.Separator())
        self.append(Gtk.ScrolledWindow(child=self.view, vexpand=True))

    def refresh(self) -> None:
        w = self.window
        if w.project is None:
            return
        title = w.path.stem.upper() if w.path else _("UNTITLED")
        self.view.get_buffer().set_text(sheet_text(w.project, w.pv, title, self.moves_switch.get_active()))

    def _save(self) -> None:
        w = self.window
        stem = w.path.stem if w.path else "part"
        dlg = Gtk.FileDialog(title=_("Save Setup Sheet"), initial_name=f"{stem}-sheet.txt")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            buf = self.view.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            with open(f.get_path(), "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            w.toast(_("Saved {name}").format(name=f.get_basename()))

        dlg.save(w, None, done)
