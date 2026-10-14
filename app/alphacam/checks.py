"""Tool checks against the job (workflow quick win): warnings, never errors. The program is still written, so a
deliberate choice is never blocked; the app shows them on the operation and on the setup sheet."""

from __future__ import annotations

from .i18n import tr
from .model.operations import Drill, Groove, Thread, ToolChange
from .model.tools import standard_library


def _tool(project, number: int, library=None):
    for t in project.tools:
        if t.number == number:
            return t
    for lib in (library, standard_library()):
        if lib is None:
            continue
        try:
            return lib.get(number)
        except KeyError:
            pass
    return None


def tool_warnings(project, library=None) -> dict[int, list[str]]:
    """Operation index → what looks wrong about its tool."""
    out: dict[int, list[str]] = {}
    current = project.first_tool
    for i, op in enumerate(project.operations):
        if isinstance(op, ToolChange):
            current = op.tool
            continue
        number = getattr(op, "tool", None)
        if number is None:
            continue
        msgs = []
        if number != current:
            msgs.append(tr("uses T{op} but T{cur} is in the turret: add a Tool Change", op=number, cur=current))
        t = _tool(project, number, library)
        if t is not None:
            # a thread wants a threading tool, or a 60° neutral tool (the metric thread form; the Training manual's T8)
            if isinstance(op, Thread) and t.type != "thread" and not (t.hand == "N" and t.angle == 60):
                msgs.append(tr("T{n} is not a threading tool", n=number))
            if isinstance(op, Groove):
                if t.type != "groove":
                    msgs.append(tr("T{n} is not a grooving tool", n=number))
                elif t.width > 0 and abs(t.width - op.tip_width * (25.4 if project.units == "inch" else 1)) > 1e-3:
                    msgs.append(tr("tip width {w:g} differs from T{n}'s {tw:g}", w=op.tip_width, n=number, tw=t.width))
            if isinstance(op, Drill) and t.type != "drill":
                msgs.append(tr("T{n} is not a drill", n=number))
            external = getattr(op, "external", None)
            if external is False and t.type == "turn":
                msgs.append(tr("an internal operation with an external tool (T{n})", n=number))
            if external is True and t.type == "bore":
                msgs.append(tr("an external operation with a boring tool (T{n})", n=number))
        if msgs:
            out[i] = msgs
    return out
