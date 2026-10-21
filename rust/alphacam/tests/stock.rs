//! The simulation's stock: what the program leaves of the billet. A roughed and finished part must come out as the
//! drawing: nothing cut inside the profile, nothing left outside it where the program machines.

use std::path::Path;

use alphacam::drawing;
use alphacam::model::operations::Operation;
use alphacam::model::{Element, Project};
use alphacam::moves::{Segment, segments};
use alphacam::preview::preview;
use alphacam::stock::{Simulation, Stock};
use alphacam::toolpath::MoveKind;
use cavalier_contours::polyline::{PlineSource, PlineSourceMut, Polyline};

fn simulate(name: &str) -> (Project, Stock, f64) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/post").join(format!("{name}.alcam"));
    let project = Project::load(path).unwrap();
    let pv = preview(&project, None, None).unwrap();
    let segs = segments(&pv);
    let program = pv.program.as_ref().unwrap();
    let tool = |label: i64| program.blocks.iter().find(|b| b.label == label).and_then(|b| b.tool());
    let mut sim = Simulation::new(&project, &segs, tool, None);
    let before = sim.at(0).area();
    let stock = sim.at(segs.len());
    (project, stock, before)
}

/// The part's profile (drawing::outer_profile, in chain order) as (Z, radius) points, arcs sampled.
fn profile(project: &Project) -> Vec<(f64, f64)> {
    let ids = drawing::pick_order(project, &drawing::outer_profile(project)).unwrap();
    let mut pts: Vec<(f64, f64)> = vec![];
    for id in ids {
        let g = project.geometry.iter().find(|g| g.id() == id).unwrap();
        let seg = match g {
            Element::Arc(arc) => Segment::new(if arc.ccw { MoveKind::ArcCcw } else { MoveKind::ArcCw },
                                              (g.a().x, g.a().z), (g.b().x, g.b().z),
                                              Some((arc.centre.x, arc.centre.z))),
            Element::Line(_) => Segment::new(MoveKind::Feed, (g.a().x, g.a().z), (g.b().x, g.b().z), None),
        };
        let mut line: Vec<(f64, f64)> = seg.polyline(16).into_iter().map(|(x, z)| (z, x / 2.0)).collect();
        let near = |p: (f64, f64), q: (f64, f64)| (p.0 - q.0).abs() < 1e-6 && (p.1 - q.1).abs() < 1e-6;
        if let Some(&last) = pts.last()
            && !near(line[0], last)
        {
            line.reverse();
        }
        for p in line {
            if pts.last().is_none_or(|&l| !near(l, p)) {
                pts.push(p);
            }
        }
    }
    pts
}

fn inside(stock: &Stock, z: f64, r: f64) -> bool {
    stock.regions.iter().any(|p| p.winding_number(cavalier_contours::core::math::Vector2::new(z, r)) != 0)
}

/// Points a little inside and a little outside the drawn profile, along every element.
fn probes(project: &Project, d: f64) -> Vec<((f64, f64), (f64, f64))> {
    let pts = profile(project);
    // the part: the profile, carried on towards the chuck at its last diameter, closed along the axis (a bore:
    // along the billet's outside)
    let internal = project.operations.iter().any(|op| match op {
        Operation::AreaClear(o) => !o.external,
        Operation::FinishPass(o) => !o.external,
        _ => false,
    });
    let edge = if internal { project.billet.diameter / 2.0 + 1.0 } else { 0.0 };
    let mut part = Polyline::new_closed();
    for &(z, r) in &pts {
        part.add(z, r, 0.0);
    }
    let back = -project.billet.length - 1.0;
    part.add(back, pts.last().unwrap().1, 0.0);
    part.add(back, edge, 0.0);
    part.add(pts[0].0, edge, 0.0);
    let in_part = |z: f64, r: f64| part.winding_number(cavalier_contours::core::math::Vector2::new(z, r)) != 0;
    let mut out = vec![];
    for w in pts.windows(2) {
        let ((az, ar), (bz, br)) = (w[0], w[1]);
        let len = f64::hypot(bz - az, br - ar);
        let (nz, nr) = (-(br - ar) / len, (bz - az) / len);
        for t in [0.2, 0.5, 0.8] {
            let (z, r) = (az + t * (bz - az), ar + t * (br - ar));
            let (p, q) = ((z + d * nz, r + d * nr), (z - d * nz, r - d * nr));
            out.push(if in_part(p.0, p.1) { (p, q) } else { (q, p) });
        }
    }
    out
}

#[test]
fn a_finished_part_comes_out_as_drawn() {
    for name in ["example_rough_and_finish", "golden_TWOOP_area_finish_metric", "golden_TOOLCHG_rough1_finish3_metric",
                 "golden_TOOLCHG_inch"] {
        let (project, stock, before) = simulate(name);
        assert!(stock.area() < before, "{name}: nothing cut");
        let d = if project.units == "inch" { 0.004 } else { 0.1 };
        let top = profile(&project).iter().map(|p| p.1).fold(0.0, f64::max);
        for ((zi, ri), (zo, ro)) in probes(&project, d) {
            assert!(inside(&stock, zi, ri), "{name}: cut into the part at Z{zi:.3} X{:.3}", 2.0 * ri);
            // outside the profile, below its largest diameter, the program machines everything away
            if ro < top - d && ro > 0.0 {
                assert!(!inside(&stock, zo, ro), "{name}: stock left at Z{zo:.3} X{:.3}", 2.0 * ro);
            }
        }
    }
}

/// Every area clear and finish pass of the samples, internal and external, with dives, grooves and radii: the
/// tool's insert never goes into the drawn part.
#[test]
fn roughing_and_finishing_never_cut_into_the_part() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/post");
    let mut names: Vec<String> = std::fs::read_dir(&dir)
        .unwrap()
        .flatten()
        .filter_map(|e| e.file_name().to_str()?.strip_suffix(".alcam").map(String::from))
        .filter(|n| n.starts_with("golden_AREA") || n.starts_with("golden_FINISH") || n.starts_with("golden_DIVE"))
        .collect();
    names.sort();
    assert!(names.len() >= 20, "{names:?}");
    let mut failures = vec![];
    for name in &names {
        let (project, stock, before) = simulate(name);
        assert!(stock.area() < before, "{name}: nothing cut");
        let d = if project.units == "inch" { 0.004 } else { 0.1 };
        for ((zi, ri), _) in probes(&project, d) {
            if !inside(&stock, zi, ri) {
                failures.push(format!("{name}: cut into the part at Z{zi:.3} X{:.3}", 2.0 * ri));
                break;
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// The shaft job: roughing, finishing, a relief groove and an M30 thread. The thread passes take the chamfer and the
/// diameter down to the thread's minor diameter (X28.16) and no further.
#[test]
fn a_thread_cuts_to_its_minor_diameter() {
    let (_, stock, _) = simulate("golden_JOB_shaft_metric");
    for z in [-5.0, -12.0, -20.0] {
        assert!(!inside(&stock, z, 28.3 / 2.0), "stock left above the minor diameter at Z{z}");
        assert!(inside(&stock, z, 28.0 / 2.0), "cut below the minor diameter at Z{z}");
    }
    // behind the relief groove the finished X40 is untouched
    assert!(inside(&stock, -40.0, 39.8 / 2.0));
    assert!(!inside(&stock, -40.0, 40.2 / 2.0));
}
