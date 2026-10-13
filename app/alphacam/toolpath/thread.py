"""Threading (#127=20 external and internal), G32 passes (THREAD sample: X40 -> X20 to Z-25, pitch 1.5, first
cut 1; THREAD_internal: X20 -> X40, U-21.; THREAD_p2_len30: pitch 2 over 30, W-4.).

Cut n (n = 1..N) goes to radial depth first * sqrt(n) (constant chip area), N = (total depth / first)^2 rounded up,
the last cut clamped to the inner diameter. Each cut ends one pitch before the thread end and pulls out over the
last pitch (U = outer - inner + 1 on diameter: U21 for 40 -> 20, U13 for 40 -> 28 in THREAD_depth6, U-21 internal).
An infeed angle moves each cut's start Z (ThreadPlan.cut_start_z, THREAD_angle30); spring passes repeat the last
cut in a #127=21 block (THREAD_spring2: two)."""

from __future__ import annotations

from ..i18n import tr
import math
from dataclasses import dataclass

from ..model.geometry import Line
from ..model.operations import Thread


MAX_CUTS = 999  # #121 counts the cuts; more than this is a typing error (first cut 0.01 over 10 mm = 1M cuts)


@dataclass
class ThreadPlan:
    xs: list[float]  # cut diameters
    z_end: float  # thread end incl. lead-out (G32 stops one pitch before)
    pullout_u: float  # incremental X of the pull-out
    inner: float  # final diameter of the last cut (#125): inner line external, outer line internal
    z_start: float = 0.0  # where every cut starts: top of the thread + lead-in
    first_cut: float = 0.0  # radial depth of the first cut (SD1 G76 D)
    angle: float = 0.0  # ANGLE (flank infeed, degrees)
    depth: float = 0.0  # radial thread depth
    decimals: int = 3

    def cut_start_z(self, n: int) -> float:
        """THREAD_angle30: with an infeed angle cut n starts depth_n * tan(angle / 2) further towards the chuck
        (cut 1, depth 1 at 30 degrees: Z5 -> 4.732), so the tool follows one flank. depth_n is the exact
        first * sqrt(n), not the rounded diameter (cut 51: 7.14143, Z3.086, not 3.087); the real Z is rounded."""
        if not self.angle:
            return self.z_start
        depth = min(self.first_cut * math.sqrt(n), self.depth)
        return round(self.z_start - depth * math.tan(math.radians(self.angle / 2)), self.decimals)


def plan_thread(op: Thread, outer: Line, inner: Line, decimals: int) -> ThreadPlan:
    """The radial depth of each cut is rounded to the output decimals before it is doubled (cut 6:
    sqrt(6) -> 2.449, X35.102, not 35.101)."""
    if not op.towards:
        # THREAD_away: ALPHA-T only moves the start to Z-5 (inside the thread) and flips the pull-out W; the cuts
        # still run towards the chuck
        raise NotImplementedError(tr("threading AWAY: ALPHA-T itself starts every cut inside the thread for it, so the "
                                  "app does not offer it; use TOWARDS"))
    if outer.a.x != outer.b.x or inner.a.x != inner.b.x:
        raise ValueError(tr("thread lines must be parallel to Z"))
    xo, xi = outer.a.x, inner.a.x
    depth = (xo - xi) / 2
    # NO. OF CUTS mode: ALPHA-T stores the first cut rounded to 4 decimals (SD1 D4.0825)
    first = round(depth / math.sqrt(op.cuts), 4) if op.cuts > 0 else op.cut_depth
    if depth <= 0 or first <= 0:
        raise ValueError(tr("thread: outer diameter must be above the inner one, first cut > 0"))
    n = math.ceil(round((depth / first) ** 2, 9))
    if n > MAX_CUTS:
        raise ValueError(tr("threading: {n} cuts; make the first cut deeper or set fewer cuts (at most {max})",
                            n=n, max=MAX_CUTS))
    steps = [2 * round(first * math.sqrt(k), decimals) for k in range(1, n + 1)]
    # THREAD_leadin2_leadout2: cuts start at the thread top + lead-in (default 5) and end at its end - lead-out
    z_end = round(min(outer.a.z, outer.b.z) - op.lead_out, 9)
    z_start = round(max(outer.a.z, outer.b.z) + op.lead_in, 9)
    if op.external:  # from the outer diameter in towards the inner one
        return ThreadPlan([round(max(xo - d, xi), 9) for d in steps], z_end, xo - xi + 1, xi, z_start, first,
                          op.angle, depth, decimals)
    # internal (THREAD_internal): from the inner (bore) diameter out to the outer one, pull-out inwards
    return ThreadPlan([round(min(xi + d, xo), 9) for d in steps], z_end, -(xo - xi + 1), xo, z_start, first,
                      op.angle, depth, decimals)
