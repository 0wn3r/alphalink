//! Tool pictures for the simulation against the Python app: every ATOOLIB tool and a few made-up ones.

mod common;

use alphacam::model::Tool;
use alphacam::tool_shapes::{internal, outline};
use common::same;
use serde_json::json;

#[test]
fn same_outlines_as_the_python_app() {
    let cases = common::load("tool_shapes", "cases.json");
    let mut failures = vec![];
    for c in cases.as_array().unwrap() {
        let tool: Tool = serde_json::from_value(c["tool"].clone()).unwrap();
        let got: Vec<Vec<[f64; 2]>> =
            outline(Some(&tool)).into_iter().map(|line| line.into_iter().map(|(z, r)| [z, r]).collect()).collect();
        if !same(&json!(got), &c["outline"]) || internal(&tool) != c["internal"].as_bool().unwrap() {
            failures.push(format!("T{} {}: {}", tool.number, tool.name, json!(got)));
        }
    }
    assert!(outline(None).is_empty());
    assert!(cases.as_array().unwrap().len() >= 41);
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
