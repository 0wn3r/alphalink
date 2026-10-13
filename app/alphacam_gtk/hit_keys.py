"""ALPHA-T's single-key "hit codes" on the Part page, so an ALPHA-T user's fingers keep working.

The table is ALPHA-T's own (HELP/ENG/SD503.HLP), checked in ALPHA-T under DOSBox-X:

    A find nearest arc (- erases it)   L find nearest line (- erases it)   P find nearest point
    I find nearest intersection        G X,Z absolute move                 H X,Z incremental move
    Y L,A move                          S start point at the cursor         X draw line X,Z
    D draw line D,A                     + draw line freehand                F radius / chamfer
    O 2 entity radius                   K trim corner                       M X,Z measure
    N L,A measure                       T input text                        U erase text
    Q erase all the drawing             Z zoom                              V recall view
    R redraw                            C CAD / CAM menus                   E exit (not mapped: E edits, below)
    cursor keys move the cursor, PgUp / PgDn step x10 / :10, Home screen centre, End reference point (0,0)

The ALPHA-T cursor is the pointer's last position on the drawing, or where these keys put it. Typed numbers go to
the command entry, as typing a coordinate did in ALPHA-T. Letters work while no text field has the focus, and in
the command entry too (a coordinate never has letters), without Ctrl or Alt, so they never clash with the Ctrl
shortcuts or with typing. Added here (not in ALPHA-T): E edits the selected element, B is the Chamfer tool. They follow the key's place on the keyboard, so they also work with the Ukrainian layout
active (the key marked A/Ф is A). F1 stays this app's help."""

from __future__ import annotations

# letter -> action name handled by DrawingTools.hit_key
HIT_KEYS = {
    "a": "find-arc",
    "c": "cam",
    "b": "chamfer",
    "d": "line-la",
    "e": "edit",
    "f": "radius",
    "g": "move-abs",
    "h": "move-inc",
    "i": "find-intersection",
    "k": "corner",
    "l": "find-line",
    "m": "measure-xz",
    "n": "measure-la",
    "o": "fillet",
    "p": "find-point",
    "q": "erase-drawing",
    "r": "fit",
    "s": "start",
    "t": "text",
    "u": "erase-text",
    "v": "recall-view",
    "x": "line",
    "y": "move-la",
    "z": "zoom-in",
    "+": "freehand",
}

# hardware key codes (evdev + 8, the same under X11 and Wayland) of the letter keys on a QWERTY keyboard; used when
# the active layout gives a non-Latin letter, e.g. Ukrainian "ф" on the A key
LATIN_BY_KEYCODE = dict(zip(
    (38, 56, 54, 40, 26, 41, 42, 43, 31, 44, 45, 46, 58, 57, 32, 33, 24, 27, 39, 28, 30, 55, 25, 53, 29, 52),
    "abcdefghijklmnopqrstuvwxyz"))

# keys that move the cursor (Gdk key names); they act only while the drawing has the focus
CURSOR_KEYS = {"Left": (0, -1), "Right": (0, 1), "Up": (1, 0), "Down": (-1, 0)}
STEP_KEYS = {"Page_Up": 10.0, "Page_Down": 0.1}

# keys that start a typed coordinate in the command entry
ENTRY_CHARS = set("0123456789.-@,")


def key_char(char: str, keycode: int) -> str:
    """The character a key stands for in ALPHA-T: a non-Latin letter becomes the Latin letter on that key."""
    if len(char) == 1 and char.isalpha() and not char.isascii():
        return LATIN_BY_KEYCODE.get(keycode, char)
    return char


def hit_action(char: str, picking: bool = False, selected: bool = False) -> str | None:
    """The action for a typed character (Enter is "\\r"), or None. `picking`: a contour is being picked;
    `selected`: a drawing element is selected, so "-" erases it as in ALPHA-T."""
    if not char or len(char) != 1:
        return None
    if picking:
        # ALPHA-T: Enter accepts each point, 3 finishes the contour
        return "pick-done" if char in ("3", "\r", "\n") else None
    if char in ("\r", "\n"):
        return "accept"
    if char == "-" and selected:
        return "erase"
    if char in ENTRY_CHARS:
        return "type"
    return HIT_KEYS.get(char.lower())
