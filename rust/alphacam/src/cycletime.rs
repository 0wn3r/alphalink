//! Cycle time estimate (workflow improvements, step 5): from the moves of the preview and the feeds.
//!
//! The 400T turns at constant surface speed (G96) in the Harrison macros, so a feed move's time depends on the
//! diameter: rpm = 1000 Vc / (pi D), capped by the spindle limit. Each tool can carry its surface speed (m/min) and a
//! spindle limit of its own in the library; otherwise a default per tool type is used. Threads and drills run at the
//! rpm of their largest diameter. Tool changes are manual (the operator indexes the turret), so they are counted, and
//! their time is an allowance the user sets. It is an estimate: acceleration and the control's block time are left
//! out.

use std::collections::HashMap;
use std::f64::consts::PI;

use indexmap::IndexMap;

use crate::checks::tool;
use crate::model::{Project, Tool, ToolLibrary};
use crate::moves::Segment;
use crate::preview::{Move, Preview};
use crate::py::{self, hypot, round_int};
use crate::toolpath::MoveKind;

/// m/min when a tool has none of its own (carbide in steel, on the safe side)
pub const DEFAULT_SURFACE_SPEED: &[(&str, f64)] =
    &[("turn", 180.0), ("bore", 140.0), ("groove", 90.0), ("thread", 60.0), ("drill", 60.0)];

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Machine {
    /// rpm
    pub spindle_max: f64,
    /// mm/min
    pub rapid: f64,
    /// s for the operator to change a tool
    pub tool_change: f64,
}

impl Default for Machine {
    fn default() -> Self {
        Machine { spindle_max: 2500.0, rapid: 6000.0, tool_change: 30.0 }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Estimate {
    /// operation index -> time, in program order
    pub seconds: IndexMap<Option<usize>, f64>,
    pub tool_changes: usize,
    pub machine: Machine,
}

impl Estimate {
    pub fn cutting(&self) -> f64 {
        py::sum(self.seconds.values().copied())
    }

    pub fn total(&self) -> f64 {
        self.cutting() + self.tool_changes as f64 * self.machine.tool_change
    }
}

pub fn surface_speed(tool: Option<&Tool>) -> f64 {
    if let Some(t) = tool.filter(|t| t.surface_speed > 0.0) {
        return t.surface_speed;
    }
    let kind = tool.map_or("turn", |t| t.kind.as_str());
    let default = |k: &str| DEFAULT_SURFACE_SPEED.iter().find(|(n, _)| *n == k).map(|(_, v)| *v);
    default(kind).or_else(|| default("turn")).unwrap_or_default()
}

pub fn rpm(tool: Option<&Tool>, diameter_mm: f64, machine: &Machine) -> f64 {
    let mut limit = machine.spindle_max;
    if let Some(t) = tool.filter(|t| t.max_rpm > 0.0) {
        limit = py_min(limit, t.max_rpm);
    }
    if diameter_mm <= 1e-6 {
        return limit;
    }
    py_min(1000.0 * surface_speed(tool) / (PI * diameter_mm), limit)
}

/// Python's `min(a, b)`: `a` unless `b` is smaller.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

pub fn estimate(project: &Project, preview: &Preview, library: Option<&ToolLibrary>, machine: Option<Machine>)
                -> Estimate {
    let machine = machine.unwrap_or_default();
    let mut est = Estimate { machine, ..Estimate::default() };
    let Some(program) = &preview.program else { return est };
    let k = if project.units == "inch" { 25.4 } else { 1.0 }; // everything below in mm
    let owner: HashMap<u32, Option<usize>> =
        preview.ops.iter().flat_map(|op| op.blocks.iter().map(|b| (b.label, op.op_index))).collect();
    let mut pos: Option<(f64, f64)> = None;
    for block in &program.blocks {
        let name = block.op_name();
        if name == "tool_change" {
            est.tool_changes += 1;
        }
        let tool = block.tool().filter(|n| *n != 0).and_then(|n| tool(project, n, library));
        let feed = block.feed().unwrap_or(0.0) * k; // mm/rev (threads: the pitch)
        let i = owner.get(&block.label).copied().flatten();
        if pos.is_none() && block.start().is_some() {
            pos = block.start();
        }
        // threads and drills turn at one speed: the largest diameter of the block
        let mut fixed = None;
        if matches!(name.as_str(), "thread" | "thread_spring" | "drill") {
            let xs = block.moves().filter_map(|m| m.x.map(f64::abs));
            let mut d = xs.reduce(|a, b| if b > a { b } else { a }).unwrap_or(0.0) * k;
            if let Some(t) = tool.filter(|t| name == "drill" && t.width != 0.0) {
                d = t.width; // the drill's own diameter (library sizes are mm)
            }
            fixed = Some(rpm(tool, d, &machine)).filter(|r| *r != 0.0);
        }
        let mut t = 0.0;
        for m in block.moves() {
            if m.kind == MoveKind::Dwell {
                let speed = fixed.unwrap_or_else(|| rpm(tool, pos.map_or(0.0, |p| p.0) * k, &machine));
                t += 60.0 * m.dwell_revs.unwrap_or(0.0) / speed;
                continue;
            }
            let (Some(x), Some(z)) = (m.x, m.z) else { continue };
            let end = (x, z);
            if let Some(start) = pos {
                let length = path_length(m, start, end) * k;
                if m.kind == MoveKind::Rapid {
                    t += 60.0 * length / machine.rapid;
                } else if feed > 0.0 {
                    let d = (start.0.abs() + end.0.abs()) / 2.0 * k; // mean diameter of the move
                    t += 60.0 * length / (feed * fixed.unwrap_or_else(|| rpm(tool, d, &machine)));
                }
            }
            pos = Some(end);
        }
        if t != 0.0 {
            *est.seconds.entry(i).or_insert(0.0) += t;
        }
    }
    est
}

/// Path length on the radius (X is a diameter), arcs along the arc.
fn path_length(m: &Move, a: (f64, f64), b: (f64, f64)) -> f64 {
    let pts = match (m.kind, m.centre_x) {
        (MoveKind::ArcCw | MoveKind::ArcCcw, Some(cx)) => {
            Segment::new(m.kind, a, b, Some((cx, m.centre_z.unwrap_or(f64::NAN)))).polyline(24)
        }
        _ => vec![a, b],
    };
    py::sum(pts.windows(2).map(|w| hypot((w[1].0 - w[0].0) / 2.0, w[1].1 - w[0].1)))
}

/// h:mm:ss from an hour on, else m:ss.
pub fn mmss(seconds: f64) -> String {
    let s = round_int(seconds);
    if s >= 3600 {
        format!("{}:{:02}:{:02}", s.div_euclid(3600), s.div_euclid(60).rem_euclid(60), s.rem_euclid(60))
    } else {
        format!("{}:{:02}", s.div_euclid(60), s.rem_euclid(60))
    }
}
