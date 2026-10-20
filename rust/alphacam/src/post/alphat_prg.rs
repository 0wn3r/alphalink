//! Alpha-T .PRG post (DESIGN.md section 5): project in, bytes out.
//!
//! Supported: tool blocks (with move/rapid and dwell appended), finish pass (external, internal, face) and area
//! clear (external and internal, with offsets, lines and arcs, pre-finish, follow, retract distance), face clear,
//! grooving (radial, internal, face), drilling (pecks, full retract, retract distance), threading (infeed angle,
//! spring passes), several operations and mid-program tool changes; programs in other units than the drawing
//! (Project::output_units). Everything is pinned by golden tests against the ALPHA-T samples in samples/alphat_v12.

use crate::error::{Error, Result, not_implemented, value};
use crate::i18n::tr;
use crate::model::{
    AreaClear, FinishPass, GrooveMode, MachineProfile, Operation, Point, Project, Thread, Tool,
    ToolChange, ToolLibrary, standard_library,
};
use crate::numfmt::format_number;
use crate::py::{round, round_int};
use crate::toolpath::area::{RETRACT_MM, area_clear, collision_angle, retract_mm};
use crate::toolpath::contour::{chain, front_first, limits, mm, points};
use crate::toolpath::drill::drill;
use crate::toolpath::face::{self, face_clear};
use crate::toolpath::finish::finish_pass;
use crate::toolpath::groove::groove;
use crate::toolpath::thread::plan_thread;
use crate::toolpath::{Move, MoveKind, Toolpath};

/// Area clear pre-finish block (#127=10): pull-off 1 mm instead of 0.2.
pub const PREFINISH_LEAD_OUT_MM: f64 = 1.0;

pub const HEADER_LOGIC: &[&str] = &[
    "#140=0",
    "IF[#1006EQ1]GOTO[[#129+1]*10]",
    "IF[#1005EQ1]GOTO[#129*10]",
    "#131=#131-1",
    "IF[#1009EQ1]GOTO[#129*10]",
    "#131=0",
];

fn units_mcode(units: &str) -> &'static str {
    if units == "inch" { "M70" } else { "M71" }
}

/// Formats the program's numbers. Toolpaths are computed in the drawing's units (rounded to `tdec`); a program in
/// other units (Project::output_units, ALPHA-T's "Select Units To Store Output In") converts each value at the end,
/// rounding the real value to the output decimals before the x2 scaling, as ALPHA-T does (UNITS_metricdwg_inchout:
/// Z-25 mm -> 0.9843 -> Z-1.9686; X96 -> radius 1.8898 -> X3.7796; feed 0.15 -> 0.0059 -> 0.0118).
struct Writer<'a> {
    dec: usize,
    tdec: usize,
    k: f64,
    zs: f64,
    fs: f64,
    lines: Vec<String>,
    /// restart points so far: IF[#140EQk] GOTO 50(k+1)
    restart: u32,
    /// inside an M62 feed group
    feeding: bool,
    /// last end point, for arc I/K
    pos: (f64, f64),
    library: Option<&'a ToolLibrary>,
}

impl<'a> Writer<'a> {
    fn new(project: &Project, profile: &MachineProfile, library: Option<&'a ToolLibrary>) -> Self {
        let out = project.program_units();
        Writer {
            dec: profile.decimals(out),
            tdec: profile.decimals(&project.units),
            k: if out == project.units { 1.0 } else if out == "inch" { 1.0 / 25.4 } else { 25.4 },
            zs: profile.z_scale,
            fs: profile.f_scale,
            lines: vec![],
            restart: 0,
            feeding: false,
            pos: (f64::NAN, f64::NAN),
            library,
        }
    }

    /// A real value in the output units, rounded to the output decimals before any scaling (FOL3: radius
    /// 34.69443 -> 34.694 -> X69.388; Z-10.53333 -> -10.533 -> Z-21.066).
    fn c(&self, v: f64) -> f64 {
        round(v * self.k, self.dec)
    }

    /// A diameter: its radius is rounded.
    fn x(&self, v: f64) -> String {
        format_number(2.0 * self.c(v / 2.0), self.dec as u32)
    }

    fn z(&self, v: f64) -> String {
        format_number(self.c(v) * self.zs, self.dec as u32)
    }

    fn f(&self, v: f64) -> String {
        format_number(self.c(v) * self.fs, self.dec as u32)
    }

    fn emit<S: Into<String>>(&mut self, lines: impl IntoIterator<Item = S>) {
        self.lines.extend(lines.into_iter().map(Into::into));
    }

    /// IF[#140EQk] GOTO <label>; returns the label to place where execution resumes.
    fn restart_jump(&mut self) -> u32 {
        self.restart += 1;
        let label = 501 + self.restart;
        self.emit([format!("IF[#140EQ{}] GOTO {label}", self.restart)]);
        label
    }

    fn feed_group(&mut self) {
        if !self.feeding {
            self.emit(["M62"]);
            self.feeding = true;
        }
    }

    fn moves(&mut self, moves: &[Move]) -> Result<()> {
        for m in moves {
            let mut start = self.pos;
            if m.kind != MoveKind::Dwell && m.x.is_some() {
                start = std::mem::replace(&mut self.pos, m.xz());
            }
            let (x, z) = m.xz();
            match m.kind {
                MoveKind::Rapid => {
                    let line = format!("G0X{}Z{}", self.x(x), self.z(z));
                    self.emit(["M60".to_string(), line, "M61".to_string()]);
                    self.feeding = false;
                }
                MoveKind::Feed => {
                    self.feed_group();
                    let line = format!("G1X{}Z{}F{}", self.x(x), self.z(z), self.f(m.feed.unwrap_or_default()));
                    self.emit([line]);
                }
                MoveKind::ArcCw | MoveKind::ArcCcw => {
                    self.feed_group();
                    let g = if m.kind == MoveKind::ArcCw { 2 } else { 3 };
                    let i = self.x(m.centre_x.unwrap_or_default() - start.0);
                    let k = self.z(m.centre_z.unwrap_or_default() - start.1);
                    let line = format!("G{g}X{}Z{}I{i}K{k}F{}", self.x(x), self.z(z), self.f(m.feed.unwrap_or_default()));
                    self.emit([line]);
                }
                MoveKind::Dwell => {
                    let p = round_int(m.dwell_revs.unwrap_or_default() * 1000.0);
                    self.emit([format!("G4P{p}")]);
                }
                MoveKind::Thread => {
                    return not_implemented(tr("{value} moves are not supported by the post yet",
                                              &[("value", &m.kind.as_str())]));
                }
            }
        }
        Ok(())
    }
}

/// The part's own tools first, then the user's library, then ALPHA-T's standard ATOOLIB.
pub fn tool<'p>(project: &'p Project, number: i32, library: Option<&'p ToolLibrary>) -> Result<&'p Tool> {
    project
        .tools
        .iter()
        .find(|t| t.number == number)
        .or_else(|| library.and_then(|l| l.get(number)))
        .or_else(|| standard_library().get(number))
        .ok_or_else(|| Error::Value(tr("no tool {number}", &[("number", &number)])))
}

/// The header values of a cutting operation.
#[derive(Clone, Copy)]
struct Head {
    tool: i32,
    start: Point,
    feed: f64,
    type_name: &'static str,
}

fn tool_block(w: &mut Writer, block: u32, tc: &ToolChange, moves: &[Move]) -> Result<()> {
    let lines = [format!("N{}", block * 10), "#121=1".into(), format!("#122={}", tc.tool),
                 format!("#123={}", w.x(tc.change_point.x)), format!("#124={}", w.z(tc.change_point.z)),
                 "#127=1".into(), format!("#129={block}"), format!("#130={}", w.f(tc.feed)), "M66".into()];
    w.emit(lines);
    let label = w.restart_jump();
    w.emit(["M63"]);
    w.feeding = false;
    w.moves(moves)?;
    w.emit([format!("N{label}")]);
    Ok(())
}

fn header(w: &mut Writer, block: u32, op: Head, passes: usize, kind: i32, limit: (f64, f64), feed: &str) {
    let lines = [format!("N{}", block * 10), format!("#120={passes}"), "#121=1".into(), format!("#122={}", op.tool),
                 format!("#123={}", w.x(op.start.x)), format!("#124={}", w.z(op.start.z)),
                 format!("#125={}", w.x(limit.0)), format!("#126={}", w.z(limit.1)), format!("#127={kind}"),
                 "#128=1".into(), format!("#129={block}"), format!("#130={feed}"), "#131=#131+1".into()];
    w.emit(lines);
}

fn generic_block(w: &mut Writer, block: u32, op: Head, tp: &Toolpath, kind: i32, limit: (f64, f64)) -> Result<()> {
    if tp.passes.is_empty() {
        let name = tr(&op.type_name.replace('_', " "), &[]);
        return value(tr("{op}: no cuts with this contour (nothing between the tool start and the profile)",
                        &[("op", &name)]));
    }
    // an empty pass (a pocket's floor the rear-angle line already reached, RADG) has its block but is not counted
    let feed = w.f(op.feed);
    header(w, block, op, tp.passes.iter().filter(|p| !p.moves.is_empty()).count(), kind, limit, &feed);
    let mut label = 0;
    for (n, p) in tp.passes.iter().enumerate() {
        if n > 0 {
            w.emit([format!("N{label}"), format!("#121={}", n + 1)]);
        }
        w.emit(["M66"]);
        label = w.restart_jump();
        w.emit(["M63"]);
        w.feeding = false;
        w.moves(&p.moves)?;
    }
    w.moves(&tp.retract)?;
    w.emit(["M64".to_string(), format!("N{label}")]);
    Ok(())
}

/// THREAD sample layout: one restart point for the whole op; per cut M60 G0 to (X, start Z) M61, M62, #121=n, M66,
/// G32 to one pitch before the end, U/W pull-out, G0 out to the start X (no M60/M61), M60 G0 back to start Z M61.
/// Start Z = thread top + lead-in. A spring pass (THREAD_spring1) is its own block #127=21 right after, with one
/// cut at the final diameter; the thread block then ends without M64. Returns the last block number used.
fn thread_block(w: &mut Writer, mut block: u32, project: &Project, op: &Thread) -> Result<u32> {
    let outer = project.contour(std::slice::from_ref(&op.outer_line))?[0];
    let inner = project.contour(std::slice::from_ref(&op.inner_line))?[0];
    let plan = plan_thread(op, outer, inner, w.tdec)?;
    let pitch = w.f(op.pitch);
    let (sx, sz) = (op.start.x, plan.z_start);
    let last_x = plan.xs[plan.xs.len() - 1];
    let cut = |w: &mut Writer, n: usize, x: f64| -> Result<()> {
        w.moves(&[Move::rapid(x, plan.cut_start_z(if x == last_x { plan.xs.len() } else { n }))])?;
        let lines = ["M62".to_string(), format!("#121={n}"), "M66".into(),
                     format!("G32X{}Z{}F{pitch}", w.x(x), w.z(plan.z_end + op.pitch)),
                     format!("U{}W{}", w.x(plan.pullout_u), w.z(-op.pitch)),
                     format!("G0X{}Z{}", w.x(sx), w.z(plan.z_end))];
        w.emit(lines);
        w.feeding = false;
        w.moves(&[Move::rapid(sx, sz)])
    };
    let head = Head { tool: op.tool, start: op.start, feed: op.pitch, type_name: "thread" };
    header(w, block, head, plan.xs.len(), 20, (plan.inner, plan.z_end), &pitch);
    w.emit(["M66"]);
    let label = w.restart_jump();
    w.emit(["M63"]);
    w.feeding = false;
    w.moves(&[Move::rapid(sx, sz)])?;
    for (n, &x) in plan.xs.iter().enumerate() {
        cut(w, n + 1, x)?;
    }
    if op.spring_passes <= 0 {
        w.emit(["M64".to_string(), format!("N{label}")]);
        return Ok(block);
    }
    w.emit([format!("N{label}")]);
    block += 1;
    header(w, block, head, op.spring_passes as usize, 21, (plan.inner, plan.z_end), &pitch);
    w.emit(["M66"]);
    let label = w.restart_jump();
    w.emit(["M63"]);
    w.feeding = false;
    for n in 1..=op.spring_passes as usize {
        cut(w, n, last_x)?;
    }
    w.emit(["M64".to_string(), format!("N{label}")]);
    Ok(block)
}

fn finish_head(fp: &FinishPass) -> Head {
    Head { tool: fp.tool, start: fp.start, feed: fp.feed, type_name: "finish" }
}

/// Emit the block(s) for one operation, each followed by its empty block (left open: the caller closes the
/// last one). Returns the last block number used.
fn op_blocks(w: &mut Writer, mut block: u32, project: &Project, op: &Operation) -> Result<u32> {
    let units = project.units.as_str();
    let tool = tool(project, op.tool().unwrap_or_default(), w.library)?;
    let r_exact = mm(tool.nose_radius, units);
    // the toolpaths use the nose radius rounded to the output decimals (inch: 0.4 mm -> 0.0157, ARC_finish_inch),
    // the #125/#126 limits the exact one (-0.063 = -4 x 0.015748)
    let r = round(r_exact, w.tdec);
    let tdec = w.tdec;
    let head = Head {
        tool: op.tool().unwrap_or_default(),
        start: op.start().unwrap_or_default(),
        feed: op.feed().unwrap_or_default(),
        type_name: op.type_name(),
    };
    match op {
        Operation::Thread(t) => return thread_block(w, block, project, t),
        Operation::Drill(d) => {
            generic_block(w, block, head, &drill(d, units, tdec)?, 18, (d.start.x, d.depth))?;
            return Ok(block);
        }
        _ => {}
    }
    let mut path = chain(&project.contour(op.contour().unwrap_or_default())?)?;
    if matches!(op, Operation::AreaClear(_) | Operation::FaceClear(_)) {
        path = front_first(path);
    }
    let limit = limits(&points(&path), r_exact);
    match op {
        Operation::FinishPass(fp) => {
            let angle = collision_angle(Some(tool), fp.external, true);
            let tp = finish_pass(fp, &path, r, units, tdec, None, false, angle)?;
            generic_block(w, block, head, &tp, if fp.face { 16 } else if fp.external { 14 } else { 15 }, limit)?;
        }
        Operation::AreaClear(ac) if !ac.front => {
            // FACE direction: face-clear passes; #127=8 external (AREA_front_face), 7 internal (AREA_face_internal)
            if ac.pre_finish {
                return not_implemented(tr("area clear FACE with pre-finish (no sample)", &[]));
            }
            let tp = face_clear(ac.into(), &path, r, units, tdec, retract_mm(ac.retract, units, RETRACT_MM), true)?;
            generic_block(w, block, head, &tp, if ac.external { 8 } else { 7 }, limit)?;
        }
        Operation::AreaClear(ac) => {
            let angle = collision_angle(Some(tool), ac.external, ac.towards);
            let tp = area_clear(ac, &path, r, units, tdec, angle)?;
            generic_block(w, block, head, &tp, if ac.external { 6 } else { 7 }, limit)?;
            if ac.pre_finish {
                // its own block after an empty one: #127=10, internal 11 (AREA_prefinish_internal), with the
                // area's offsets (AREA_prefinish_offset) and a 1 mm pull-off
                empty_block(w, block + 1, None, &[])?;
                block += 2;
                let fp = prefinish(ac);
                let lead_out = round(mm(retract_mm(ac.retract, units, PREFINISH_LEAD_OUT_MM), units), tdec);
                let tp = finish_pass(&fp, &path, r, units, tdec, Some(lead_out), false, angle)?;
                generic_block(w, block, finish_head(&fp), &tp, if ac.external { 10 } else { 11 }, limit)?;
            }
        }
        Operation::FaceClear(fc) => {
            let tp = face_clear(fc.into(), &path, r, units, tdec, face::RETRACT_MM, false)?;
            generic_block(w, block, head, &tp, 8, limit)?;
            if fc.pre_finish {
                // FACE_prefinish: #127=12, ends on the offset end, 0.2 mm pull-off inwards
                empty_block(w, block + 1, None, &[])?;
                block += 2;
                let fp = FinishPass { tool: fc.tool, start: fc.start, contour: fc.contour.clone(), feed: fc.feed,
                                      offset_x: fc.offset_x, offset_z: fc.offset_z, ..FinishPass::default() };
                let tp = finish_pass(&fp, &path, r, units, tdec, None, true, None)?;
                generic_block(w, block, finish_head(&fp), &tp, 12, limit)?;
            }
        }
        Operation::Groove(g) => {
            let tp = groove(g, &path, units, tdec)?;
            generic_block(w, block, head, &tp, if g.mode == GrooveMode::Finish { 17 } else { 9 }, limit)?;
        }
        _ => {
            return not_implemented(tr("{type} is not supported by the post yet", &[("type", &op.type_name())]));
        }
    }
    Ok(block)
}

fn prefinish(ac: &AreaClear) -> FinishPass {
    FinishPass { tool: ac.tool, start: ac.start, contour: ac.contour.clone(), feed: ac.feed, offset_x: ac.offset_x,
                 offset_z: ac.offset_z, external: ac.external, ..FinishPass::default() }
}

/// Moves and dwells that follow an operation or a tool change.
fn moves_for(ops: &[&Operation], feed: f64) -> Vec<Move> {
    ops.iter()
        .filter_map(|o| match o {
            Operation::Dwell(d) => Some(Move::dwell(None, d.revs)),
            Operation::MoveTo(m) if m.rapid => Some(Move::rapid(m.point.x, m.point.z)),
            Operation::MoveTo(m) => Some(Move::feed(m.point.x, m.point.z, feed)),
            _ => None,
        })
        .collect()
}

/// The empty block after an operation: moves/dwells that follow the operation, then the rapid to the tool
/// change point when a tool change follows.
fn empty_block(w: &mut Writer, block: u32, next_tool: Option<&ToolChange>, moves: &[Move]) -> Result<()> {
    w.emit([format!("N{}", block * 10), "M66".into()]);
    let label = w.restart_jump();
    w.feeding = false;
    w.moves(moves)?;
    if let Some(tc) = next_tool {
        w.feeding = false;
        w.moves(&[Move::rapid(tc.change_point.x, tc.change_point.z)])?;
    }
    w.emit([format!("N{label}")]);
    Ok(())
}

fn is_move(op: &Operation) -> bool {
    matches!(op, Operation::MoveTo(_) | Operation::Dwell(_))
}

/// Layout (samples MET2, AREA1, TWOOP, TOOLCHG, MOVE_RAPID):
/// header; tool block (N10) with any move/rapid/dwell that follows the tool change; then per operation an op block
/// and an empty block (which holds the rapid to the tool change point when a tool change comes next); a later tool
/// change is another tool block; finally an M65 block and %.
pub fn post(project: &Project, profile: Option<&MachineProfile>, library: Option<&ToolLibrary>) -> Result<Vec<u8>> {
    let default_profile = MachineProfile::default();
    let profile = profile.unwrap_or(&default_profile);
    let mut w = Writer::new(project, profile, library);
    w.emit([
        "%".to_string(),
        format!("O{}", profile.o_number),
        format!("#132={}", project.program_number),
    ]);
    w.emit(HEADER_LOGIC.iter().copied());
    w.emit([units_mcode(project.program_units())]);

    let first;
    let mut ops: Vec<&Operation> = project.operations.iter().collect();
    if !matches!(ops.first(), Some(Operation::ToolChange(_))) {
        let feed = tool(project, project.first_tool, library)?.default_feed;
        first = Operation::ToolChange(ToolChange { tool: project.first_tool, change_point: project.tool_change,
                                                   feed });
        ops.insert(0, &first);
    }
    let (mut block, mut i, mut tc_feed) = (0u32, 0usize, 0.15);
    while i < ops.len() {
        let op = ops[i];
        i += 1;
        block += 1;
        if let Operation::ToolChange(tc) = op {
            let start = i;
            while i < ops.len() && is_move(ops[i]) {
                i += 1;
            }
            tool_block(&mut w, block, tc, &moves_for(&ops[start..i], tc.feed))?;
            tc_feed = tc.feed;
            continue;
        }
        if is_move(op) {
            return not_implemented(tr("move/dwell before the first tool change is not supported", &[]));
        }
        block = op_blocks(&mut w, block, project, op)? + 1;
        // moves and dwells after an operation go into its empty block (FINISH_then_move_rapid_dwell)
        let start = i;
        while i < ops.len() && is_move(ops[i]) {
            i += 1;
        }
        let next = match ops.get(i) {
            Some(Operation::ToolChange(tc)) => Some(tc),
            _ => None,
        };
        empty_block(&mut w, block, next, &moves_for(&ops[start..i], tc_feed))?;
    }
    block += 1;
    w.emit([format!("N{}", block * 10), "M65".into(), "%".into()]);
    if let Some(label) = repeated_label(&w.lines) {
        // ALPHA-T numbers the operation blocks N10, N20... and the restart points N502, N503...: a long program
        // repeats a label, and the control's restart (GOTO[#129*10] searches forward from the top) would start
        // inside an earlier operation
        return value(tr(
            "the program is too long for ALPHA-T's label numbers: N{label} would be used twice, so a restart could \
             start in the wrong place; split the part into two programs",
            &[("label", &label)],
        ));
    }
    let mut out = String::new();
    for line in &w.lines {
        out.push_str(line);
        out.push_str("\r\n");
    }
    Ok(out.into_bytes())
}

/// The first label (`N<number>` on a line of its own) that the program uses twice.
fn repeated_label(lines: &[String]) -> Option<u32> {
    let mut seen = std::collections::HashSet::new();
    lines
        .iter()
        .filter_map(|l| l.strip_prefix('N').and_then(|n| n.parse::<u32>().ok()))
        .find(|&n| !seen.insert(n))
}
