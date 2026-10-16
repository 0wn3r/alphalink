//! ViewTransform: model (X diameter, Z) <-> screen pixels. Z runs right, X up; the drawing is never rotated (T5).

pub const MIN_PX_PER_MM: f64 = 0.05;
/// 0.01 mm per pixel
pub const MAX_PX_PER_MM: f64 = 100.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ViewTransform {
    /// pixels per mm
    pub scale: f64,
    /// screen position of Z0
    pub ox: f64,
    /// screen position of X0
    pub oy: f64,
}

impl Default for ViewTransform {
    fn default() -> Self {
        ViewTransform { scale: 1.0, ox: 0.0, oy: 0.0 }
    }
}

impl ViewTransform {
    pub fn to_screen(&self, x_dia: f64, z: f64) -> (f64, f64) {
        (self.ox + z * self.scale, self.oy - x_dia / 2.0 * self.scale)
    }

    /// Screen -> (X diameter, Z).
    pub fn to_world(&self, sx: f64, sy: f64) -> (f64, f64) {
        (2.0 * (self.oy - sy) / self.scale, (sx - self.ox) / self.scale)
    }

    pub fn pan(&mut self, dx: f64, dy: f64) {
        self.ox += dx;
        self.oy += dy;
    }

    /// Zoom about a screen point (cursor or the fingers' centre), clamped.
    pub fn zoom_at(&mut self, sx: f64, sy: f64, factor: f64) {
        let new = (self.scale * factor).clamp(MIN_PX_PER_MM, MAX_PX_PER_MM);
        let f = new / self.scale;
        self.ox = sx - (sx - self.ox) * f;
        self.oy = sy - (sy - self.oy) * f;
        self.scale = new;
    }

    /// bounds = (z_min, z_max, r_min, r_max) in mm (r = radius).
    pub fn fit(&mut self, (z0, z1, r0, r1): (f64, f64, f64, f64), width: f64, height: f64, margin: f64) {
        self.scale = ((width - 2.0 * margin) / (z1 - z0).max(1e-6))
            .min((height - 2.0 * margin) / (r1 - r0).max(1e-6))
            .clamp(MIN_PX_PER_MM, MAX_PX_PER_MM);
        self.ox = margin + (width - 2.0 * margin - (z1 - z0) * self.scale) / 2.0 - z0 * self.scale;
        self.oy = margin + (height - 2.0 * margin - (r1 - r0) * self.scale) / 2.0 + r1 * self.scale;
    }
}

#[cfg(test)]
mod tests {
    use super::ViewTransform;

    #[test]
    fn round_trip_and_zoom() {
        let mut v = ViewTransform { scale: 4.0, ox: 100.0, oy: 200.0 };
        let (sx, sy) = v.to_screen(40.0, -25.0);
        assert_eq!(v.to_world(sx, sy), (40.0, -25.0));
        v.zoom_at(sx, sy, 2.0);
        assert_eq!(v.to_screen(40.0, -25.0), (sx, sy)); // the point under the fingers stays put
    }
}
