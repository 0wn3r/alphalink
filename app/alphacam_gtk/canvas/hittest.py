"""Nearest toolpath segment within a screen-pixel radius (TOUCH.md 3: mouse 6, pen 10, touch 24 px)."""

from __future__ import annotations

import math

from .paths import Segment
from .view import ViewTransform

RADIUS_PX = {"mouse": 6.0, "pen": 10.0, "touch": 24.0}


def _dist_point_segment(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def hit(segs: list[Segment], view: ViewTransform, sx: float, sy: float, radius_px: float) -> Segment | None:
    best, best_d = None, radius_px
    for s in segs:
        pts = [view.to_screen(*p) for p in s.polyline()]
        for a, b in zip(pts, pts[1:] or pts):
            d = _dist_point_segment((sx, sy), a, b)
            if d <= best_d:
                best, best_d = s, d
    return best
