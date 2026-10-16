"""Setup sheet (ALPHA-T's ATPRINT / APRINT, without printing): per operation the tool, start, limits, offsets,
passes and depth of cut, and optionally every tool move pass by pass. Built from the preview, so the numbers are
the ones in the program (unscaled)."""

from __future__ import annotations

import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from alphacam.io.prg_parse import MoveKind  # noqa: E402
from alphacam.model.tools import standard_library  # noqa: E402

from .i18n import N_, _, ngettext  # noqa: E402
from .ui import template  # noqa: E402

MONTHS = [N_("Jan"), N_("Feb"), N_("Mar"), N_("Apr"), N_("May"), N_("Jun"), N_("Jul"), N_("Aug"), N_("Sep"), N_("Oct"),
          N_("Nov"), N_("Dec")]

KIND_NAMES = {
    "tool_change": N_("TOOL CHANGE"), "area_clear": N_("AREA CLEARANCE"), "area_clear_internal": N_("AREA CLEARANCE"),
    "face_clear": N_("FACE CLEAR"), "groove": N_("GROOVING"), "finish": N_("FINISH PASS"),
    "finish_internal": N_("FINISH PASS"), "area_prefinish": N_("PRE-FINISH PASS"), "drill": N_("DRILLING"),
    "thread": N_("THREADING"),
}
RULE = " " + "-" * 78


def _tool_name(project, number, library=None) -> str:
    for t in list(project.tools) + (list(library.tools) if library is not None else []):
        if t.number == number:
            return t.name
    try:
        return standard_library().get(number).name
    except KeyError:
        return ""


def machine():
    """The cycle time settings (Preferences)."""
    from alphacam.cycletime import Machine

    from .settings import settings

    st = settings()
    return Machine(spindle_max=float(st["spindle_max"]), rapid=float(st["rapid_rate"]),
                   tool_change=float(st["tool_change_s"]))


def time_summary(est) -> str:
    """"About 6:52 (5:52 cutting, 2 tool changes)": the estimate in one line."""
    from alphacam.cycletime import mmss

    return _("About {total} ({cutting} cutting, {changes})").format(
        total=mmss(est.total), cutting=mmss(est.cutting),
        changes=ngettext("{n} tool change", "{n} tool changes", est.tool_changes).format(n=est.tool_changes))


def sheet_text(project, pv, title: str, moves: bool, library=None, est=None) -> str:
    """The setup sheet as monospaced text, laid out like ALPHA-T's print, with the cycle time estimate."""
    from alphacam.cycletime import estimate, mmss

    today = datetime.date.today()
    month = _(MONTHS[today.month - 1])  # strftime's %b is always English here (C locale)
    out = [" ", f"  {_('TITLE')} : {title:<40} {_('DATE')} : {today.day:02d} {month} {today.year}", " "]
    prog = pv.program if pv is not None else None
    if prog is None:
        return "\n".join(out + [_("  (no program)")])
    est = est or estimate(project, pv, library, machine())
    out += [f"  {_('Program')} : {project.program_number}     {_('Cycle time')} : {time_summary(est)}", " "]
    op_no = 0
    from alphacam.checks import tool_warnings

    warnings = tool_warnings(project, library)
    from alphacam.safety import check as safety_check

    for f in safety_check(project, pv, library) if pv.data is not None else []:
        if f.op_index is not None:
            warnings.setdefault(f.op_index, []).append(f.message)
    for op in pv.ops:
        for block in op.blocks:
            name = block.op_name
            if name in ("empty", "end"):
                continue
            if name == "tool_change":
                sx, sz = block.start
                out += [RULE, f"  {_('Operation')}  : {_(KIND_NAMES[name]):<18} {_('Tool No.')} : {block.tool:3d}    "
                              f"{_tool_name(project, block.tool, library)}",
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
                        f"{block.tool:3d}    {_tool_name(project, block.tool, library)}",
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
                if op.op_index in est.seconds and block is op.blocks[0]:
                    out.append(f"  {_('Time')} : {mmss(est.seconds[op.op_index])}")
            for msg in warnings.get(op.op_index, []) if name != "tool_change" else []:
                out.append(f"  ! {msg}")
            restarts = [ps.restart[0] for ps in block.passes if ps.restart]
            if restarts:  # where the control can pick the program up again (a broken tool replaced, say)
                span = f"{restarts[0]}" if len(restarts) == 1 else f"{restarts[0]}–{restarts[-1]}"
                out.append(f"  {_('Block')} : N{block.label:<5} {_('Restart #140')} : {span}"
                           + ("" if len(restarts) == 1 else f"  ({_('one per pass')})"))
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


def tool_order(project, pv, library=None) -> list[tuple[int, str, list[str]]]:
    """The tools in the order they are needed: (number, name, the operations it does)."""
    out: list[tuple[int, str, list[str]]] = []
    if pv is None or pv.program is None:
        return out
    for op in pv.ops:
        for block in op.blocks:
            if block.op_name in ("empty", "end", "tool_change") or block.tool is None:
                continue
            what = _(KIND_NAMES.get(block.op_name, block.op_name.upper())).capitalize()
            if out and out[-1][0] == block.tool:
                if what not in out[-1][2]:
                    out[-1][2].append(what)
            else:
                out.append((block.tool, _tool_name(project, block.tool, library), [what]))
    return out


def write_pdf(path, project, pv, title: str, canvas, library=None, moves: bool = False) -> int:
    """The setup sheet as a printable A4 PDF: first page the part with its toolpaths, the program, billet and cycle
    time, the tools in loading order and the operator's notes; then the sheet itself. Returns the page count."""
    import cairo

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    from alphacam.cycletime import estimate

    W, H, M = 595.0, 842.0, 40.0  # A4 in points, margin
    est = estimate(project, pv, library, machine())
    surface = cairo.PDFSurface(str(path), W, H)
    cr = cairo.Context(surface)
    pages = 1

    def text(x, y, markup, size=10, width=W - 2 * M, mono=False):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(("Monospace " if mono else "Sans ") + str(size)))
        layout.set_width(int(width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_markup(markup, -1)
        cr.set_source_rgb(0.1, 0.1, 0.12)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)
        return layout.get_pixel_extents()[1].height

    esc = GLib.markup_escape_text
    unit = "mm" if project.units == "mm" else "inch"
    b = project.billet
    y = M
    y += text(M, y, f"<b>{esc(title)}</b>", 18) + 6
    facts = [(_("Program"), str(project.program_number)), (_("Units"), unit),
             (_("Billet"), f"Ø{b.diameter:g} × {b.length:g}" + (f", {_('bore')} Ø{b.bore:g}" if b.bore else "")),
             (_("Cycle time"), time_summary(est)), (_("Date"), datetime.date.today().isoformat())]
    y += text(M, y, "   ".join(f"{esc(k)}: <b>{esc(v)}</b>" for k, v in facts), 9) + 10
    pic_h = 300.0
    cr.save()
    cr.rectangle(M, y, W - 2 * M, pic_h)
    cr.clip()
    cr.translate(M, y)
    canvas.render_to(cr, W - 2 * M, pic_h)
    cr.restore()
    cr.set_source_rgb(0.7, 0.7, 0.72)
    cr.set_line_width(0.5)
    cr.rectangle(M, y, W - 2 * M, pic_h)
    cr.stroke()
    y += pic_h + 14
    y += text(M, y, f"<b>{esc(_('Tools, in loading order'))}</b>", 11) + 4
    for n, (number, name, what) in enumerate(tool_order(project, pv, library), 1):
        y += text(M, y, f"{n}.  <b>T{number}</b>  {esc(name)}  —  {esc(', '.join(what))}", 9) + 2
    if project.notes.strip():
        y += 10
        y += text(M, y, f"<b>{esc(_('Notes'))}</b>", 11) + 4
        y += text(M, y, esc(project.notes.strip()), 9)
    cr.show_page()
    lines = sheet_text(project, pv, title, moves, library, est).splitlines()
    per_page = int((H - 2 * M) / 10.5)
    for start in range(0, len(lines), per_page):
        pages += 1
        text(M, M, esc("\n".join(lines[start:start + per_page])), 7.5, mono=True)
        cr.show_page()
    surface.finish()
    return pages


@template("sheet-page")
class SheetPage(Gtk.Box):
    """Shows the setup sheet; a switch adds the tool moves; Save as Text writes it to a file."""

    __gtype_name__ = "AlphaLinkSheetPage"

    moves_switch = Gtk.Template.Child()
    time_label = Gtk.Template.Child()
    notes = Gtk.Template.Child()
    view = Gtk.Template.Child()

    window = None

    def __init__(self, window):
        super().__init__()
        self.window = window

    @Gtk.Template.Callback()
    def _moves_toggled(self, *_a) -> None:
        self.refresh()

    def refresh(self) -> None:
        w = self.window
        if w is None or w.project is None:
            return
        from alphacam.cycletime import estimate

        title = self._title()
        est = estimate(w.project, w.pv, w.library, machine())
        self.time_label.set_label(time_summary(est) if w.pv is not None and w.pv.program is not None else "")
        self.view.get_buffer().set_text(sheet_text(w.project, w.pv, title, self.moves_switch.get_active(), w.library,
                                                   est))
        buf = self.notes.get_buffer()
        if buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) != w.project.notes:
            buf.set_text(w.project.notes)

    def _title(self) -> str:
        w = self.window
        title = w.path.stem.upper() if w.path else (getattr(w, "import_name", None) or _("UNTITLED")).upper()
        if len(w.setups()) > 1:
            title += " " + _("SETUP {n}").format(n=w.setup_index + 1)
        return title

    @Gtk.Template.Callback()
    def _notes_changed(self, *_a) -> None:
        w = self.window
        if w is None or w.project is None:
            return
        buf = self.notes.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if text != w.project.notes:
            w.apply_edit(_("Notes"), lambda p: setattr(p, "notes", text))

    @Gtk.Template.Callback()
    def _save_pdf(self, *_a) -> None:
        w = self.window
        if w.project is None or w.pv is None or w.pv.program is None:
            w.toast(_("There is no program to print yet"))
            return
        self._notes_changed()
        stem = w.path.stem if w.path else (getattr(w, "import_name", None) or "part")
        dlg = Gtk.FileDialog(title=_("Save Setup Sheet as PDF"), initial_name=f"{stem}-sheet.pdf")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                write_pdf(f.get_path(), w.project, w.pv, self._title(), w.canvas, w.library,
                          self.moves_switch.get_active())
            except Exception as e:  # noqa: BLE001  cairo raises its own errors (disk full, permission)
                w.toast(_("Could not save {name}: {error}").format(name=f.get_basename(), error=e))
                return
            w.toast(_("Saved {name}").format(name=f.get_basename()))

        dlg.save(w, None, done)

    @Gtk.Template.Callback()
    def _save(self, *_a) -> None:
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
            try:
                with open(f.get_path(), "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            except (OSError, TypeError) as e:
                w.toast(_("Could not save {name}: {error}").format(name=f.get_basename(), error=e))
                return
            w.toast(_("Saved {name}").format(name=f.get_basename()))

        dlg.save(w, None, done)
