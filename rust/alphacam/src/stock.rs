//! The material the program leaves: the billet's half section (Z, radius) with every feed move's swept tool taken
//! away, for the simulation. The polygon work is cavalier_contours' (boolean subtraction of closed polylines).
//!
//! The tool is the insert of its picture (tool_shapes::outline, ALPHA-T's ATOOLIB.TL2 when imported), made convex
//! and, for turning and boring tools, given its nose radius: the round nose sits with its centre at (Z + r, X + r)
//! from the imaginary tip the program drives (toolpath::contour::tip_path). A straight move sweeps the hull of the
//! tool at both ends; an arc is cut as short chords. Rapids cut nothing (the safety check reports them).

use cavalier_contours::polyline::{
    BooleanOp, BooleanResultInfo, PlineBooleanOptions, PlineSource, PlineSourceMut, Polyline,
};

use crate::checks;
use crate::model::project::Billet;
use crate::model::{Project, ToolLibrary};
use crate::moves::Segment;
use crate::tool_shapes::outline;
use crate::toolpath::MoveKind;

/// Without an insert loop in the picture, the part this close to the tip cuts (mm).
const NEAR_TIP: f64 = 12.0;
/// Arcs are cut as chords of at most this angle (degrees).
const CHORD_DEG: f64 = 5.0;
/// A snapshot of the stock every this many moves, so scrubbing back replays only a few moves.
const SNAPSHOT_EVERY: usize = 40;

/// A closed region of the half section: (Z, radius) vertexes, counter-clockwise.
pub type Region = Polyline<f64>;

/// The billet as one region: Z from the face allowance in front of Z0 back to -length, radius from the bore (0 for
/// a bar) to the billet's radius.
pub fn billet(b: &Billet) -> Region {
    let (z0, z1, r0, r1) = (-b.length, b.face_allowance.max(0.0), (b.bore / 2.0).max(0.0), b.diameter / 2.0);
    let mut p = Polyline::new_closed();
    for (z, r) in [(z0, r0), (z1, r0), (z1, r1), (z0, r1)] {
        p.add(z, r, 0.0);
    }
    p
}

/// The insert in a tool picture: the closed loop of edges through the tip at (0, 0) (the holder behind it never
/// reaches the stock). Pictures without such a loop: everything within NEAR_TIP of the tip.
fn insert(lines: &[Vec<(f64, f64)>]) -> Vec<(f64, f64)> {
    let same = |a: (f64, f64), b: (f64, f64)| (a.0 - b.0).abs() < 1e-3 && (a.1 - b.1).abs() < 1e-3;
    let edges: Vec<((f64, f64), (f64, f64))> =
        lines.iter().flat_map(|l| l.windows(2).map(|w| (w[0], w[1]))).filter(|(a, b)| !same(*a, *b)).collect();
    let mut used = vec![false; edges.len()];
    let (mut at, mut loop_pts) = ((0.0, 0.0), vec![(0.0, 0.0)]);
    for _ in 0..edges.len() {
        let next = edges.iter().enumerate().find_map(|(i, &(a, b))| match () {
            _ if used[i] => None,
            _ if same(a, at) => Some((i, b)),
            _ if same(b, at) => Some((i, a)),
            _ => None,
        });
        let Some((i, to)) = next else { break };
        used[i] = true;
        if same(to, (0.0, 0.0)) {
            return loop_pts;
        }
        loop_pts.push(to);
        at = to;
    }
    let mut pts = vec![(0.0, 0.0)];
    for &(a, b) in &edges {
        // every millimetre along the edges, so cutting them off near the tip keeps the edge's direction
        let n = (f64::hypot(b.0 - a.0, b.1 - a.1).ceil() as usize).max(1);
        pts.extend((0..=n).map(|i| {
            let t = i as f64 / n as f64;
            (a.0 + t * (b.0 - a.0), a.1 + t * (b.1 - a.1))
        }));
    }
    pts.retain(|p| f64::hypot(p.0, p.1) <= NEAR_TIP);
    pts
}

/// Convex hull of (Z, radius) points, counter-clockwise (Andrew's monotone chain).
fn hull(mut pts: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    pts.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    pts.dedup();
    if pts.len() < 3 {
        return pts;
    }
    let cross = |o: (f64, f64), a: (f64, f64), b: (f64, f64)| (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0);
    let mut out: Vec<(f64, f64)> = vec![];
    for pass in 0..2 {
        let start = out.len();
        let iter: Box<dyn Iterator<Item = &(f64, f64)>> =
            if pass == 0 { Box::new(pts.iter()) } else { Box::new(pts.iter().rev()) };
        for &p in iter {
            while out.len() >= start + 2 && cross(out[out.len() - 2], out[out.len() - 1], p) <= 0.0 {
                out.pop();
            }
            out.push(p);
        }
        out.pop(); // the last point starts the other half
    }
    out
}

/// The cutting part of the tool as a convex polygon of (Z, radius) offsets from the programmed tip, in mm; None
/// when the tool has no picture.
pub fn cutter(project: &Project, number: i32, library: Option<&ToolLibrary>) -> Option<Vec<(f64, f64)>> {
    let tool = checks::tool(project, number, library)?;
    let pts = insert(&outline(Some(tool)));
    let mut shape = hull(pts);
    let radius = tool.nose_radius;
    if radius > 0.0 && matches!(tool.kind.as_str(), "turn" | "bore") && shape.len() >= 3 {
        shape = round_nose(&shape, radius, crate::tool_shapes::internal(tool));
    }
    if shape.len() < 3 {
        return None;
    }
    Some(shape)
}

/// The insert with a round nose of `radius`: shrunk by the radius (cavalier's parallel offset), grown back as the
/// hull of circles on its corners, and moved so the nose centre is where the program's imaginary tip puts it.
fn round_nose(shape: &[(f64, f64)], radius: f64, internal: bool) -> Vec<(f64, f64)> {
    let mut p = Polyline::new_closed();
    for &(z, r) in shape {
        p.add(z, r, 0.0);
    }
    // counter-clockwise: a positive offset goes to the left, inwards
    let Some(core) = p.parallel_offset(radius).into_iter().next() else { return shape.to_vec() };
    let corners: Vec<(f64, f64)> = core.iter_vertexes().map(|v| (v.x, v.y)).collect();
    // the nose: the shrunk corner nearest the sharp tip
    let Some(&centre) = corners.iter().min_by(|a, b| f64::hypot(a.0, a.1).total_cmp(&f64::hypot(b.0, b.1))) else {
        return shape.to_vec();
    };
    let side = if internal { -1.0 } else { 1.0 };
    let (dz, dr) = (radius - centre.0, side * radius - centre.1);
    let mut pts = vec![];
    for (cz, cr) in corners {
        for i in 0..24 {
            let a = i as f64 * std::f64::consts::TAU / 24.0;
            pts.push((cz + dz + radius * a.cos(), cr + dr + radius * a.sin()));
        }
    }
    hull(pts)
}

fn region(points: &[(f64, f64)]) -> Region {
    let mut p = Polyline::new_closed();
    for &(z, r) in points {
        p.add(z, r, 0.0);
    }
    p
}

/// What one feed move takes away: the tool's hull at the start and end of each chord.
fn swept(seg: &Segment, tool: &[(f64, f64)]) -> Vec<Region> {
    let pts: Vec<(f64, f64)> = match seg.centre {
        Some(_) => {
            let sweep = {
                let all = seg.polyline(72);
                // the angle the arc turns through, from its sampled length and radius
                let len: f64 = all.windows(2).map(|w| f64::hypot((w[1].0 - w[0].0) / 2.0, w[1].1 - w[0].1)).sum();
                let (cx, cz) = seg.centre.unwrap();
                let rad = f64::hypot(seg.a.0 / 2.0 - cx / 2.0, seg.a.1 - cz);
                if rad > 0.0 { (len / rad).to_degrees() } else { 0.0 }
            };
            seg.polyline(((sweep / CHORD_DEG).ceil() as usize).max(1))
        }
        None => vec![seg.a, seg.b],
    };
    pts.windows(2)
        .map(|w| {
            let ((ax, az), (bx, bz)) = (w[0], w[1]);
            let at = |x: f64, z: f64| tool.iter().map(move |&(dz, dr)| (z + dz, x / 2.0 + dr));
            region(&hull(at(ax, az).chain(at(bx, bz)).collect()))
        })
        .collect()
}

/// The stock: the regions left (cutting can split the billet in two).
#[derive(Clone, Debug)]
pub struct Stock {
    pub regions: Vec<Region>,
}

impl Stock {
    pub fn new(b: &Billet) -> Self {
        Stock { regions: vec![billet(b)] }
    }

    /// Take a region away. A cut wholly inside the material (only a crashing rapid could put the tool there) would
    /// make a hole; the half section has none, so such a cut leaves the stock as it is.
    pub fn cut(&mut self, cut: &Region) {
        let Some(c) = cut.extents() else { return };
        let mut out = Vec::with_capacity(self.regions.len());
        for region in self.regions.drain(..) {
            let overlaps = region.extents().is_some_and(|e| {
                e.min_x < c.max_x && c.min_x < e.max_x && e.min_y < c.max_y && c.min_y < e.max_y
            });
            if !overlaps {
                out.push(region);
                continue;
            }
            // slivers thinner than a micron are dropped, and repeated points, so the next cut starts clean
            let options = PlineBooleanOptions { collapsed_area_eps: Some(1e-6), ..Default::default() };
            let result = region.boolean_opt(cut, BooleanOp::Not, &options);
            match result.result_info {
                BooleanResultInfo::Intersected => out.extend(
                    result.pos_plines.into_iter().map(|p| p.pline.remove_repeat_pos(1e-5).unwrap_or(p.pline)),
                ),
                BooleanResultInfo::Pline1InsidePline2 | BooleanResultInfo::Overlapping => {}
                _ => out.push(region),
            }
        }
        self.regions = out;
    }

    /// The area of the section left (both sides of the axis).
    pub fn area(&self) -> f64 {
        2.0 * self.regions.iter().map(|r| r.area().abs()).sum::<f64>()
    }
}

/// A tool's cutting outline in mm, shared by all its moves.
type Cutter = std::rc::Rc<Vec<(f64, f64)>>;

/// The stock after any number of moves of a program, computed as the simulation asks for it.
pub struct Simulation {
    /// mm per program unit: the polygon work is done in mm, where cavalier's fixed tolerances (1e-5) fit
    mm: f64,
    /// the moves, in mm
    segs: Vec<Segment>,
    /// the cutter of each move (None: a rapid or a tool without a picture)
    cutters: Vec<Option<Cutter>>,
    /// the stock after 0, SNAPSHOT_EVERY, 2 x SNAPSHOT_EVERY ... moves, as far as computed
    snapshots: Vec<Stock>,
    /// the last stock asked for and its number of moves
    current: (usize, Stock),
}

impl Simulation {
    pub fn new(project: &Project, segs: &[Segment], tool_of_block: impl Fn(i64) -> Option<i64>,
               library: Option<&ToolLibrary>) -> Self {
        let mut by_tool = std::collections::HashMap::new();
        let cutters = segs
            .iter()
            .map(|seg| {
                if matches!(seg.kind, MoveKind::Rapid | MoveKind::Dwell) {
                    return None;
                }
                let number = tool_of_block(seg.block)? as i32;
                by_tool
                    .entry(number)
                    .or_insert_with(|| cutter(project, number, library).map(std::rc::Rc::new))
                    .clone()
            })
            .collect();
        let mm = if project.units == "inch" { 25.4 } else { 1.0 };
        let scale = |(x, z): (f64, f64)| (x * mm, z * mm);
        let segs = segs
            .iter()
            .map(|s| Segment { a: scale(s.a), b: scale(s.b), centre: s.centre.map(scale), ..*s })
            .collect();
        let mut start = Stock::new(&project.billet);
        start.regions.iter_mut().for_each(|r| r.scale_mut(mm));
        Simulation { mm, segs, cutters, snapshots: vec![start.clone()], current: (0, start) }
    }

    fn apply(&self, stock: &mut Stock, i: usize) {
        if let Some(tool) = &self.cutters[i] {
            for cut in swept(&self.segs[i], tool) {
                stock.cut(&cut);
            }
        }
    }

    /// The stock once the first `n` moves are done, in the program's units.
    pub fn at(&mut self, n: usize) -> Stock {
        let n = n.min(self.segs.len());
        if n != self.current.0 {
            let (mut done, mut stock) = if n > self.current.0 {
                (self.current.0, self.current.1.clone())
            } else {
                let k = (n / SNAPSHOT_EVERY).min(self.snapshots.len() - 1);
                (k * SNAPSHOT_EVERY, self.snapshots[k].clone())
            };
            while done < n {
                self.apply(&mut stock, done);
                done += 1;
                if done % SNAPSHOT_EVERY == 0 && done / SNAPSHOT_EVERY == self.snapshots.len() {
                    self.snapshots.push(stock.clone());
                }
            }
            self.current = (n, stock);
        }
        let mut stock = self.current.1.clone();
        if self.mm != 1.0 {
            stock.regions.iter_mut().for_each(|r| r.scale_mut(1.0 / self.mm));
        }
        stock
    }
}
