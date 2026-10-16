//! Threads and corners against the Python app (test_features.py's cases and every standard thread size), the
//! second setup (test_workflow_456.py and the examples turned around), and the undo stack (test_commands.py).

mod common;

use alphacam::commands::UndoStack;
use alphacam::features::{self, ThreadSpec};
use alphacam::model::{FinishPass, Operation, Point, Project};
use alphacam::{drawing, setups};
use common::{check_step, f, p, pt, s, same};
use serde_json::{Value, json};
use std::cell::RefCell;
use std::rc::Rc;

fn spec_json(t: &ThreadSpec) -> Value {
    json!({"d": t.d, "pitch": t.pitch, "standard": t.standard, "units": t.units, "size": t.size, "tpi": t.tpi,
           "minor": t.minor(), "depth": t.depth(), "relief_diameter": t.relief_diameter(),
           "relief_width": t.relief_width(), "name": t.name()})
}

/// [d, pitch] or [d, pitch, standard, units, size, tpi] as Python's ThreadSpec(*args).
fn spec_from(a: &Value) -> ThreadSpec {
    let a = a.as_array().unwrap();
    let mut t = ThreadSpec::new(f(&a[0]), f(&a[1]));
    if a.len() > 2 {
        (t.standard, t.units, t.size, t.tpi) = (s(&a[2]).into(), s(&a[3]).into(), s(&a[4]).into(), a[5].as_f64());
    }
    t
}

#[test]
fn threads_like_the_python_app() {
    let data = common::load("features", "cases.json");
    let mut failures = vec![];
    for c in data["specs"].as_array().unwrap() {
        let a = &c["args"];
        let t = features::spec(s(&a[0]), s(&a[1]), a[2].as_u64().unwrap() as usize, s(&a[3])).unwrap();
        if !same(&spec_json(&t), &c["spec"]) {
            failures.push(format!("spec {a}: {} != {}", spec_json(&t), c["spec"]));
        }
    }
    for c in data["direct"].as_array().unwrap() {
        let a = &c["args"];
        let mut t = ThreadSpec::new(f(&a[0]), f(&a[1]));
        t.units = s(&a[2]).into();
        if !same(&spec_json(&t), &c["spec"]) {
            failures.push(format!("ThreadSpec {a}: {} != {}", spec_json(&t), c["spec"]));
        }
    }
    for (standard, sizes) in data["sizes"].as_object().unwrap() {
        if !same(&json!(features::sizes(standard)), sizes) {
            failures.push(format!("sizes {standard}"));
        }
    }
    for c in data["nearest"].as_array().unwrap() {
        let a = &c["args"];
        let got = features::nearest(s(&a[0]), f(&a[1]), s(&a[2]));
        if got != s(&c["result"]) {
            failures.push(format!("nearest {a}: {got} != {}", c["result"]));
        }
    }
    for c in data["coarse"].as_array().unwrap() {
        let a = &c["args"];
        if features::coarse(f(&a[0]), f(&a[1])) != c["result"].as_bool().unwrap() {
            failures.push(format!("coarse {a}"));
        }
    }
    assert_eq!(features::nearest_size(39.2), 39);
    assert_eq!(features::nearest_size(41.0), 40);
    let mut steps = 0;
    for case in data["edits"].as_array().unwrap() {
        let name = s(&case["name"]);
        let mut project = common::project(&case["project"]);
        let mut expected = &case["project"];
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            steps += 1;
            let a = step["args"].as_array().unwrap();
            let got = match s(&step["call"]) {
                "add_thread" => features::add_thread(&mut project, s(&a[0]), &spec_from(&a[1]), a[2].as_f64(),
                                                     a[3].as_bool().unwrap())
                    .map(|id| json!(id)),
                "outside_corners" => Ok(Value::Array(features::outside_corners(&project).into_iter().map(p).collect())),
                "all_corners" => Ok(json!(features::all_corners(&mut project, s(&a[0]), f(&a[1]), f(&a[2])))),
                other => panic!("no call {other}"),
            };
            check_step(name, i, step, got, &mut failures);
            if let Some(after) = step.get("project") {
                expected = after;
            }
            if !same(&project.to_json(true), expected) {
                failures.push(format!("{name} step {i}: part\n  rust:   {}\n  python: {expected}", project.to_json(true)));
                break;
            }
        }
    }
    assert!(steps >= 20);
    assert!(failures.is_empty(), "{} differ:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn second_setup_like_the_python_app() {
    let cases = common::load("setups", "cases.json");
    let mut failures = vec![];
    for c in cases.as_array().unwrap() {
        let name = s(&c["name"]);
        let project = common::project(&c["project"]);
        if setups::part_length(&project) != f(&c["part_length"]) {
            failures.push(format!("{name}: part length {}", setups::part_length(&project)));
        }
        let got = setups::turn_around(&project, c["args"][0].as_f64(), c["args"][1].as_i64());
        match (got, c.get("result")) {
            (Ok(q), Some(expected)) if same(&q.to_json(true), expected) => {}
            (Err(e), None) if e.to_string() == s(&c["error"]) => {}
            (got, _) => failures.push(format!("{name}: {:?}", got.map(|q| q.to_json(true)))),
        }
    }
    assert!(cases.as_array().unwrap().len() >= 10);
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// test_commands.py's mutations, by name.
fn mutation(q: &mut Project, name: &str, arg: &Value) -> alphacam::Result<()> {
    match name {
        "feed" => {
            if let Operation::FinishPass(op) = &mut q.operations[arg[0].as_u64().unwrap() as usize] {
                op.feed = f(&arg[1]);
            } else if let Operation::AreaClear(op) = &mut q.operations[arg[0].as_u64().unwrap() as usize] {
                op.feed = f(&arg[1]);
            }
        }
        "pop_op" => {
            q.operations.remove(arg.as_u64().unwrap() as usize);
        }
        "program_number" => q.program_number = arg.as_i64().unwrap(),
        "add_line" => {
            drawing::add_line(q, pt(&arg[0]), pt(&arg[1]))?;
        }
        "bad" => {
            q.operations.push(Operation::FinishPass(FinishPass {
                tool: 3,
                start: Point::new(120.0, 5.0),
                contour: vec!["g0".into()],
                ..FinishPass::default()
            }));
            return Err(alphacam::Error::Value("nope".into()));
        }
        "fillet" => {
            drawing::fillet(q, pt(&arg[0]), f(&arg[1]))?;
        }
        other => panic!("no mutation {other}"),
    }
    Ok(())
}

#[test]
fn undo_like_the_python_app() {
    let cases = common::load("commands", "cases.json");
    let mut failures = vec![];
    for c in cases.as_array().unwrap() {
        let name = s(&c["name"]);
        let project = Project::load(common::fixtures().join("post").join(s(&c["file"]))).unwrap();
        let seen = Rc::new(RefCell::new(vec![]));
        let log = seen.clone();
        let on_change = Box::new(move |p: &Project| log.borrow_mut().push(p.operations.len()));
        let mut st = UndoStack::new(project, Some(on_change), c["limit"].as_u64().unwrap() as usize);
        for (i, step) in c["steps"].as_array().unwrap().iter().enumerate() {
            let action = &step["action"];
            let result = match s(&action[0]) {
                "apply" => match st.apply(s(&action[1]), |q| mutation(q, s(&action[2]), &action[3])) {
                    Ok(()) => Value::Null,
                    Err(e) => json!({"error": e.to_string()}),
                },
                "undo" => json!(st.undo()),
                "redo" => json!(st.redo()),
                "mark_saved" => {
                    st.mark_saved();
                    Value::Null
                }
                "mark_unsaved" => {
                    st.mark_unsaved();
                    Value::Null
                }
                other => panic!("no action {other}"),
            };
            let got = json!({"result": result, "modified": st.modified(), "can_undo": st.can_undo(),
                             "can_redo": st.can_redo(), "project": st.project.to_json(true),
                             "seen": *seen.borrow()});
            let expected = json!({"result": step["result"], "modified": step["modified"], "can_undo": step["can_undo"],
                                  "can_redo": step["can_redo"], "project": step["project"], "seen": step["seen"]});
            if !same(&got, &expected) {
                failures.push(format!("{name} step {i} {action}:\n  rust:   {got}\n  python: {expected}"));
                break;
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
