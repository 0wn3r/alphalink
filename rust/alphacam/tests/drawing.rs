//! The drawing commands against the Python app: every scripted edit the retired Python app's exporter recorded
//! (the cases of test_drawing.py, test_hit_keys.py and test_regressions.py, every corner of a few profiles filleted
//! and chamfered, and random drawings for the intersections, tangents and blends) gives the same result or the
//! same error, and leaves the same part.

mod common;

use alphacam::drawing::{self, BlendRef, Kind};
use alphacam::model::Project;
use common::{check_step, f, p, pt, s, same, unordered};
use serde_json::{Value, json};

fn ids(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|i| s(i).to_string()).collect()
}

fn blend_ref(v: &Value) -> BlendRef {
    match s(&v[0]) {
        "point" => BlendRef::Point(pt(&v[1])),
        "line" => BlendRef::Line(s(&v[1]).into()),
        _ => BlendRef::Arc(s(&v[1]).into(), v[2].as_bool().unwrap()),
    }
}

fn pairs(v: Vec<(alphacam::model::Point, alphacam::model::Point)>) -> Value {
    Value::Array(v.into_iter().map(|(a, b)| json!([p(a), p(b)])).collect())
}

/// One call of the script, its result as Python's exporter encodes it.
fn call(project: &mut Project, name: &str, a: &[Value]) -> alphacam::Result<Value> {
    Ok(match name {
        "next_id" => json!(drawing::next_id(project)),
        "endpoints" => Value::Array(drawing::endpoints(project).into_iter().map(p).collect()),
        "snap" => {
            let (q, kind) = drawing::snap(project, pt(&a[0]), f(&a[1]), f(&a[2]));
            json!([p(q), kind])
        }
        "lock_direction" => p(drawing::lock_direction(pt(&a[0]), pt(&a[1]), f(&a[2]))),
        "add_line" => json!(drawing::add_line(project, pt(&a[0]), pt(&a[1]))?),
        "delete_element" => {
            drawing::delete_element(project, s(&a[0]))?;
            Value::Null
        }
        "edit_element" => {
            drawing::edit_element(project, s(&a[0]), pt(&a[1]), pt(&a[2]), a[3].as_f64(), a[4].as_bool().unwrap())?;
            Value::Null
        }
        "fillet" => json!(drawing::fillet(project, pt(&a[0]), f(&a[1]))?),
        "chamfer" => json!(drawing::chamfer(project, pt(&a[0]), f(&a[1]), f(&a[2]))?),
        "pick_order" => json!(drawing::pick_order(project, &ids(&a[0]))?),
        "add_arc_3p" => json!(drawing::add_arc_3p(project, pt(&a[0]), pt(&a[1]), pt(&a[2]))?),
        "corner" => p(drawing::corner(project, s(&a[0]), s(&a[1]))?),
        "polar" => p(drawing::polar(pt(&a[0]), f(&a[1]), f(&a[2]))),
        "measure_xz" => json!(drawing::measure_xz(pt(&a[0]), pt(&a[1]))),
        "measure_la" => json!(drawing::measure_la(pt(&a[0]), pt(&a[1]))),
        "key_points" => Value::Array(drawing::key_points(project).into_iter().map(p).collect()),
        "nearest_point" => drawing::nearest_point(project, pt(&a[0])).map_or(Value::Null, p),
        "distance_to" => {
            let g = project.geometry.iter().find(|g| g.id() == s(&a[0])).unwrap();
            json!(drawing::distance_to(g, pt(&a[1])))
        }
        "nearest_element" => {
            let kind = match a[1].as_str() {
                Some("line") => Some(Kind::Line),
                Some("arc") => Some(Kind::Arc),
                _ => None,
            };
            json!(drawing::nearest_element(project, pt(&a[0]), kind))
        }
        "intersections" => Value::Array(drawing::intersections(project).into_iter().map(p).collect()),
        "nearest_intersection" => drawing::nearest_intersection(project, pt(&a[0])).map_or(Value::Null, p),
        "erase_drawing" => {
            drawing::erase_drawing(project);
            Value::Null
        }
        "add_text" => {
            drawing::add_text(project, pt(&a[0]), s(&a[1]), f(&a[2]))?;
            Value::Null
        }
        "nearest_text" => json!(drawing::nearest_text(project, pt(&a[0]))),
        "add_circle" => json!(drawing::add_circle(project, pt(&a[0]), f(&a[1]))?),
        "tangents_point_arc" => pairs(drawing::tangents_point_arc(project, pt(&a[0]), s(&a[1]))?),
        "tangents_arc_arc" => pairs(drawing::tangents_arc_arc(project, s(&a[0]), s(&a[1]))?),
        "tangents_at_angle" => pairs(drawing::tangents_at_angle(project, s(&a[0]), f(&a[1]))?),
        "parallels" => pairs(drawing::parallels(project, s(&a[0]), f(&a[1]))?),
        "split_arc" => json!(drawing::split_arc(project, s(&a[0]), pt(&a[1]))?),
        "mirror_axis" => json!(drawing::mirror_axis(project)?),
        "blends" => Value::Array(
            drawing::blends(project, &blend_ref(&a[0]), &blend_ref(&a[1]), f(&a[2]))?
                .into_iter()
                .map(|(s, c, e)| json!([p(s), p(c), p(e)]))
                .collect(),
        ),
        "add_blend" => json!(drawing::add_blend(project, pt(&a[0]), pt(&a[1]), pt(&a[2]))?),
        "components" => json!(drawing::components(project)),
        "outer_profile" => json!(drawing::outer_profile(project)),
        "groove_elements" => json!(drawing::groove_elements(project, &ids(&a[0]))),
        "thread_lines" => json!(drawing::thread_lines(project, &ids(&a[0]))),
        other => panic!("no call {other}"),
    })
}

#[test]
fn same_edits_as_the_python_app() {
    let cases = common::load("drawing", "cases.json");
    let cases = cases.as_array().unwrap();
    let (mut failures, mut steps) = (vec![], 0);
    for case in cases {
        let name = s(&case["name"]);
        let mut project = common::project(&case["project"]);
        let mut expected = &case["project"];
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            steps += 1;
            let mut got = call(&mut project, s(&step["call"]), step["args"].as_array().unwrap());
            let mut checked = step.clone();
            if matches!(s(&step["call"]), "intersections" | "blends") {
                // candidates the person picks from by tapping: any order
                got = got.map(|v| unordered(&v));
                checked["result"] = unordered(&step["result"]);
            }
            check_step(name, i, &checked, got, &mut failures);
            if let Some(after) = step.get("project") {
                expected = after;
            }
            if !same(&project.to_json(true), expected) {
                failures.push(format!("{name} step {i} {}: part\n  rust:   {}\n  python: {expected}", step["call"],
                                      project.to_json(true)));
                break;
            }
        }
    }
    assert!(cases.len() >= 250, "{} cases", cases.len());
    assert!(failures.is_empty(), "{} of {steps} steps differ:\n{}", failures.len(), failures.join("\n"));
}
