//! The tool's shape for the simulation: ALPHA-T's own picture when the library was imported with its ATOOLIB.TL2,
//! otherwise a simple shape from the tool's type, clearance angles, width and nose radius. Polylines of (Z, radius)
//! points in mm with the tip at (0, 0), the way ALPHA-T draws them: +Z right, the radius up. Turning tools hang down
//! from above the part, boring bars and face tools reach in along +Z.

use std::f64::consts::PI;

use crate::model::Tool;

/// A polyline of (Z, radius) points in mm.
pub type Polyline = Vec<(f64, f64)>;

fn circle(cz: f64, cr: f64, r: f64, steps: usize) -> Polyline {
    (0..=steps)
        .map(|k| {
            let a = 2.0 * PI * k as f64 / steps as f64;
            (cz + r * a.cos(), cr + r * a.sin())
        })
        .collect()
}

/// A bar along +Z from z0, between the radii r0 and r1.
fn bar(z0: f64, r0: f64, r1: f64) -> Polyline {
    let length = 50.0;
    vec![(z0, r0), (z0 + length, r0), (z0 + length, r1), (z0, r1), (z0, r0)]
}

/// A shank going up from the radius r0, between z0 and z1.
fn shank(z0: f64, z1: f64, r0: f64) -> Polyline {
    let height = 35.0;
    vec![(z0, r0), (z0, r0 + height), (z1, r0 + height), (z1, r0)]
}

pub fn internal(tool: &Tool) -> bool {
    let name = tool.name.to_lowercase();
    tool.kind == "bore" || tool.front_angle < 0.0 || name.contains("internal") || name.contains("int.")
}

/// Python's `x or default` for a number.
fn or(x: f64, default: f64) -> f64 {
    if x != 0.0 { x } else { default }
}

pub fn outline(tool: Option<&Tool>) -> Vec<Polyline> {
    let Some(tool) = tool else { return vec![] };
    if !tool.outline.is_empty() {
        return tool.outline.iter().map(|line| line.iter().map(|&[z, x]| (z, x)).collect()).collect();
    }
    let inside = internal(tool);
    match tool.kind.as_str() {
        "drill" => {
            let r = or(tool.width, 10.0) / 2.0;
            let tip = r * 31f64.to_radians().tan(); // 118 degree point
            return vec![vec![(0.0, 0.0), (tip, r), (60.0, r), (60.0, -r), (tip, -r), (0.0, 0.0)]];
        }
        "groove" => {
            let w = or(tool.width, 3.0);
            if inside {
                return vec![vec![(0.0, 0.0), (0.0, -6.0), (w, -6.0), (w, 0.0), (0.0, 0.0)], bar(0.0, -6.0, -16.0)];
            }
            return vec![vec![(0.0, 0.0), (0.0, 20.0), (w, 20.0), (w, 0.0), (0.0, 0.0)], shank(-4.0, w + 8.0, 20.0)];
        }
        "thread" => {
            let s = if inside { -1.0 } else { 1.0 };
            let mut tip = vec![(-1.73, s * 3.0), (0.0, 0.0), (1.73, s * 3.0)];
            if inside {
                tip.push((-1.73, -3.0));
                return vec![tip, bar(-1.73, -3.0, -14.0)];
            }
            tip.push((-1.73, 3.0));
            return vec![tip, shank(-6.0, 6.0, 3.0)];
        }
        _ => {}
    }
    let (front, rear) = (tool.front_angle, tool.rear_angle);
    if front == rear {
        // ball-ends and anything without angles: the nose
        let r = or(tool.nose_radius, 2.5);
        return vec![circle(0.0, 0.0, r, 24), bar(0.0, -r, r)];
    }
    let length = 10.0;
    let f = (length * front.to_radians().cos(), length * front.to_radians().sin());
    let b = (length * rear.to_radians().cos(), length * rear.to_radians().sin());
    let insert = vec![(0.0, 0.0), f, (f.0 + b.0, f.1 + b.1), b, (0.0, 0.0)];
    let (cz, cr) = ((f.0 + b.0) / 2.0, (f.1 + b.1) / 2.0);
    if inside || (f.0 > 0.0 && b.0 > 0.0 && f.1 * b.1 < 0.0) {
        // boring bars, face tools
        return vec![insert, bar(cz, cr - 4.0, cr + 4.0)];
    }
    vec![insert, shank(cz - 6.0, cz + 6.0, cr)]
}
