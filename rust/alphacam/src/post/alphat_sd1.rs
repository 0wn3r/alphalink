//! Alpha-T .SD1 listing (what ALPHA-T writes next to the .PRG): project in, bytes out.
//!
//! The SD1 is ALPHA-T's own canned-cycle listing of the program, in real (unscaled) units: N numbers step by 5, M30
//! ends it without one, CRLF, numbers with up to 4 decimals and no trailing point (X120, Z0.4). Within a run of motion
//! lines a coordinate equal to the previous one is left out; the first motion line after any other line has both.
//!
//! ```text
//! N10 F<feed> T<tool> G95G90M8M3G96G30L0      first tool;  N15 G92 X163.3803 Z120;  N20 G5 / N25 G6 tool change pt
//! G0 X Z, G99 F T M8M3G96                      later tool change (rapid to the change point first)
//! G5 X Z                                       operation start
//! G115 X Z                                     limits (= PRG #125/#126)
//! G70 R<1 = area pre-finish> E<0 ext, 1 int, 2 face> U W                finish pass
//! G71 P Q U W D R A45 E<100, -100 int, 401 face, 101 area FACE; +200 FOLLOW> H<passes>   area / face clear
//!     P = the G21 line, Q = the G0 back to the start; R = retract (area RETRACT DIST, default 1; face 0.2)
//! G21 E<1, -1> Q<2, 1> C0, G0 approach, G1/G2/G3 nose-centre path, G0/G1 pull-off, G20, G0 start, G170/G171
//! G75 P Q U W D I R E L H C A, G0/G1 drawn profile, G175                grooving (P/Q = first/last profile line)
//! G78, the finish path, G178                                           grooving finish mode
//! G117 X Z(top + lead in), G76 X Z Q D I L B0 A<angle> E K<spring passes> F, G176 F   threading
//! G74, the drill moves, G174                                           drilling
//! G4 P<revs>, G0/G1                                                    dwell, moves
//! ```
//!
//! Pinned against every metric sample in samples/alphat_v12 (tests/test_sd1_write.py).

use crate::error::Result;
use crate::model::{
    AreaClear, Datum, FinishPass, GrooveMode, Operation, Point, Project, Tool, ToolChange, ToolLibrary,
};
use crate::numfmt::format_number;
use crate::post::alphat_prg::{PREFINISH_LEAD_OUT_MM, tool as find_tool};
use crate::py::{hypot, round};
use crate::toolpath::area::{self, area_clear, collision_angle, retract_mm};
use crate::toolpath::contour::{Seg, centre_path, chain, cos45, front_first, limit_dives, limits, mm, points, translate};
use crate::toolpath::drill::drill;
use crate::toolpath::face::{self, face_clear};
use crate::toolpath::groove::{groove, groove_finish};
use crate::toolpath::thread::plan_thread;
use crate::toolpath::{Move, MoveKind};

pub const DEC: usize = 4;
/// mm; constant in every sample (inch: X6.4323 Z4.7244)
pub const G92: (f64, f64) = (163.3803, 120.0);
/// G0 to the first point from 2 mm away at 45 degrees
pub const APPROACH_MM: f64 = 2.0;
pub const FINISH_LEAD_OUT_MM: f64 = 0.2;
pub const FACE_RETRACT_MM: f64 = 0.2;

fn n(v: f64) -> String {
    let s = format_number(v, DEC as u32);
    match s.strip_suffix('.') {
        Some(t) => t.to_string(),
        None => s,
    }
}

struct Listing {
    /// None: a cycle line filled in once its P/Q are known
    lines: Vec<Option<String>>,
    pos: (Option<f64>, Option<f64>),
}

impl Listing {
    fn next_n(&self) -> usize {
        5 * (self.lines.len() + 2)
    }

    /// A non-motion line; returns its N.
    fn put(&mut self, text: &str) -> usize {
        let n = self.next_n();
        self.lines.push(Some(format!("N{n}{text}")));
        self.pos = (None, None);
        n
    }

    fn mv(&mut self, g: u32, x: f64, z: f64, extra: &str) -> usize {
        let (x, z) = (round(x, DEC), round(z, DEC));
        let mut words = String::new();
        if self.pos.0 != Some(x) || !extra.is_empty() {
            words += &format!("X{}", n(x));
        }
        if self.pos.1 != Some(z) || !extra.is_empty() {
            words += &format!("Z{}", n(z));
        }
        let num = self.next_n();
        self.lines.push(Some(format!("N{num}G{g}{words}{extra}")));
        self.pos = (Some(x), Some(z));
        num
    }

    fn path(&mut self, segs: &[Seg]) -> usize {
        let mut num = 0;
        for s in segs {
            num = match s.centre {
                Some(c) => {
                    let (i, k) = ((c.x - s.a.x) / 2.0, c.z - s.a.z);
                    self.mv(if s.ccw { 3 } else { 2 }, s.b.x, s.b.z, &format!("I{}K{}", n(i), n(k)))
                }
                None => self.mv(1, s.b.x, s.b.z, ""),
            };
        }
        num
    }

    /// Reserve the line of a cycle whose text needs later line numbers.
    fn reserve(&mut self) -> usize {
        self.lines.push(None);
        self.lines.len() - 1
    }

    fn fill(&mut self, index: usize, text: String) {
        self.lines[index] = Some(format!("N{}{text}", 5 * (index + 2)));
    }
}

fn leg(units: &str, dist_mm: f64) -> f64 {
    mm(dist_mm, units) * cos45()
}

/// What `_contour_cycle` needs to know about its operation.
struct Cycle<'a> {
    start: Point,
    /// a finish pass lists its path moved by its offsets
    offsets: Option<(f64, f64)>,
    external: bool,
    cycle_line: &'a dyn Fn(usize, usize) -> String,
    lead_out_mm: f64,
    lead_out_rapid: bool,
    extend_end: bool,
    inward: bool,
    end_g: u32,
    face_mode: bool,
    angle: Option<f64>,
}

/// G70/G71 body. `cycle_line(p, q)` gives the cycle's own line text (needs the P/Q line numbers).
/// angle (area clear, collision_angle): a wall dropping into a pocket steeper than this is listed at this angle,
/// from its top to its floor (DIVE2/DIVE3: N70 X30.8 Z-28.5922 for the drawn Z-22.27 with T3's 31 degrees).
/// face_mode (face clear passes: E401 / E101 / E-101): the first point is carried r back along its element, not
/// below the axis (AREA_face_internal: X49.2 Z0.4; FACE_*: X0 Z0.4), and internal work lists G21 Q3.
fn contour_cycle(out: &mut Listing, path: &[Seg], r: f64, units: &str, c: Cycle) -> Result<()> {
    let side: f64 = if c.external { 1.0 } else { -1.0 };
    let mut cp = centre_path(path, r, c.external, c.extend_end)?;
    if c.face_mode && r != 0.0 && !cp[0].is_arc() {
        let s = cp[0];
        let (dr, dz) = ((s.b.x - s.a.x) / 2.0, s.b.z - s.a.z);
        let len = hypot(dr, dz);
        cp[0] = Seg::new(Point::new((s.a.x - 2.0 * r * dr / len).max(0.0), s.a.z - r * dz / len), s.b, s.centre, s.ccw);
    }
    if let Some((ox, oz)) = c.offsets {
        // the finish pass lists its path moved by the offsets, the first point only across its element
        // (OFFSET_finish, INT_finish_offset); area and face clear list the path as drawn
        cp = translate(cp, ox, oz, c.external);
    }
    let cp = limit_dives(cp, c.angle, side);
    let (first, last) = (cp[0].a, cp[cp.len() - 1].b);
    let approach = leg(units, APPROACH_MM);
    let out_leg = leg(units, c.lead_out_mm);
    let index = out.reserve();
    let p = out.put(&format!("G21E{}Q{}C0", side as i32, if c.external { 2 } else if c.face_mode { 3 } else { 1 }));
    out.mv(0, first.x + side * 2.0 * approach, first.z + approach, "");
    out.mv(1, first.x, first.z, "");
    out.path(&cp);
    let dx = (if c.inward { -1.0 } else { 1.0 }) * side * 2.0 * out_leg;
    out.mv(if c.lead_out_rapid { 0 } else { 1 }, last.x + dx, last.z + out_leg, "");
    out.put("G20");
    let q = out.mv(0, c.start.x, c.start.z, "");
    out.fill(index, (c.cycle_line)(p, q));
    out.put(&format!("G{}", c.end_g));
    Ok(())
}

pub fn sd1(project: &Project, library: Option<&ToolLibrary>) -> Result<Vec<u8>> {
    // ALPHA-T keeps the .SD1 in the drawing's units even when the program is written in others
    // (UNITS_metricdwg_inchout: G30, F0.15, mm values next to an inch .PRG)
    let units = project.units.as_str();
    let mut out = Listing { lines: vec![], pos: (None, None) };
    let first_tc;
    let mut ops: Vec<&Operation> = project.operations.iter().collect();
    if !matches!(ops.first(), Some(Operation::ToolChange(_))) {
        let feed = find_tool(project, project.first_tool, library)?.default_feed;
        first_tc = Operation::ToolChange(ToolChange { tool: project.first_tool, change_point: project.tool_change,
                                                      feed });
        ops.insert(0, &first_tc);
    }
    let mut first = true;
    let mut tc_feed = ops[0].feed().unwrap_or_default();
    for op in ops {
        match op {
            Operation::ToolChange(tc) => {
                tc_feed = tc.feed;
                let cp = tc.change_point;
                if first {
                    out.put(&format!("F{}T{}G95G90M8M3G96G{}L0", n(tc.feed), tc.tool,
                                     if units == "mm" { 30 } else { 31 }));
                    out.put(&format!("G92X{}Z{}", n(mm(G92.0, units)), n(mm(G92.1, units))));
                    out.put(&format!("G5X{}Z{}", n(cp.x), n(cp.z)));
                    out.put(&format!("G6X{}Z{}", n(cp.x), n(cp.z)));
                    first = false;
                } else {
                    out.mv(0, cp.x, cp.z, "");
                    out.put(&format!("G99F{}T{}M8M3G96", n(tc.feed), tc.tool));
                }
            }
            Operation::Dwell(d) => {
                out.put(&format!("G4P{}", n(d.revs)));
            }
            Operation::MoveTo(m) => {
                out.mv(if m.rapid { 0 } else { 1 }, m.point.x, m.point.z, "");
            }
            _ => op_lines(&mut out, project, op, library, tc_feed)?,
        }
    }
    let mut text = String::new();
    for line in out.lines.iter().flatten().map(String::as_str).chain(["M30"]) {
        text.push_str(line);
        text.push_str("\r\n");
    }
    Ok(text.into_bytes())
}

fn op_lines(out: &mut Listing, project: &Project, op: &Operation, library: Option<&ToolLibrary>, tc_feed: f64)
            -> Result<()> {
    let units = project.units.as_str();
    let tool: &Tool = find_tool(project, op.tool().unwrap_or_default(), library)?;
    let r_exact = mm(tool.nose_radius, units);
    // the SD1 uses the exact nose radius (MET2_finish_inch: X1.6315, the PRG's rounded r gives 1.6314)
    let r = r_exact;
    let start = op.start().unwrap_or_default();
    out.put(&format!("G5X{}Z{}", n(start.x), n(start.z)));
    match op {
        Operation::Thread(t) => {
            let outer = project.contour(std::slice::from_ref(&t.outer_line))?[0];
            let inner = project.contour(std::slice::from_ref(&t.inner_line))?[0];
            let plan = plan_thread(t, outer, inner, DEC)?;
            out.put(&format!("G115X{}Z{}", n(plan.inner), n(plan.z_end)));
            out.put(&format!("G117X{}Z{}", n(t.start.x), n(plan.z_start)));
            // Q: the thread depth on radius (THREAD_depth6: Q6)
            let depth = (outer.a().x - inner.a().x).abs() / 2.0;
            out.put(&format!("G76X{}Z{}Q{}D{}I{}L{}B0A{}E{}K{}F{}", n(plan.inner), n(plan.z_end + t.lead_out),
                             n(depth), n(plan.first_cut), n(t.lead_in), n(t.lead_out), n(t.angle),
                             if t.external { 1 } else { -1 }, t.spring_passes, n(t.pitch)));
            out.put(&format!("G176F{}", n(tc_feed)));
            return Ok(());
        }
        Operation::Drill(d) => {
            out.put(&format!("G115X{}Z{}", n(d.start.x), n(d.depth)));
            out.put("G74");
            moves(out, &drill(d, units, DEC)?.moves());
            out.put("G174");
            return Ok(());
        }
        _ => {}
    }
    let mut path = chain(&project.contour(op.contour().unwrap_or_default())?)?;
    if matches!(op, Operation::AreaClear(_) | Operation::FaceClear(_)) {
        path = front_first(path);
    }
    let (lx, lz) = limits(&points(&path), r_exact);
    out.put(&format!("G115X{}Z{}", n(lx), n(lz)));
    match op {
        Operation::FinishPass(fp) => {
            let line = |_: usize, _: usize| {
                format!("G70R0E{}U{}W{}", if fp.face { 2 } else if fp.external { 0 } else { 1 }, n(fp.offset_x),
                        n(fp.offset_z))
            };
            contour_cycle(out, &path, r, units, Cycle {
                start: fp.start,
                offsets: Some((fp.offset_x, fp.offset_z)),
                external: fp.external,
                cycle_line: &line,
                lead_out_mm: FINISH_LEAD_OUT_MM,
                lead_out_rapid: false,
                extend_end: true,
                inward: fp.face,
                end_g: 170,
                face_mode: false,
                angle: if fp.face { None } else { collision_angle(Some(tool), fp.external, true) },
            })?;
        }
        Operation::AreaClear(_) | Operation::FaceClear(_) => clear_lines(out, project, op, tool, &path, r, (lx, lz))?,
        Operation::Groove(g) if g.mode == GrooveMode::Finish => {
            out.put("G78");
            moves(out, &groove_finish(g, &path).moves());
            out.put("G178");
        }
        Operation::Groove(g) => {
            let tp = groove(g, &path, units, DEC)?;
            let index = out.reserve();
            let p = out.mv(0, path[0].a.x, path[0].a.z, "");
            let q = out.path(&path);
            let flags = format!("{}{}{}{}", if g.external { "" } else { "-" }, if g.front { 1 } else { 2 },
                                if g.datum == Datum::Left { 1 } else { 0 }, if g.full_retract { 1 } else { 0 });
            out.fill(index, format!("G75P{p}Q{q}U{}W{}D{}I{}R{}E{}L{flags}H{}C{}A{}", n(g.finish_offset_x),
                                    n(g.finish_offset_z), n(g.peck_depth), n(g.stepover), n(g.z_shift),
                                    n(g.tip_width), tp.plunges, g.repeat + 1, n(g.dwell_revs)));
            out.put("G175");
        }
        _ => unreachable!("tool changes, moves and dwells are listed by sd1()"),
    }
    Ok(())
}

/// Area clear (FRONT or FACE) and face clear: the G71 cycle, and the G70 R1 pre-finish when asked for.
fn clear_lines(out: &mut Listing, project: &Project, op: &Operation, tool: &Tool, path: &[Seg], r: f64,
               limit: (f64, f64)) -> Result<()> {
    let units = project.units.as_str();
    let (ac, fc) = match op {
        Operation::AreaClear(ac) => (Some(ac), None),
        Operation::FaceClear(fc) => (None, Some(fc)),
        _ => unreachable!(),
    };
    let face = fc.is_some();
    let external = ac.is_none_or(|a| a.external);
    let (tool_n, start, contour, feed, offset_x, offset_z, depth, pre_finish) = match (ac, fc) {
        (Some(a), _) => (a.tool, a.start, &a.contour, a.feed, a.offset_x, a.offset_z, a.depth_of_cut, a.pre_finish),
        (_, Some(f)) => (f.tool, f.start, &f.contour, f.feed, f.offset_x, f.offset_z, f.depth_of_cut, f.pre_finish),
        _ => unreachable!(),
    };
    let retract_of = |default: f64| retract_mm(ac.and_then(|a| a.retract), units, default);
    let (e, tp) = match (ac, fc) {
        (_, Some(f)) => (401, face_clear(f.into(), path, r, units, DEC, face::RETRACT_MM, false)?),
        (Some(a), _) if !a.front => {
            (if external { 101 } else { -101 },
             face_clear(a.into(), path, r, units, DEC, retract_of(area::RETRACT_MM), true)?)
        }
        (Some(a), _) => {
            (if external { 100 } else { -100 },
             area_clear(a, path, r, units, DEC, collision_angle(Some(tool), a.external, a.towards))?)
        }
        _ => unreachable!(),
    };
    let area_angle = |a: &AreaClear| collision_angle(Some(tool), a.external, a.towards);
    // mm, for the pull-off legs
    let retract = if face { FACE_RETRACT_MM } else { retract_of(area::RETRACT_MM) };
    // R as entered, in the drawing's units (UNITS_inchdwg_metricout: R0.0079)
    let r_shown = if units == "mm" {
        retract
    } else {
        ac.and_then(|a| a.retract).unwrap_or_else(|| round(retract / 25.4, 4))
    };
    let follow = ac.is_some_and(|a| a.follow);
    let e_shown = if follow { e + if e > 0 { 200 } else { -200 } } else { e };
    let passes = tp.passes.iter().filter(|p| !p.moves.is_empty()).count();
    let line = |p: usize, q: usize| {
        format!("G71P{p}Q{q}U{}W{}D{}R{}A45E{e_shown}H{passes}", n(offset_x), n(offset_z), n(depth), n(r_shown))
    };
    let normal = e == 100 || e == -100;
    contour_cycle(out, path, r, units, Cycle {
        start,
        offsets: None,
        external,
        cycle_line: &line,
        lead_out_mm: retract,
        lead_out_rapid: true,
        extend_end: normal,
        inward: face,
        end_g: 171,
        face_mode: !normal,
        angle: if normal { ac.and_then(area_angle) } else { None },
    })?;
    if pre_finish {
        // G70 R1, E0 / E1 internal / E2 face, with the op's offsets
        out.put(&format!("G115X{}Z{}", n(limit.0), n(limit.1)));
        let fp = FinishPass { tool: tool_n, start, contour: contour.clone(), feed, offset_x, offset_z, external,
                              ..FinishPass::default() };
        let pe = if face { 2 } else if external { 0 } else { 1 };
        let line = |_: usize, _: usize| format!("G70R1E{pe}U{}W{}", n(offset_x), n(offset_z));
        contour_cycle(out, path, r, units, Cycle {
            start: fp.start,
            offsets: Some((fp.offset_x, fp.offset_z)),
            external,
            cycle_line: &line,
            lead_out_mm: if face { FINISH_LEAD_OUT_MM } else { retract_of(PREFINISH_LEAD_OUT_MM) },
            lead_out_rapid: false,
            extend_end: !face,
            inward: face,
            end_g: 170,
            face_mode: false,
            angle: if face { None } else { ac.and_then(area_angle) },
        })?;
    }
    Ok(())
}

fn moves(out: &mut Listing, moves: &[Move]) {
    for m in moves {
        let (x, z) = m.xz();
        match m.kind {
            MoveKind::Dwell => {
                out.put(&format!("G4P{}", n(m.dwell_revs.unwrap_or_default())));
            }
            MoveKind::ArcCw | MoveKind::ArcCcw => {
                // I/K from where the arc starts (GRF: G2X30Z-24.5I0K-1.5)
                let (sx, sz) = (out.pos.0.unwrap_or(f64::NAN), out.pos.1.unwrap_or(f64::NAN));
                let extra = format!("I{}K{}", n((m.centre_x.unwrap_or_default() - sx) / 2.0),
                                    n(m.centre_z.unwrap_or_default() - sz));
                out.mv(if m.kind == MoveKind::ArcCw { 2 } else { 3 }, x, z, &extra);
            }
            kind => {
                out.mv(if kind == MoveKind::Rapid { 0 } else { 1 }, x, z, "");
            }
        }
    }
}
