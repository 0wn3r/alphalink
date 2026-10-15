"""Job templates (workflow improvements, step 4): a saved list of operations with their tools and settings but no
geometry. Applying one to a drawing adds the operations with their contours picked from that drawing, the way a new
operation picks them: the outer profile, the bore for internal operations, the groove, the two thread lines. Any part
can be saved as a template; a few common jobs come built in.

A template is JSON: {"format": "alcam-template/1", "name", "units", "operations": [...]}, the .alcam operation format
without the fields that point into a drawing or depend on the billet."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from gi.repository import GLib

from alphacam.model import serde
from alphacam.model.operations import OPERATION_TYPES

from .i18n import N_, _
from .op_factory import default_op

FORMAT = "alcam-template/1"
# filled from the drawing (and the billet) when the template is applied
FROM_PART = {"contour", "outer_line", "inner_line", "start", "change_point"}
# lengths: converted when the template's units are not the part's (feeds are per revolution: lengths too)
LENGTHS = {"depth_of_cut", "offset_x", "offset_z", "feed", "finish_offset_x", "finish_offset_z", "peck_depth",
           "stepover", "tip_width", "z_shift", "retract", "depth", "peck", "pitch", "cut_depth", "lead_in", "lead_out"}
THROUGH = "through"  # a drill depth: through the billet, whatever its length


def template_dir() -> Path:
    return Path(GLib.get_user_config_dir()) / "alphalink-cam" / "templates"


def from_part(project, name: str) -> dict:
    """The part's operations as a template (the drawing references and tool starts left out)."""
    ops = []
    for op in project.operations:
        d = serde.to_json(op)
        for k in FROM_PART:
            d.pop(k, None)
        ops.append(d)
    return {"format": FORMAT, "name": name, "units": project.units, "operations": ops}


def _bore(project) -> list[str]:
    """The bore for internal operations: of the drawing's connected chains, the one closest to the axis (the outer
    profile when there is only one)."""
    from alphacam.drawing import components, outer_profile, pick_order

    by_id = {g.id: g for g in project.geometry}
    groups = [g for g in components(project) if len(g) > 1]  # a single loose line is a thread or construction line
    if len(groups) < 2:
        return outer_profile(project)
    best = min(groups, key=lambda grp: max(max(by_id[i].a.x, by_id[i].b.x) for i in grp))
    try:
        return pick_order(project, best)
    except (ValueError, KeyError):
        return best


def apply(project, template: dict, library=None) -> int:
    """Append the template's operations to the project (one edit: call inside the undo stack's apply); returns how
    many were added."""
    if template.get("format") != FORMAT:
        raise ValueError(_("not an AlphaLink CAM template"))
    k = 1.0
    if template.get("units", project.units) != project.units:
        k = 1 / 25.4 if project.units == "inch" else 25.4
    added = 0
    for d in template["operations"]:
        kind = d["type"]
        at = len(project.operations)
        op = default_op(kind, project, at)
        for key, value in d.items():
            if key == "type" or key in FROM_PART or not hasattr(op, key):
                continue
            if key == "depth" and value == THROUGH:
                value = -round(project.billet.length + (0.04 if project.units == "inch" else 1.0), 4)
            elif key in LENGTHS and isinstance(value, (int, float)) and not isinstance(value, bool):
                value = round(value * k, 4)
            setattr(op, key, copy.deepcopy(value))
        if hasattr(op, "contour") and getattr(op, "external", True) is False:
            op.contour = _bore(project)
        project.operations.append(op)
        added += 1
    return added


# Built-in templates (the workflow document's examples), in mm. Tool numbers are ALPHA-T's standard library.
BUILTIN = [
    (N_("Shaft: rough T1, finish T3"), [
        {"type": "tool_change", "tool": 1},
        {"type": "area_clear", "tool": 1, "depth_of_cut": 2.0, "offset_x": 0.2, "offset_z": 0.1, "feed": 0.2},
        {"type": "tool_change", "tool": 3},
        {"type": "finish", "tool": 3, "feed": 0.1},
    ]),
    (N_("Bush: drill T25, bore rough and finish T12"), [
        {"type": "tool_change", "tool": 25},
        {"type": "drill", "tool": 25, "depth": THROUGH, "peck": 10.0, "feed": 0.1},
        {"type": "tool_change", "tool": 12},
        {"type": "area_clear", "tool": 12, "external": False, "depth_of_cut": 1.0, "offset_x": 0.2, "offset_z": 0.1,
         "feed": 0.15},
        {"type": "finish", "tool": 12, "external": False, "feed": 0.08},
    ]),
    (N_("Groove and thread: groove T16, thread T31"), [
        {"type": "tool_change", "tool": 16},
        {"type": "groove", "tool": 16, "tip_width": 2.0, "stepover": 1.5, "feed": 0.08},
        {"type": "tool_change", "tool": 31},
        {"type": "thread", "tool": 31, "cut_depth": 0.2, "spring_passes": 1},
    ]),
]


def builtin() -> list[dict]:
    return [{"format": FORMAT, "name": _(name), "units": "mm", "operations": ops, "builtin": True}
            for name, ops in BUILTIN]


def user_templates() -> list[tuple[Path, dict]]:
    out = []
    folder = template_dir()
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if t.get("format") == FORMAT and _valid(t):
            out.append((f, t))
    return out


def _valid(t: dict) -> bool:
    return all(isinstance(o, dict) and o.get("type") in OPERATION_TYPES for o in t.get("operations", []))


def save_user_template(t: dict) -> Path:
    folder = template_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\- ]+", "", t["name"]).strip().replace(" ", "-")[:60] or "template"
    path = folder / f"{stem}.json"
    path.write_text(json.dumps(t, indent=1) + "\n", encoding="utf-8")
    return path
