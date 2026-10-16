//! ALPHA-T's file formats against the Python app: every case exported by tools/export_rust_io_fixtures.py (every
//! sample .SDG, .SD1 and .PRG, the .SD1 listing of every post case, the .SDG/.SDT written back, the tool library and
//! DXF inputs the Python tests build) must give the same result or the same refusal.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use alphacam::Error;
use alphacam::io::dxf::{DxfOptions, load_dxf};
use alphacam::io::prg_parse::{Kind, Program, parse, parse_file};
use alphacam::io::sd1::{load_sd1, read_sd1, sibling, verify};
use alphacam::io::sdg::{load_sdg, read_sdg};
use alphacam::io::sdg_write::{SIZE, save_sdg, write_sdg, write_sdt};
use alphacam::io::toollib::{read_tl1, read_tl2};
use alphacam::model::{MachineProfile, Operation, Project, ToolLibrary};
use alphacam::post::alphat_sd1::sd1;
use alphacam::toolpath::MoveKind;
use serde_json::Value;
use sha2::{Digest, Sha256};

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

static MANIFEST: LazyLock<Value> = LazyLock::new(|| {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/io/manifest.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
});

fn cases(section: &str) -> &'static serde_json::Map<String, Value> {
    MANIFEST[section].as_object().unwrap()
}

fn file(rel: &str) -> PathBuf {
    repo().join(rel)
}

fn sha(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

/// The first differing line of two texts, with its neighbours.
fn diff(got: &str, want: &str) -> String {
    let first = got.lines().zip(want.lines()).position(|(a, b)| a != b).unwrap_or(got.lines().count().min(want.lines().count()));
    let near = |s: &str| s.lines().skip(first.saturating_sub(2)).take(5).collect::<Vec<_>>().join(" | ");
    format!("line {}\n  rust:   {}\n  python: {}", first + 1, near(got), near(want))
}

/// None when the error is the one Python raised (class, and the message unless `message` is false).
fn same_error(case: &Value, got: &Error, message: bool) -> Option<String> {
    let class_ok = matches!((case["error"].as_str(), got),
        (Some("ValueError"), Error::Value(_)) | (Some("NotImplementedError"), Error::NotImplemented(_)));
    let want = case["message"].as_str().unwrap_or_default();
    if class_ok && (!message || got.to_string() == want) {
        None
    } else {
        Some(format!("expected {} {want:?}, got {got:?}", case["error"]))
    }
}

fn report(failures: Vec<String>, total: usize, at_least: usize) {
    assert!(total >= at_least, "{total} cases");
    assert!(failures.is_empty(), "{} of {total} cases differ:\n{}", failures.len(), failures.join("\n"));
}

fn compare_projects(name: &str, got: &Project, want: &Project, failures: &mut Vec<String>) {
    if got != want {
        failures.push(format!("{name}: {}", diff(&got.dumps(), &want.dumps())));
    }
}

#[test]
fn sdg_drawings_read_like_python() {
    let mut failures = vec![];
    for (sample, case) in cases("sdg") {
        match (read_sdg(&std::fs::read(file(sample)).unwrap()), case.get("expected")) {
            (Ok(got), Some(expected)) => {
                let mut want = Project::load(file(expected.as_str().unwrap())).unwrap();
                if case.get("without_operations").is_some() {
                    want.operations.clear();
                }
                compare_projects(sample, &got, &want, &mut failures);
            }
            (Err(e), None) => failures.extend(same_error(case, &e, true).map(|m| format!("{sample}: {m}"))),
            (got, _) => failures.push(format!("{sample}: got {:?}", got.map(|p| p.geometry.len()))),
        }
    }
    report(failures, cases("sdg").len(), 129);
}

#[test]
fn sd1_listings_rebuild_the_operations() {
    let mut failures = vec![];
    for (listing, case) in cases("sd1_import") {
        let mut project = load_sdg(file(case["sdg"].as_str().unwrap())).unwrap();
        match (load_sd1(file(listing), &project), case.get("expected")) {
            (Ok(ops), Some(expected)) => {
                project.operations = ops;
                compare_projects(listing, &project, &Project::load(file(expected.as_str().unwrap())).unwrap(),
                                 &mut failures);
            }
            (Err(e), None) => failures.extend(same_error(case, &e, true).map(|m| format!("{listing}: {m}"))),
            (got, _) => failures.push(format!("{listing}: got {got:?}")),
        }
    }
    report(failures, cases("sd1_import").len(), 119);
}

#[test]
fn sd1_listings_written_like_python() {
    let mut failures = vec![];
    for (name, case) in cases("sd1_write") {
        let project = Project::load(file(case["project"].as_str().unwrap())).unwrap();
        match (sd1(&project, None), case.get("expected")) {
            (Ok(got), Some(expected)) => {
                let want = std::fs::read(file(expected.as_str().unwrap())).unwrap();
                if got != want {
                    failures.push(format!("{name}: {}", diff(&String::from_utf8_lossy(&got),
                                                             &String::from_utf8_lossy(&want))));
                }
            }
            (Err(e), None) => failures.extend(same_error(case, &e, true).map(|m| format!("{name}: {m}"))),
            (got, _) => failures.push(format!("{name}: got {:?}", got.map(|b| b.len()))),
        }
    }
    report(failures, cases("sd1_write").len(), 197);
}

#[test]
fn sdg_files_written_like_python() {
    let mut failures = vec![];
    let same = |got: &[u8], want: &str| {
        if want.starts_with("samples/") { std::fs::read(file(want)).unwrap() == got } else { sha(got) == want }
    };
    for (input, case) in cases("sdg_write") {
        let project = if input.ends_with(".SDG") {
            read_sdg(&std::fs::read(file(input)).unwrap()).unwrap()
        } else {
            Project::load(file(input)).unwrap()
        };
        match (write_sdg(&project), case.get("sdg")) {
            (Ok(got), Some(want)) => {
                assert_eq!(got.len(), SIZE);
                if !same(&got, want.as_str().unwrap()) {
                    failures.push(format!("{input}: .SDG differs"));
                }
            }
            (Err(e), None) => failures.extend(same_error(case, &e, true).map(|m| format!("{input}: {m}"))),
            (got, _) => failures.push(format!("{input}: got {:?}", got.map(|b| b.len()))),
        }
        if !same(&write_sdt(&project), case["sdt"].as_str().unwrap()) {
            failures.push(format!("{input}: .SDT differs"));
        }
    }
    report(failures, cases("sdg_write").len(), 138);
}

// --- PRG ---------------------------------------------------------------------------------------------------

/// Python's `repr()` of a float.
fn py_float(v: f64) -> String {
    let s = format!("{v:?}");
    match s.split_once('e') {
        Some((m, e)) => {
            let e: i32 = e.parse().unwrap();
            format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs())
        }
        None => s,
    }
}

fn opt<T: ToString>(v: Option<T>) -> String {
    v.map_or("None".into(), |v| v.to_string())
}

fn optf(v: Option<f64>) -> String {
    v.map_or("None".into(), py_float)
}

fn list(v: &[i64]) -> String {
    format!("[{}]", v.iter().map(i64::to_string).collect::<Vec<_>>().join(", "))
}

/// The same text as prg_digest() in tools/export_rust_io_fixtures.py.
fn digest(prog: &Program) -> String {
    let eol = if prog.eol == "\r\n" { "'\\r\\n'" } else { "'\\n'" };
    let mut lines = vec![format!("program {} {} {eol} {} {} {}", prog.o_number, prog.units, prog.header.len(),
                                 py_float(prog.z_scale), py_float(prog.f_scale))];
    for s in &prog.statements {
        let words: String = s.words.iter().map(|(k, v)| format!("{k}={v};")).collect();
        lines.push(format!("stmt {} {} {} {} {} {words}", s.line, s.kind.as_str(), opt(s.number), opt(s.value.as_ref()),
                           opt(s.target)));
    }
    for b in &prog.blocks {
        let regs = b.registers.iter().map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join(";");
        lines.push(format!("block {} {} {} {regs} {} {}", b.label, b.line, b.op_name(), list(&b.m_codes),
                           list(&b.labels)));
        for p in &b.passes {
            let restart = p.restart.map_or("None".into(), |(a, b)| format!("({a}, {b})"));
            lines.push(format!("pass {} {} {restart}", p.number, p.line));
            for m in &p.moves {
                lines.push(format!("move {} {} {} {} {} {} {} {} {}", m.kind.as_str(), m.line, optf(m.x), optf(m.z),
                                   optf(m.feed), optf(m.dwell_revs), if m.incremental { "True" } else { "False" },
                                   optf(m.centre_x), optf(m.centre_z)));
            }
        }
    }
    lines.join("\n") + "\n"
}

fn same_program(name: &str, prog: &Program, case: &Value, failures: &mut Vec<String>) {
    let ops: Vec<Value> = prog
        .operations()
        .iter()
        .map(|b| serde_json::json!([b.op_name(), b.declared_passes(), b.passes.len()]))
        .collect();
    let summary = serde_json::json!({"units": prog.units, "blocks": prog.blocks.len(), "ops": ops});
    let want = serde_json::json!({"units": case["units"], "blocks": case["blocks"], "ops": case["ops"]});
    if summary != want {
        failures.push(format!("{name}: {summary} != {want}"));
    } else if sha(digest(prog).as_bytes()) != case["sha256"] {
        failures.push(format!("{name}: the parse differs in detail"));
    }
}

#[test]
fn prg_programs_parse_like_python() {
    let mut failures = vec![];
    let samples = MANIFEST["prg"]["samples"].as_object().unwrap();
    for (sample, case) in samples {
        let data = std::fs::read(file(sample)).unwrap();
        let prog = parse(&data, None).unwrap();
        assert_eq!(prog.to_bytes(), data, "{sample}");
        same_program(sample, &prog, case, &mut failures);
    }
    let unscaled = &MANIFEST["prg"]["unscaled"];
    let profile = MachineProfile { z_scale: 1.0, f_scale: 1.0, ..MachineProfile::default() };
    let prog = parse_file(file(unscaled["sample"].as_str().unwrap()), Some(&profile)).unwrap();
    same_program("unscaled", &prog, unscaled, &mut failures);
    report(failures, samples.len(), 117);
}

#[test]
fn broken_programs_are_refused_like_python() {
    let broken = MANIFEST["prg"]["broken"].as_object().unwrap();
    let mut failures = vec![];
    for (name, case) in broken {
        let mut data = std::fs::read(file(case["base"].as_str().unwrap())).unwrap();
        let latin1 = |s: &Value| s.as_str().unwrap().chars().map(|c| c as u8).collect::<Vec<u8>>();
        for pair in case.get("replace").and_then(Value::as_array).into_iter().flatten() {
            let (from, to) = (latin1(&pair[0]), latin1(&pair[1]));
            let mut out = vec![];
            let mut i = 0;
            while i < data.len() {
                if data[i..].starts_with(&from) {
                    out.extend(&to);
                    i += from.len();
                } else {
                    out.push(data[i]);
                    i += 1;
                }
            }
            data = out;
        }
        if let Some(cut) = case.get("cut").and_then(Value::as_u64) {
            data.truncate(data.len() - cut as usize);
        }
        match (parse(&data, None), case.get("message")) {
            (Err(e), Some(message)) => {
                if e.to_string() != message.as_str().unwrap() || e.line != case["line"].as_u64().map(|l| l as usize) {
                    failures.push(format!("{name}: {e} (line {:?}) != {message}", e.line));
                }
            }
            (Ok(prog), None) => {
                assert_eq!(prog.to_bytes(), data, "{name}");
                same_program(name, &prog, case, &mut failures);
            }
            (got, _) => failures.push(format!("{name}: got {:?}", got.map(|p| p.blocks.len()))),
        }
    }
    report(failures, broken.len(), 14);
}

fn prg(name: &str) -> Program {
    parse_file(repo().join("samples/alphat_v12").join(format!("{name}.PRG")), None).unwrap()
}

#[test]
fn prg_sample_details() {
    // tests/test_prg_parse.py SampleDetails
    let b = prg("MET2_finish_metric").operations()[0].clone();
    let moves = b.moves();
    let feeds: Vec<(f64, f64)> = moves.iter().filter(|m| m.kind == MoveKind::Feed).map(|m| (m.x.unwrap(), m.z.unwrap())).collect();
    assert_eq!(feeds, [(0.0, 0.0), (40.0, 0.0), (40.0, -25.0), (60.0, -25.0), (60.0, -50.0), (100.0, -50.0),
                       (100.282, -49.859)]);
    assert_eq!(moves.last().map(|m| (m.kind, m.x, m.z)), Some((MoveKind::Rapid, Some(120.0), Some(5.0))));

    let b = prg("AREA1_metric").operations()[0].clone();
    let first_cut: Vec<f64> = b.passes.iter().map(|p| p.moves[1].x.unwrap()).collect();
    assert_eq!(&first_cut[..3], [96.0, 92.0, 88.0]);
    assert_eq!(first_cut.last(), Some(&-0.8));
    // Restart: pass n jumps to label 50(n+2) when #140 == n+1.
    assert_eq!([b.passes[0].restart, b.passes[1].restart], [Some((2, 503)), Some((3, 504))]);
    assert_eq!(&b.labels[..2], [503, 504]);

    let b = prg("THREAD_metric").operations()[0].clone();
    assert_eq!(b.feed(), Some(1.5)); // pitch, unscaled
    let cuts: Vec<_> = b.moves().into_iter().filter(|m| m.kind == MoveKind::Thread && !m.incremental).collect();
    assert_eq!(cuts.len(), 100);
    assert_eq!(cuts[..3].iter().map(|c| c.x.unwrap()).collect::<Vec<_>>(), [38.0, 37.172, 36.536]);
    assert!(cuts.iter().all(|c| c.z == Some(-23.5) && c.feed == Some(1.5)));

    let p = prg("DRILL_DWELL_metric");
    let tool_dwell: Vec<_> = p.blocks[0].moves().iter().map(|m| (m.kind, m.dwell_revs, m.x, m.z)).collect();
    assert_eq!(tool_dwell, [(MoveKind::Dwell, Some(3.0), Some(100.0), Some(50.0))]);
    let drill: Vec<f64> = p.operations()[0].moves().iter().filter(|m| m.kind == MoveKind::Feed).map(|m| m.z.unwrap()).collect();
    assert_eq!(drill, [-5.0, -3.0, -15.0, -13.0, -25.0, -23.0, -30.0, -28.0]);

    let p = prg("MOVE_RAPID_metric");
    assert_eq!(p.blocks.len(), 2);
    let moves: Vec<_> = p.blocks[0].moves().iter().map(|m| (m.kind, m.x.unwrap(), m.z.unwrap())).collect();
    assert_eq!(moves, [(MoveKind::Feed, 100.0, -50.0), (MoveKind::Feed, 60.0, -50.0), (MoveKind::Rapid, 60.0, -70.0)]);
    assert_eq!(p.program_number(), Ok(10));
    assert_eq!(p.statements[0].kind, Kind::Percent);
}

// --- tool libraries and DXF --------------------------------------------------------------------------------

#[test]
fn tool_libraries_read_like_python() {
    let mut failures = vec![];
    for (path, case) in cases("toollib") {
        let data = std::fs::read(file(path)).unwrap();
        let got = if path.ends_with(".TL1") {
            read_tl1(&data).map(|lib| (Some(lib), None))
        } else {
            read_tl2(&data).map(|pics| (None, Some(pics)))
        };
        match got {
            Ok((Some(lib), _)) => {
                let want = ToolLibrary::from_json(&case["library"]).unwrap();
                if lib != want {
                    failures.push(format!("{path}: {lib:?} != {want:?}"));
                }
            }
            Ok((_, Some(pics))) => {
                let want: BTreeMap<i32, Vec<Vec<[f64; 2]>>> = serde_json::from_value(case["pictures"].clone()).unwrap();
                if pics != want {
                    failures.push(format!("{path}: {pics:?} != {want:?}"));
                }
            }
            Ok(_) => unreachable!(),
            Err(e) => failures.extend(same_error(case, &e, true).map(|m| format!("{path}: {m}"))),
        }
    }
    report(failures, cases("toollib").len(), 8);
}

#[test]
fn dxf_drawings_read_like_python() {
    let mut failures = vec![];
    for (name, case) in cases("dxf") {
        let o = &case["options"];
        let text = |k: &str, d: &str| o.get(k).and_then(Value::as_str).unwrap_or(d).to_string();
        let opts = DxfOptions {
            y_is: text("y_is", "radius"),
            units: text("units", "mm"),
            upper_half: o.get("upper_half").and_then(Value::as_bool).unwrap_or(true),
            rotate: o.get("rotate").and_then(Value::as_i64).unwrap_or(0) as i32,
            front: text("front", "right"),
            axis: text("axis", "as drawn"),
            datum: o.get("datum").and_then(Value::as_bool).unwrap_or(false),
        };
        match (load_dxf(file(case["file"].as_str().unwrap()), &opts), case.get("project")) {
            (Ok(res), Some(want)) => {
                compare_projects(name, &res.project, &Project::loads(want.as_str().unwrap()).unwrap(), &mut failures);
                let skipped: BTreeMap<String, u64> = res.skipped.iter().map(|(k, n)| (k.clone(), *n as u64)).collect();
                let want: BTreeMap<String, u64> = serde_json::from_value(case["skipped"].clone()).unwrap();
                if skipped != want {
                    failures.push(format!("{name}: skipped {skipped:?} != {want:?}"));
                }
            }
            // the reader's own message differs (ezdxf's recover mode reads a non-DXF file as an empty drawing)
            (Err(e), None) => failures.extend(same_error(case, &e, false).map(|m| format!("{name}: {m}"))),
            (got, _) => failures.push(format!("{name}: got {:?}", got.map(|r| r.project.geometry.len()))),
        }
    }
    report(failures, cases("dxf").len(), 33);
}

// --- the rest of the Python tests --------------------------------------------------------------------------

#[test]
fn rebuilt_sample_posts_like_alpha_t_and_files_are_found() {
    let sdg = repo().join("samples/alphat_v12/TOOLCHG_rough1_finish3_metric.SDG");
    let mut project = load_sdg(&sdg).unwrap();
    let listing = sibling(&sdg, ".sd1").unwrap();
    assert_eq!(listing.extension().unwrap(), "SD1");
    project.operations = load_sd1(&listing, &project).unwrap();
    assert!(matches!(project.operations[0], Operation::ToolChange(_)));
    assert!(verify(&project, sdg.with_extension("PRG")));
    assert!(!verify(&project, sdg.with_extension("SDG")));
    assert!(sibling(&sdg, ".XYZ").is_none());
    // CR-only line ends and stray bytes read like Python's splitlines() and errors="replace"
    let text = std::fs::read_to_string(&listing).unwrap();
    assert_eq!(read_sd1(&text.replace("\r\n", "\r"), &project).unwrap(), project.operations);
}

#[test]
fn sdg_and_sdt_files_are_saved_next_to_each_other() {
    let project = load_sdg(repo().join("samples/alphat_v12/AREA1_metric.SDG")).unwrap();
    let dir = std::env::temp_dir().join(format!("alphacam-io-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    for (name, sdg, sdt) in [("PART", "PART.SDG", "PART.SDT"), ("part.sdg", "part.sdg", "part.sdt"),
                             ("x.alcam", "x.SDG", "x.SDT")] {
        let written = save_sdg(&project, dir.join(name)).unwrap();
        assert_eq!(written, [dir.join(sdg), dir.join(sdt)]);
        assert_eq!(read_sdg(&std::fs::read(&written[0]).unwrap()).unwrap().geometry, project.geometry);
    }
    std::fs::remove_dir_all(&dir).unwrap();
    // ALPHA-T's limits
    let mut big = project.clone();
    big.geometry = (0..300)
        .map(|i| alphacam::model::Element::line(alphacam::model::Point::new(i as f64, 0.0),
                                                 alphacam::model::Point::new(i as f64, -1.0), format!("g{i}")))
        .collect();
    assert_eq!(write_sdg(&big).unwrap_err().to_string(), "the drawing has too many elements for ALPHA-T (300 > 255)");
    big.geometry.truncate(255);
    assert!(write_sdg(&big).is_ok());
    big.geometry = (0..390)
        .map(|i| alphacam::model::Element::line(alphacam::model::Point::new(i as f64, 0.0),
                                                 alphacam::model::Point::new(i as f64, -1.0), format!("g{i}")))
        .collect();
    assert!(write_sdg(&big).is_err());
}

#[test]
fn tool_pictures_are_found_whatever_the_case() {
    use alphacam::io::toollib::sibling;
    let dir = std::env::temp_dir().join(format!("alphacam-tl-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("ATOOLIB.TL1"), b"").unwrap();
    std::fs::write(dir.join("atoolib.tl2"), b"").unwrap();
    assert_eq!(sibling(dir.join("ATOOLIB.TL1"), ".TL2").unwrap().file_name().unwrap(), "atoolib.tl2");
    assert!(sibling(dir.join("ATOOLIB.TL1"), ".TL3").is_none());
    std::fs::remove_dir_all(&dir).unwrap();
}
