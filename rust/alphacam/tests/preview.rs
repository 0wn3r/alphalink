//! The preview and what is built on it (the simulation's segments, the cycle time, the safety check, the tool
//! checks) against the Python app, for every project of the post fixtures and the parts the Python tests build
//! (tools/export_rust_core_fixtures.py).
//!
//! The crate has no PRG parser yet (io::prg_parse is ported separately), so the parser handed to the preview gives
//! back the program Python parsed, after checking that it is asked for the very bytes Python parsed.

mod common;

use alphacam::Error;
use alphacam::cycletime::{self, Machine};
use alphacam::model::{MachineProfile, Project, standard_library};
use alphacam::moves::{pass_starts, segments};
use alphacam::preview::{Preview, Program, preview};
use alphacam::{checks, safety};
use common::{f, same};
use serde_json::{Map, Value, json};

struct Case<'a> {
    name: &'a str,
    project: Project,
    expected: &'a Value,
}

fn cases(previews: &Value) -> Vec<Case<'_>> {
    previews
        .as_object()
        .unwrap()
        .iter()
        .map(|(name, c)| Case {
            name,
            project: Project::load(common::fixtures().join(c["file"].as_str().unwrap())).unwrap(),
            expected: c,
        })
        .collect()
}

/// The programs Python parsed for this case, by their bytes.
fn run_preview(case: &Case) -> alphacam::Result<Preview> {
    let parsed = case.expected["parsed"].as_array().cloned().unwrap_or_default();
    let python_data = case.expected["data"].as_str().map(str::as_bytes);
    let parse = |data: &[u8], _: &MachineProfile| -> alphacam::Result<Program> {
        for entry in &parsed {
            let text = entry["data"].as_str().map(str::as_bytes).or(python_data);
            if text == Some(data) {
                return Ok(serde_json::from_value(entry["program"].clone()).unwrap());
            }
        }
        Err(Error::Value(format!("{}: Python never parsed this program:\n{}", case.name, String::from_utf8_lossy(data))))
    };
    preview(&case.project, None, None, &parse)
}

fn ops_json(pv: &Preview) -> Value {
    Value::Array(
        pv.ops
            .iter()
            .map(|o| json!([o.op_index, o.blocks.iter().map(|b| b.label).collect::<Vec<_>>(), o.passes(),
                            o.moves().len()]))
            .collect(),
    )
}

#[test]
fn same_previews_as_the_python_app() {
    let previews = common::load("preview", "cases.json");
    let moves = common::load("moves", "cases.json");
    let cycles = common::load("cycletime", "cases.json");
    let findings = common::load("safety", "cases.json");
    let warnings = common::load("checks", "cases.json");
    let machines = [Machine::default(), Machine { spindle_max: 1000.0, rapid: 1e9, ..Machine::default() },
                    Machine { spindle_max: 1e9, rapid: 1e9, tool_change: 12.5 }];
    let mut failures = vec![];
    let mut fail = |name: &str, what: &str, got: &Value, expected: &Value| {
        if !same(got, expected) {
            failures.push(format!("{name} {what}:\n  rust:   {got}\n  python: {expected}"));
        }
    };
    let all = cases(&previews);
    let mut n_segments = 0;
    for case in &all {
        let (name, c) = (case.name, case.expected);
        if let Some(message) = c.get("message") {
            let got = run_preview(case).map(|_| ()).map_err(|e| e.to_string());
            fail(name, "raises", &json!(got.err()), message);
            continue;
        }
        let pv = match run_preview(case) {
            Ok(pv) => pv,
            Err(e) => {
                fail(name, "preview", &json!(e.to_string()), &json!(null));
                continue;
            }
        };
        let data = pv.data.as_ref().map(|d| String::from_utf8_lossy(d).into_owned());
        fail(name, "data", &json!(data), &c["data"]);
        // Python's KeyError shows its message in quotes ('no tool 77'): the core has no KeyError
        let error = pv.error.as_ref().map(|e| if c["error"].as_str() == Some(&format!("'{e}'")) { format!("'{e}'") } else { e.clone() });
        fail(name, "error", &json!([error, pv.error_op]), &json!([c["error"], c["error_op"]]));
        fail(name, "ops", &ops_json(&pv), &c["ops"]);

        // moves: the simulation's segments
        let segs = segments(&pv);
        n_segments += segs.len();
        let got: Vec<Value> = segs
            .iter()
            .map(|s| json!([s.kind, s.a.0, s.a.1, s.b.0, s.b.1, s.op_index, s.line, s.centre.map(|c| [c.0, c.1]),
                            s.block, s.pass_no]))
            .collect();
        fail(name, "segments", &json!(got), &moves[name]["segments"]);
        fail(name, "pass starts", &json!(pass_starts(&segs)), &moves[name]["pass_starts"]);
        let polylines: Vec<Value> = segs
            .iter()
            .filter(|s| s.centre.is_some())
            .flat_map(|s| [5, 16].map(|n| json!(s.polyline(n).iter().map(|q| [q.0, q.1]).collect::<Vec<_>>())))
            .take(40)
            .collect();
        fail(name, "polylines", &json!(polylines), &moves[name]["polylines"]);

        // the cycle time on three machines
        for (m, expected) in machines.iter().zip(cycles[name].as_array().unwrap()) {
            let est = cycletime::estimate(&case.project, &pv, None, Some(*m));
            let seconds: Vec<Value> = est.seconds.iter().map(|(k, v)| json!([k, v])).collect();
            let got = json!({"machine": [m.spindle_max, m.rapid, m.tool_change], "seconds": seconds,
                             "tool_changes": est.tool_changes, "cutting": est.cutting(), "total": est.total(),
                             "mmss": cycletime::mmss(est.total())});
            fail(name, "cycle time", &got, expected);
        }

        // the safety check and the tool checks
        let got: Vec<Value> = safety::check(&case.project, &pv, None)
            .iter()
            .map(|f| json!([f.op_index, f.kind, f.message, f.x, f.z]))
            .collect();
        fail(name, "safety", &json!(got), &findings[name]);
        let got: Map<String, Value> = checks::tool_warnings(&case.project, None)
            .into_iter()
            .map(|(k, v)| (k.to_string(), json!(v)))
            .collect();
        fail(name, "tool checks", &Value::Object(got), &warnings[name]);
    }
    assert!(all.len() >= 230, "{} cases", all.len());
    assert!(n_segments >= 30000, "{n_segments} segments");
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn rpm_and_mmss_like_the_python_app() {
    let helpers = common::load("cycletime", "helpers.json");
    let custom = alphacam::model::Tool {
        number: 3,
        name: "x".into(),
        surface_speed: 100.0,
        max_rpm: 500.0,
        ..Default::default()
    };
    for c in helpers["rpm"].as_array().unwrap() {
        let tool = if c["custom"].as_bool().unwrap() {
            Some(&custom)
        } else {
            c["tool"].as_i64().and_then(|n| standard_library().get(n as i32))
        };
        let m = &c["machine"];
        let machine = Machine { spindle_max: f(&m[0]), rapid: f(&m[1]), tool_change: f(&m[2]) };
        assert_eq!(cycletime::rpm(tool, f(&c["d"]), &machine), f(&c["rpm"]), "{c}");
    }
    for c in helpers["mmss"].as_array().unwrap() {
        assert_eq!(cycletime::mmss(f(&c[0])), c[1].as_str().unwrap());
    }
}
