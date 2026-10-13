"""Threading (#127=20), external, G32 passes (THREAD sample: X40 -> X20 to Z-25, pitch 1.5, first cut 1).

Cut n (n = 1..N) goes to radial depth first * sqrt(n) (constant chip area), N = (total depth / first)^2 rounded up,
the last cut clamped to the inner diameter. Each cut ends one pitch before the thread end and pulls out over the
last pitch (U = outer - inner + 1 on diameter; assumed, the sample has U21 for 40 -> 20)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..model.geometry import Line
from ..model.operations import Thread


@dataclass
class ThreadPlan:
    xs: list[float]  # cut diameters
    z_end: float  # thread end (G32 stops one pitch before)
    pullout_u: float  # incremental X of the pull-out
    inner: float


def plan_thread(op: Thread, outer: Line, inner: Line, decimals: int) -> ThreadPlan:
    """The radial depth of each cut is rounded to the output decimals before it is doubled (cut 6:
    sqrt(6) -> 2.449, X35.102, not 35.101)."""
    if not op.external or op.lead_in or op.lead_out or op.spring_pass or not op.towards:
        raise NotImplementedError("threading: only external, towards, no lead-in/out or spring pass (no sample)")
    if outer.a.x != outer.b.x or inner.a.x != inner.b.x:
        raise ValueError("thread lines must be parallel to Z")
    xo, xi = outer.a.x, inner.a.x
    depth = (xo - xi) / 2
    if depth <= 0 or op.cut_depth <= 0:
        raise ValueError("thread: outer diameter must be above the inner one, first cut > 0")
    n = math.ceil(round((depth / op.cut_depth) ** 2, 9))
    xs = [round(max(xo - 2 * round(op.cut_depth * math.sqrt(k), decimals), xi), 9) for k in range(1, n + 1)]
    return ThreadPlan(xs, min(outer.a.z, outer.b.z), xo - xi + 1, xi)
