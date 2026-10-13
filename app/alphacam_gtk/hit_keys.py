"""ALPHA-T's single-key "hit codes" on the Part page, so an ALPHA-T user's fingers keep working.

ALPHA-T draws with one letter per command (its HELP screen): S start point, X / Y draw line, G absolute move,
H incremental move, F radius/chamfer, O fillet, B trim, L / A erase line / arc, Z zoom, R redraw. Picking a
contour, Enter accepts and 3 finishes. Here each letter selects the matching tool or action; typed numbers go to
the command entry, as typing a coordinate did in ALPHA-T. The keys work only while no text field has the focus and
without Ctrl or Alt, so they never clash with the Ctrl shortcuts or with typing.

Not mapped: Q (erase drawing; New part is Ctrl+N and asks first), D (move by radius and angle), P (find point),
M / N (measure), T / U (text), and F1, which is this app's help rather than ALPHA-T's "accept"."""

from __future__ import annotations

# letter -> action name handled by DrawingTools.hit_key
HIT_KEYS = {
    "s": "start",  # start point: Line tool, type the first point
    "x": "line",  # draw line X-Z
    "y": "line",  # draw line D-A (the app has one Line tool)
    "g": "absolute",  # absolute move: type X,Z
    "h": "relative",  # incremental move: type @dX,dZ
    "f": "fillet",  # radius / chamfer
    "o": "fillet",
    "c": "chamfer",
    "b": "corner",  # trim
    "l": "erase",  # erase line
    "a": "erase",  # erase arc
    "z": "zoom-in",
    "r": "fit",  # redraw
}

# keys that start a typed coordinate in the command entry
ENTRY_CHARS = set("0123456789.-@,")


def hit_action(char: str, picking: bool = False) -> str | None:
    """The action for a typed character (Enter is "\\r"), or None. `picking`: a contour is being picked."""
    if not char or len(char) != 1:
        return None
    if picking:
        # ALPHA-T: Enter accepts each point, 3 finishes the contour
        return "pick-done" if char in ("3", "\r", "\n") else None
    if char in ENTRY_CHARS:
        return "type"
    return HIT_KEYS.get(char.lower())
