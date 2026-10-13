"""ViewTransform: model (X diameter, Z) <-> screen pixels. Z runs right, X up; the drawing is never rotated (T5)."""

from __future__ import annotations

from dataclasses import dataclass

MIN_PX_PER_MM = 0.05
MAX_PX_PER_MM = 100.0  # 0.01 mm per pixel


@dataclass
class ViewTransform:
    scale: float = 1.0  # pixels per mm
    ox: float = 0.0  # screen position of Z0
    oy: float = 0.0  # screen position of X0

    def to_screen(self, x_dia: float, z: float) -> tuple[float, float]:
        return self.ox + z * self.scale, self.oy - x_dia / 2 * self.scale

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        """Screen -> (X diameter, Z)."""
        return 2 * (self.oy - sy) / self.scale, (sx - self.ox) / self.scale

    def pan(self, dx: float, dy: float) -> None:
        self.ox += dx
        self.oy += dy

    def zoom_at(self, sx: float, sy: float, factor: float) -> None:
        """Zoom about a screen point (cursor or the fingers' centre), clamped."""
        new = min(max(self.scale * factor, MIN_PX_PER_MM), MAX_PX_PER_MM)
        f = new / self.scale
        self.ox = sx - (sx - self.ox) * f
        self.oy = sy - (sy - self.oy) * f
        self.scale = new

    def fit(self, bounds: tuple[float, float, float, float], width: float, height: float, margin: float = 24) -> None:
        """bounds = (z_min, z_max, r_min, r_max) in mm (r = radius)."""
        z0, z1, r0, r1 = bounds
        self.scale = min(max(min((width - 2 * margin) / max(z1 - z0, 1e-6),
                                 (height - 2 * margin) / max(r1 - r0, 1e-6)), MIN_PX_PER_MM), MAX_PX_PER_MM)
        self.ox = margin + (width - 2 * margin - (z1 - z0) * self.scale) / 2 - z0 * self.scale
        self.oy = margin + (height - 2 * margin - (r1 - r0) * self.scale) / 2 + r1 * self.scale

    def copy(self) -> "ViewTransform":
        return ViewTransform(self.scale, self.ox, self.oy)
