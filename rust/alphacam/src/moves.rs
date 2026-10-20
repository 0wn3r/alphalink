//! Flatten a preview into segments (drawing, hit testing, the safety check), in program order, each tagged with its
//! operation.

use std::collections::HashMap;
use std::f64::consts::PI;

use crate::preview::Preview;
use crate::toolpath::MoveKind;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Segment {
    pub kind: MoveKind,
    /// (X diameter, Z)
    pub a: (f64, f64),
    pub b: (f64, f64),
    /// operation the move belongs to (None: implicit tool block / empty blocks)
    pub op_index: Option<usize>,
    /// PRG line of the move
    pub line: usize,
    /// arcs
    pub centre: Option<(f64, f64)>,
    /// PRG block label (N10, N20 ...)
    pub block: i64,
    /// #121 pass within the block (0 outside passes)
    pub pass_no: i64,
}

impl Segment {
    /// A segment outside any program (the safety check samples drawn arcs with it).
    pub fn new(kind: MoveKind, a: (f64, f64), b: (f64, f64), centre: Option<(f64, f64)>) -> Self {
        Segment { kind, a, b, op_index: None, line: 0, centre, block: 0, pass_no: 0 }
    }

    /// Points along the segment (arcs sampled), for hit testing.
    pub fn polyline(&self, steps: usize) -> Vec<(f64, f64)> {
        let Some((cx, cz)) = self.centre else { return vec![self.a, self.b] };
        let ((ax, az), (bx, bz)) = (self.a, self.b);
        // work in (z, r) with r = X/2; G3 (ARC_CCW) is counter-clockwise with Z right and X up
        let a0 = (ax / 2.0 - cx / 2.0).atan2(az - cz);
        let a1 = (bx / 2.0 - cx / 2.0).atan2(bz - cz);
        let rad = f64::hypot(ax / 2.0 - cx / 2.0, az - cz);
        let ccw = self.kind == MoveKind::ArcCcw;
        let mut sweep = a1 - a0;
        if ccw && sweep < 0.0 {
            sweep += 2.0 * PI;
        }
        if !ccw && sweep > 0.0 {
            sweep -= 2.0 * PI;
        }
        (0..=steps)
            .map(|t| {
                let angle = a0 + sweep * t as f64 / steps as f64;
                (2.0 * (cx / 2.0 + rad * angle.sin()), cz + rad * angle.cos())
            })
            .collect()
    }
}

pub fn segments(preview: &Preview) -> Vec<Segment> {
    let Some(program) = &preview.program else { return vec![] };
    let owner: HashMap<i64, Option<usize>> =
        preview.ops.iter().flat_map(|op| op.blocks.iter().map(|b| (b.label, op.op_index))).collect();
    let mut out = vec![];
    let mut pos: Option<(f64, f64)> = None;
    for block in &program.blocks {
        if pos.is_none() && block.start().is_some() {
            pos = block.start();
        }
        let op = owner.get(&block.label).copied().flatten();
        for p in &block.passes {
            for m in &p.moves {
                let (Some(x), Some(z)) = (m.x, m.z) else { continue };
                let end = (x, z);
                let segment = |a, centre| Segment { kind: m.kind, a, b: end, op_index: op, line: m.line, centre,
                                                    block: block.label, pass_no: p.number };
                if m.kind == MoveKind::Dwell {
                    out.push(segment(end, None));
                } else if let Some(start) = pos {
                    let centre = m.centre_x.map(|cx| (cx, m.centre_z.unwrap_or(f64::NAN)));
                    out.push(segment(start, centre));
                }
                pos = Some(end);
            }
        }
    }
    out
}

/// Indices where a new pass (or block) starts: the stops of the simulation's step buttons.
pub fn pass_starts(segs: &[Segment]) -> Vec<usize> {
    let mut key = None;
    let mut starts = vec![];
    for (i, s) in segs.iter().enumerate() {
        let k = Some((s.block, s.pass_no));
        if k != key {
            starts.push(i);
            key = k;
        }
    }
    starts
}
