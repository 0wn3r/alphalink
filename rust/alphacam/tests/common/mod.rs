//! Shared by the tests that replay what the retired Python app's exporter recorded from the Python app.

#![allow(dead_code)]

use std::path::PathBuf;

use alphacam::Error;
use alphacam::model::{Point, Project};
use serde_json::{Value, json};

pub fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

pub fn load(module: &str, name: &str) -> Value {
    let path = fixtures().join(module).join(name);
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display())))
        .unwrap()
}

/// JSON with every number as a float: Python writes 100 where Rust writes 100.0 for the same value. Floats compare
/// exactly.
pub fn norm(v: &Value) -> Value {
    match v {
        Value::Number(n) => json!(n.as_f64().unwrap()),
        Value::Array(a) => Value::Array(a.iter().map(norm).collect()),
        Value::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), norm(v))).collect()),
        other => other.clone(),
    }
}

pub fn same(a: &Value, b: &Value) -> bool {
    norm(a) == norm(b)
}

pub fn pt(v: &Value) -> Point {
    Point::new(v[0].as_f64().unwrap(), v[1].as_f64().unwrap())
}

pub fn p(p: Point) -> Value {
    json!([p.x, p.z])
}

pub fn project(v: &Value) -> Project {
    Project::from_json(v).unwrap()
}

pub fn f(v: &Value) -> f64 {
    v.as_f64().unwrap()
}

pub fn s(v: &Value) -> &str {
    v.as_str().unwrap()
}

pub fn opt_f(v: &Value) -> Option<f64> {
    v.as_f64()
}

/// A step's outcome against what Python recorded: the same result, or an error with the same text.
pub fn check_step(name: &str, i: usize, step: &Value, got: alphacam::Result<Value>, failures: &mut Vec<String>) {
    match (got, step.get("error")) {
        (Ok(v), None) => {
            if !same(&v, &step["result"]) {
                failures.push(format!("{name} step {i} {}: {v} != {}", step["call"], step["result"]));
            }
        }
        (Err(e), Some(class)) => {
            let ok_class = matches!((&e, class.as_str()), (Error::Value(_), Some("ValueError" | "KeyError"))
                | (Error::NotImplemented(_), Some("NotImplementedError")));
            if !ok_class || e.to_string() != step["message"].as_str().unwrap_or_default() {
                failures.push(format!("{name} step {i} {}: {e:?} != {class} {}", step["call"], step["message"]));
            }
        }
        (got, _) => failures.push(format!("{name} step {i} {}: {got:?} != {step}", step["call"])),
    }
}
