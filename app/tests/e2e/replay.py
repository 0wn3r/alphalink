"""Replay a finger recorded on the Surface with `libinput record` (a touchscreen's evdev events) through libei, with
its own timing, speed and jitter: closer to a real finger than the harness's straight, even strokes.

    strokes = load("recordings/surface-editor-swipes-2026-10-19.yml")    # one list of frames per finger-down
    session.input.play(placed(strokes[0], at=(x, y)))                     # stroke 1, its first touch at x, y

Positions are converted to logical pixels from the device's resolution (units per mm) at the Surface Pro 7's
1368 × 912 logical screen (259 × 171 mm), and placed relative to where the stroke starts."""

from __future__ import annotations

import re
from pathlib import Path

SCREEN_PX = (1368, 912)
SCREEN_MM = (259.0, 171.0)

# a "- [  sec,  usec,  type,  code,  value]" line of libinput record's evdev events
EVENT = re.compile(r"^\s*- \[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(-?\d+)\]")
ABS_MT_SLOT, ABS_MT_TRACKING_ID, ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 47, 57, 53, 54
EV_SYN, EV_ABS = 0, 3


def _resolution(text: str) -> tuple[float, float]:
    """Units per mm of ABS_MT_POSITION_X / _Y, from the recording's device description."""
    res = {}
    for code in ("53", "54"):
        m = re.search(rf"Event code {code} \(ABS_MT_POSITION_[XY]\).*?Resolution\s+(\d+)", text, re.S)
        res[code] = float(m.group(1)) if m else None
    if not all(res.values()):  # older records: the plain ABS_X / ABS_Y lines carry it
        x = re.search(r"Event code 0 \(ABS_X\).*?Resolution\s+(\d+)", text, re.S)
        y = re.search(r"Event code 1 \(ABS_Y\).*?Resolution\s+(\d+)", text, re.S)
        res = {"53": float(x.group(1)), "54": float(y.group(1))}
    return res["53"], res["54"]


def load(path: str | Path) -> list[list[tuple[float, dict[int, tuple[float, float] | None]]]]:
    """The recording as strokes: from a first finger down to the last finger up, each a list of frames
    (seconds from the stroke's start, {slot: (x px, y px) or None for a lift})."""
    text = Path(path).read_text()
    rx, ry = _resolution(text)
    kx, ky = SCREEN_PX[0] / SCREEN_MM[0] / rx, SCREEN_PX[1] / SCREEN_MM[1] / ry
    strokes, frame, pos, active = [], {}, {}, set()
    slot, start, current = 0, None, None
    for line in text.splitlines():
        m = EVENT.match(line)
        if not m:
            continue
        sec, usec, typ, code, value = (int(g) for g in m.groups())
        if typ == EV_ABS:
            if code == ABS_MT_SLOT:
                slot = value
            elif code == ABS_MT_TRACKING_ID:
                if value < 0:
                    frame[slot] = None
                    active.discard(slot)
                else:
                    active.add(slot)
                    pos[slot] = [None, None]
            elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                p = pos.setdefault(slot, [None, None])
                p[code - ABS_MT_POSITION_X] = value * (kx if code == ABS_MT_POSITION_X else ky)
                frame[slot] = p
        elif typ == EV_SYN and code == 0 and frame:
            t = sec + usec / 1e6
            if current is None:
                current, start = [], t
            current.append((t - start, {s: (tuple(p) if p is not None and None not in p else None)
                                        for s, p in frame.items()}))
            frame = {}
            if not active:
                strokes.append(current)
                current = None
    return strokes


def placed(stroke, at: tuple[float, float], upwards: bool = False):
    """The stroke moved so that its first touch lands at `at`; with `upwards`, mirrored if needed so the finger goes
    up the screen (to scroll a list that starts at its top)."""
    first = next(p for _t, slots in stroke for p in slots.values() if p)
    flip = -1 if upwards and travel(stroke)[1] > 0 else 1
    return [(t, {s: (at[0] + p[0] - first[0], at[1] + flip * (p[1] - first[1])) if p else None
                 for s, p in slots.items()}) for t, slots in stroke]


def travel(stroke) -> tuple[float, float]:
    """How far the stroke's first finger went, first touch to last position (px)."""
    pts = [p for _t, slots in stroke for p in slots.values() if p]
    return pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]
