//! Rebuild a project's operations from ALPHA-T's .SD1 listing.
//!
//! ALPHA-T keeps no operations in the drawing: the .SDG holds geometry and billet, and the .SDT is the same for every
//! operation. The operations survive only in the programs it writes. The .SD1 (an ISO-style listing written next to
//! the .PRG) names every operation with its parameters as canned cycles:
//!
//!   N10 F<feed> T<tool> ... G30 (mm) / G31 (inch)    first tool and its feed
//!   N20 G5 X Z / N25 G6 X Z                          tool change point
//!   G4 P<revs>                                       dwell (in the tool block)
//!   G1 / G0 ...                                      move / rapid to a point (in the tool block)
//!   G5 X Z                                           tool start of the next operation
//!   G115 X Z                                         operation limits (drilling: the hole bottom)
//!   G70 R E U W                                      finish pass (E1 internal, E2 FACE, U/W offsets; R1 = pre-finish of the area)
//!   G71 ... D R E U W H                              area clear (E100, E-100 internal, E101/E-101 FACE direction) / face clear (E401), D depth
//!   G75 ... D I E L C                                grooving: D peck, I stepover, E tip width, L11x full retract
//!   G78 ... G178                                     grooving finish mode (path lines only)
//!   G117 X Z, G76 X Z ... D E F                      threading: start, final diameter and end, first cut, E1/-1, pitch
//!   G74 + G1 pecks                                   drilling (peck = start Z - first G1 Z), G4 inside = dwell,
//!                                                    G1 back-off = RETRACT DIST, G0 to the start between = FULL RET
//!   G99 F T                                          tool change later in the program
//!
//! The contour of every profile operation is the drawing's chain. The rebuilt project can be checked by posting it
//! and comparing with the .PRG ALPHA-T wrote (see verify()).

use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use regex::Regex;

use super::{ascii_replace, py_float, py_strip, read_file};
use crate::error::{Error, Result, not_implemented, value};
use crate::drawing::{outer_profile, pick_order};
use crate::i18n::tr;
use crate::model::{
    AreaClear, Datum, Drill, Dwell, Element, FaceClear, FinishPass, Groove, GrooveMode, MoveTo, Operation, Point,
    Project, Thread, ToolChange,
};
use crate::post::alphat_prg::post;
use crate::py::round;

static RE_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"([A-Z])(-?\d*\.?\d+)").unwrap());

/// The G codes of a line and its other words (the first of each letter; N left out).
struct Words {
    gs: Vec<i64>,
    w: Vec<(char, f64)>,
}

impl Words {
    fn parse(line: &str) -> Result<Self> {
        let (mut gs, mut w) = (vec![], vec![]);
        for m in RE_WORD.captures_iter(line) {
            let k = m[1].chars().next().unwrap();
            let v = py_float(&m[2])?;
            if k == 'G' {
                gs.push(v as i64);
            } else if k != 'N' && !w.iter().any(|(c, _)| *c == k) {
                w.push((k, v));
            }
        }
        Ok(Words { gs, w })
    }

    fn g(&self, g: i64) -> bool {
        self.gs.contains(&g)
    }

    fn get(&self, k: char) -> Option<f64> {
        self.w.iter().find(|(c, _)| *c == k).map(|(_, v)| *v)
    }

    fn has(&self, k: char) -> bool {
        self.get(k).is_some()
    }

    fn or(&self, k: char, default: f64) -> f64 {
        self.get(k).unwrap_or(default)
    }

    /// A word the line must have (Python's KeyError).
    fn need(&self, k: char) -> Result<f64> {
        self.get(k).ok_or_else(|| Error::Value(format!("'{k}'")))
    }
}

fn gs_of(line: &str) -> Result<Vec<i64>> {
    Ok(Words::parse(line)?.gs)
}

// --- the listing ------------------------------------------------------------------------------------------

/// The drawing elements a grooving cycle lists after its G75 (G0/G1 X/Z, modal, up to G175), in order; the
/// whole drawing when they do not all match (GROOVE_internal picks only the bore 0..-20 of the drawing).
fn listed_contour(project: &Project, lines: &[&str], mut i: usize, end_g: i64, fallback: Vec<String>)
                  -> Result<Vec<String>> {
    let (mut px, mut pz): (Option<f64>, Option<f64>) = (None, None);
    let mut pts = vec![];
    while i < lines.len() {
        let w = Words::parse(lines[i])?;
        i += 1;
        if w.g(end_g) {
            break;
        }
        if w.g(0) || w.g(1) {
            px = w.get('X').or(px);
            pz = w.get('Z').or(pz);
            if let (Some(x), Some(z)) = (px, pz) {
                pts.push(Point::new(x, z));
            }
        }
    }
    // {a, b} == {c, d} as sets of (x, z)
    let set = |p: Point, q: Point| if p == q { vec![p] } else { vec![p, q] };
    let same_set = |s: &[Point], t: &[Point]| s.len() == t.len() && s.iter().all(|p| t.contains(p));
    let mut ids = vec![];
    for w in pts.windows(2) {
        let want = set(w[0], w[1]);
        let Some(g) = project.geometry.iter().find(|g| same_set(&set(g.a(), g.b()), &want)) else {
            return Ok(fallback);
        };
        ids.push(g.id().to_string());
    }
    Ok(if ids.is_empty() { fallback } else { ids })
}

/// Python's `str.splitlines()` for the ASCII text of a listing.
fn splitlines(text: &str) -> Vec<&str> {
    let mut out = vec![];
    let mut rest = text;
    while !rest.is_empty() {
        let breaks = ['\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\u{85}', '\u{2028}', '\u{2029}'];
        match rest.char_indices().find(|(_, c)| breaks.contains(c)) {
            None => {
                out.push(rest);
                break;
            }
            Some((k, c)) => {
                out.push(&rest[..k]);
                let skip = if rest[k..].starts_with("\r\n") { 2 } else { c.len_utf8() };
                rest = &rest[k + skip..];
            }
        }
    }
    out
}

#[derive(PartialEq)]
enum State {
    Header,
    /// moves and dwells belong to the tool block
    Tool,
    Op,
    After,
}

/// Operations for `project` (geometry already loaded) from the SD1 text.
pub fn read_sd1(text: &str, project: &Project) -> Result<Vec<Operation>> {
    let all: Vec<String> = project.geometry.iter().map(|g| g.id().to_string()).collect();
    let contour = match pick_order(project, &all) {
        Ok(c) => c,
        Err(_) => {
            // loose construction lines too (a thread's minor diameter, JOB_shaft): the part's profile from the axis
            let p = outer_profile(project);
            if p.is_empty() { all.clone() } else { p }
        }
    };
    let mut ops: Vec<Operation> = vec![];
    let (mut tool, mut feed) = (project.first_tool, 0.15);
    let mut change = project.tool_change;
    let mut start: Option<Point> = None;
    let mut limit: Option<Point> = None;
    let (mut pos_x, mut pos_z) = (0.0, 0.0);
    let mut state = State::Header; // header -> tool (moves/dwells belong to the tool block) -> op
    let lines: Vec<&str> = splitlines(text).into_iter().map(py_strip).filter(|l| !l.is_empty()).collect();
    let start_of = |s: Option<Point>| s.unwrap_or_default();
    let mut i = 0;
    while i < lines.len() {
        let w = Words::parse(lines[i])?;
        i += 1;
        if w.has('T') && (w.gs.is_empty() || w.g(95) || w.g(99)) {
            // N10 F T ... or G99 F T
            (tool, feed) = (w.need('T')? as i32, w.or('F', feed));
            ops.push(Operation::ToolChange(ToolChange { tool, change_point: change, feed }));
            state = State::Tool;
            continue;
        }
        if w.g(92) {
            continue;
        }
        if w.g(5) && i < lines.len() && gs_of(lines[i])?.contains(&6) {
            // G5 + G6 pair: tool change point
            change = Point::new(w.need('X')?, w.need('Z')?);
            if let Some(Operation::ToolChange(tc)) = ops.last_mut() {
                tc.change_point = change;
            }
            i += 1;
            continue;
        }
        if w.g(5) {
            let (x, z) = (w.need('X')?, w.need('Z')?);
            start = Some(Point::new(x, z));
            (pos_x, pos_z) = (x, z);
            state = State::Op;
            continue;
        }
        if w.g(4) && matches!(state, State::Tool | State::After) {
            ops.push(Operation::Dwell(Dwell { revs: w.need('P')? }));
            continue;
        }
        if [170, 171, 174, 175, 176, 178].iter().any(|&g| w.g(g)) {
            // end of an operation's cycle
            state = State::After;
            continue;
        }
        if state == State::After && w.g(0) && i < lines.len() && gs_of(lines[i])?.contains(&99) {
            continue; // the rapid to the tool change point before a G99 tool change
        }
        if matches!(state, State::Tool | State::After) && (w.g(0) || w.g(1)) && (w.has('X') || w.has('Z')) {
            pos_x = w.or('X', pos_x);
            pos_z = w.or('Z', pos_z);
            ops.push(Operation::MoveTo(MoveTo { point: Point::new(pos_x, pos_z), rapid: w.g(0), feed }));
            continue;
        }
        if w.g(115) {
            limit = Some(Point::new(w.need('X')?, w.need('Z')?));
            state = State::Op;
            continue;
        }
        if w.g(70) {
            match ops.last_mut() {
                Some(Operation::AreaClear(o)) if w.get('R') == Some(1.0) => o.pre_finish = true,
                Some(Operation::FaceClear(o)) if w.get('R') == Some(1.0) => o.pre_finish = true,
                _ => ops.push(Operation::FinishPass(FinishPass {
                    tool,
                    start: start_of(start),
                    contour: contour.clone(),
                    feed,
                    offset_x: w.or('U', 0.0),
                    offset_z: w.or('W', 0.0),
                    external: w.or('E', 0.0) != 1.0,
                    face: w.or('E', 0.0) == 2.0,
                })),
            }
            continue;
        }
        if w.g(71) {
            let e = w.or('E', 100.0) as i64;
            let (depth_of_cut, offset_x, offset_z) = (w.or('D', 2.0), w.or('U', 0.0), w.or('W', 0.0));
            if e == 401 || e == 411 {
                // 411 = AWAY (FACE_away_*)
                ops.push(Operation::FaceClear(FaceClear { tool, start: start_of(start), contour: contour.clone(),
                                                          depth_of_cut, offset_x, offset_z, feed, towards: e == 401,
                                                          pre_finish: false }));
            } else if [100, 101, 110, 300].contains(&e.abs()) {
                // 110 = AWAY (AREA_away_tool5), 300 = FOLLOW (AREA_follow_contour)
                let default = if project.units == "mm" { 1.0 } else { round(1.0 / 25.4, 4) }; // RETRACT DIST
                let r = w.or('R', default);
                ops.push(Operation::AreaClear(AreaClear {
                    tool,
                    start: start_of(start),
                    contour: contour.clone(),
                    depth_of_cut,
                    offset_x,
                    offset_z,
                    feed,
                    towards: e.abs() != 110,
                    pre_finish: false,
                    external: e > 0,
                    front: e.abs() != 101,
                    retract: if (r - default).abs() < 1e-9 { None } else { Some(r) },
                    follow: e.abs() == 300,
                }));
            } else {
                return not_implemented(tr("G71 E{e} (area clear option) is not supported yet", &[("e", &e)]));
            }
            continue;
        }
        if w.g(75) {
            // L <front: 1, back: 2><datum: 1 left, 0 right><full retract: 1 yes>, negative for internal
            // (GROOVE_back L210, GROOVE_internal L-110)
            let lval = w.or('L', 110.0) as i64;
            let flags = lval.abs();
            ops.push(Operation::Groove(Groove {
                tool,
                start: start_of(start),
                contour: listed_contour(project, &lines, i, 175, contour.clone())?,
                peck_depth: w.or('D', 0.0),
                stepover: w.or('I', 2.0),
                tip_width: w.or('E', 3.0),
                full_retract: flags % 10 == 1,
                datum: if flags / 10 % 10 == 1 { Datum::Left } else { Datum::Right },
                z_shift: w.or('R', 0.0),
                repeat: (w.or('C', 1.0) as i32 - 1).max(0),
                dwell_revs: w.or('A', 0.0),
                feed,
                external: lval > 0,
                front: flags / 100 != 2,
                finish_offset_x: w.or('U', 0.0),
                finish_offset_z: w.or('W', 0.0),
                mode: GrooveMode::Area,
            }));
            continue;
        }
        if w.g(78) {
            // grooving FINISH mode: no parameters (the path lines up to G178 are its output)
            ops.push(Operation::Groove(Groove { tool, start: start_of(start), contour: contour.clone(),
                                                mode: GrooveMode::Finish, feed, ..Groove::default() }));
            continue;
        }
        if w.g(117) {
            // thread approach point = (start X, thread top + lead-in); the G5 start stays the start
            continue;
        }
        if w.g(76) {
            let external = w.or('E', 1.0) > 0.0;
            let (fin, z_end) = (w.need('X')?, w.need('Z')?);
            let lines_z: Vec<&Element> = project
                .geometry
                .iter()
                .filter(|g| !g.is_arc() && g.a().x == g.b().x && (g.a().z.min(g.b().z) - z_end).abs() < 1e-6)
                .collect();
            let at_final = lines_z.iter().position(|g| (g.a().x - fin).abs() < 1e-6);
            let other = lines_z.iter().enumerate().find(|(k, _)| Some(*k) != at_final).map(|(_, g)| *g);
            let (Some(at_final), Some(other)) = (at_final.map(|k| lines_z[k]), other) else {
                return value(tr("threading: the thread lines are not in the drawing", &[]));
            };
            let (outer, inner) = if external { (other, at_final) } else { (at_final, other) };
            ops.push(Operation::Thread(Thread {
                tool,
                start: start_of(start),
                outer_line: outer.id().into(),
                inner_line: inner.id().into(),
                pitch: w.need('F')?,
                cut_depth: w.or('D', 1.0),
                external,
                lead_in: w.or('I', 5.0),
                lead_out: w.or('L', 0.0),
                spring_passes: w.or('K', 0.0) as i32,
                angle: w.or('A', 0.0),
                towards: w.or('E', 1.0).abs() == 1.0, // E2 = AWAY
                ..Thread::default()
            }));
            continue;
        }
        if w.g(74) {
            // G1 Z pecks, each followed by a G1 back-off; FULL RET adds G0 out to the start Z and G0 back in
            let st = start.ok_or_else(|| Error::Value("'NoneType' object has no attribute 'z'".into()))?;
            let (mut peck, mut dwell, mut full) = (0.0, 0.0, false);
            let (mut backs, mut zs): (Vec<f64>, Vec<f64>) = (vec![], vec![]);
            while i < lines.len() {
                let w2 = Words::parse(lines[i])?;
                if w2.g(174) {
                    break;
                }
                if let (true, Some(z)) = (w2.g(1), w2.get('Z')) {
                    if let Some(&last) = zs.last()
                        && z > last {
                            backs.push(round(z - last, 6));
                        }
                    zs.push(z);
                    if peck == 0.0 {
                        peck = round(st.z - z, 6);
                    }
                }
                if let (true, Some(z)) = (w2.g(0), w2.get('Z'))
                    && (z - st.z).abs() < 1e-9 && i + 1 < lines.len() && !gs_of(lines[i + 1])?.contains(&174) {
                        full = true;
                    }
                if w2.g(4) {
                    dwell = w2.need('P')?;
                }
                i += 1;
            }
            let depth = limit.ok_or_else(|| Error::Value("local variable 'limit' referenced before assignment".into()))?.z;
            let back = backs.first().copied().unwrap_or(2.0);
            let retract = if full { back * 2.0 } else { back };
            ops.push(Operation::Drill(Drill {
                tool,
                start: st,
                depth,
                peck: if (peck - (st.z - depth)).abs() < 1e-9 { 0.0 } else { peck },
                feed,
                dwell_revs: dwell,
                full_retract: full,
                retract: if retract == 2.0 { None } else { Some(retract) },
            }));
            continue;
        }
        // profile points, G20/G21/G17x/M30: nothing to do
    }
    Ok(ops)
}

pub fn load_sd1(path: impl AsRef<Path>, project: &Project) -> Result<Vec<Operation>> {
    read_sd1(&ascii_replace(&read_file(path.as_ref())?), project)
}

/// NAME.SDG -> NAME.SD1 (upper case first, then lower case) when it exists.
pub fn sibling(path: impl AsRef<Path>, suffix: &str) -> Option<PathBuf> {
    let path = path.as_ref();
    let ext = suffix.trim_start_matches('.');
    [ext.to_uppercase(), ext.to_lowercase()].into_iter().map(|e| path.with_extension(e)).find(|c| c.exists())
}

/// True if posting the project gives exactly the bytes ALPHA-T wrote.
pub fn verify(project: &Project, prg_path: impl AsRef<Path>) -> bool {
    match (post(project, None, None), std::fs::read(prg_path)) {
        (Ok(out), Ok(prg)) => out == prg,
        _ => false,
    }
}
