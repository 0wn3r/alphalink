//! Job templates (templates.py; workflow improvements, step 4): a saved list of operations with their tools and
//! settings but no geometry. Applying one to a drawing adds the operations with their contours picked from that
//! drawing, the way a new operation picks them: the outer profile, the bore for internal operations, the groove, the
//! two thread lines. Any part can be saved as a template; a few common jobs come built in.
//!
//! A template is JSON: {"format": "alcam-template/1", "name", "units", "operations": [...]}, the .alcam operation
//! format without the fields that point into a drawing or depend on the billet.

use std::path::PathBuf;
use std::sync::LazyLock;

use alphacam::drawing::{components, outer_profile, pick_order};
use alphacam::error::{Error, Result};
use alphacam::model::serde_text::dumps;
use alphacam::model::tools::ToolLibrary;
use alphacam::model::{Element, Operation, Project};
use alphacam::py::round;
use gtk::glib;
use regex::Regex;
use serde_json::{Value, json};

use crate::i18n::gettext;
use crate::op_factory::default_op;

pub const FORMAT: &str = "alcam-template/1";
/// filled from the drawing (and the billet) when the template is applied
const FROM_PART: [&str; 5] = ["contour", "outer_line", "inner_line", "start", "change_point"];
/// lengths: converted when the template's units are not the part's (feeds are per revolution: lengths too)
const LENGTHS: [&str; 17] = [
    "depth_of_cut", "offset_x", "offset_z", "feed", "finish_offset_x", "finish_offset_z", "peck_depth", "stepover",
    "tip_width", "z_shift", "retract", "depth", "peck", "pitch", "cut_depth", "lead_in", "lead_out",
];
/// a drill depth: through the billet, whatever its length
const THROUGH: &str = "through";

pub fn name(t: &Value) -> String {
    t.get("name").and_then(Value::as_str).unwrap_or_default().to_string()
}

pub fn template_dir() -> PathBuf {
    glib::user_config_dir().join("alphalink-cam").join("templates")
}

fn json_error(e: serde_json::Error) -> Error {
    Error::Value(e.to_string())
}

/// The part's operations as a template (the drawing references and tool starts left out).
pub fn from_part(project: &Project, name: &str) -> Value {
    let ops: Vec<Value> = project
        .operations
        .iter()
        .map(|op| {
            let mut d = serde_json::to_value(op).expect("operations serialise");
            if let Some(m) = d.as_object_mut() {
                for k in FROM_PART {
                    m.shift_remove(k);
                }
            }
            d
        })
        .collect();
    json!({"format": FORMAT, "name": name, "units": project.units, "operations": ops})
}

/// The bore for internal operations: of the drawing's connected chains, the one closest to the axis (the outer
/// profile when there is only one).
fn bore(project: &Project) -> Vec<String> {
    let outer_x = |id: &String| {
        project.geometry.iter().find(|g| g.id() == id).map_or(f64::NEG_INFINITY, |g: &Element| g.a().x.max(g.b().x))
    };
    // a single loose line is a thread or construction line
    let groups: Vec<Vec<String>> = components(project).into_iter().filter(|g| g.len() > 1).collect();
    if groups.len() < 2 {
        return outer_profile(project);
    }
    let key = |grp: &Vec<String>| grp.iter().map(outer_x).fold(f64::NEG_INFINITY, f64::max);
    // Python's min(): the first of the smallest
    let best = groups.iter().fold(&groups[0], |best, g| if key(g) < key(best) { g } else { best });
    pick_order(project, best).unwrap_or_else(|_| best.clone())
}

/// An operation type the .alcam format knows.
fn is_op_type(kind: &str) -> bool {
    serde_json::from_value::<Operation>(json!({"type": kind})).is_ok()
}

fn valid(t: &Value) -> bool {
    match t.get("operations") {
        None => true,
        Some(Value::Array(ops)) => {
            ops.iter().all(|o| o.is_object() && o.get("type").and_then(Value::as_str).is_some_and(is_op_type))
        }
        Some(_) => false,
    }
}

/// Append the template's operations to the project (one edit: call inside the undo stack's apply); returns how many
/// were added.
pub fn apply(project: &mut Project, template: &Value, _library: Option<&ToolLibrary>) -> Result<usize> {
    if template.get("format").and_then(Value::as_str) != Some(FORMAT) || !valid(template) {
        return Err(Error::Value(gettext("not an AlphaLink CAM template")));
    }
    let inch = project.units == "inch";
    let mut k = 1.0;
    if template.get("units").and_then(Value::as_str).is_some_and(|u| u != project.units) {
        k = if inch { 1.0 / 25.4 } else { 25.4 };
    }
    let ops = template.get("operations").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut added = 0;
    for d in &ops {
        let kind = d["type"].as_str().unwrap_or_default();
        let at = project.operations.len();
        let mut op = serde_json::to_value(default_op(kind, project, at)).map_err(json_error)?;
        let fields = op.as_object_mut().expect("an operation is an object");
        for (key, value) in d.as_object().into_iter().flatten() {
            if key == "type" || FROM_PART.contains(&key.as_str()) || !fields.contains_key(key) {
                continue;
            }
            let value = if key == "depth" && value == THROUGH {
                json!(-round(project.billet.length + if inch { 0.04 } else { 1.0 }, 4))
            } else if LENGTHS.contains(&key.as_str()) && value.is_number() {
                json!(round(value.as_f64().unwrap_or_default() * k, 4))
            } else {
                value.clone()
            };
            fields.insert(key.clone(), value);
        }
        if fields.contains_key("contour") && fields.get("external") == Some(&Value::Bool(false)) {
            fields.insert("contour".into(), json!(bore(project)));
        }
        project.operations.push(serde_json::from_value(op).map_err(json_error)?);
        added += 1;
    }
    Ok(added)
}

/// Built-in templates (the workflow document's examples), in mm. Tool numbers are ALPHA-T's standard library.
fn builtin_ops() -> [(&'static str, Value); 3] {
    [
        (
            "Shaft: rough T1, finish T3",
            json!([
                {"type": "tool_change", "tool": 1},
                {"type": "area_clear", "tool": 1, "depth_of_cut": 2.0, "offset_x": 0.2, "offset_z": 0.1, "feed": 0.2},
                {"type": "tool_change", "tool": 3},
                {"type": "finish", "tool": 3, "feed": 0.1},
            ]),
        ),
        (
            "Bush: drill T25, bore rough and finish T12",
            json!([
                {"type": "tool_change", "tool": 25},
                {"type": "drill", "tool": 25, "depth": THROUGH, "peck": 10.0, "feed": 0.1},
                {"type": "tool_change", "tool": 12},
                {"type": "area_clear", "tool": 12, "external": false, "depth_of_cut": 1.0, "offset_x": 0.2,
                 "offset_z": 0.1, "feed": 0.15},
                {"type": "finish", "tool": 12, "external": false, "feed": 0.08},
            ]),
        ),
        (
            "Groove and thread: groove T16, thread T31",
            json!([
                {"type": "tool_change", "tool": 16},
                {"type": "groove", "tool": 16, "tip_width": 2.0, "stepover": 1.5, "feed": 0.08},
                {"type": "tool_change", "tool": 31},
                {"type": "thread", "tool": 31, "cut_depth": 0.2, "spring_passes": 1},
            ]),
        ),
    ]
}

pub fn builtin() -> Vec<Value> {
    builtin_ops()
        .into_iter()
        .map(|(name, ops)| json!({"format": FORMAT, "name": gettext(name), "units": "mm", "operations": ops,
                                  "builtin": true}))
        .collect()
}

/// (file, template) of the user's saved templates
pub fn user_templates() -> Vec<(PathBuf, Value)> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(template_dir())
        .map(|dir| dir.filter_map(|e| e.ok().map(|e| e.path())).collect())
        .unwrap_or_default();
    files.retain(|f| f.extension().is_some_and(|e| e == "json"));
    files.sort();
    files
        .into_iter()
        .filter_map(|f| {
            let t: Value = serde_json::from_str(&std::fs::read_to_string(&f).ok()?).ok()?;
            (t.get("format").and_then(Value::as_str) == Some(FORMAT) && valid(&t)).then_some((f, t))
        })
        .collect()
}

/// The file name for a template called `name`: its letters, digits, '-' and '_', spaces as '-', at most 60.
fn file_stem(name: &str) -> String {
    static UNSAFE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\w\- ]+").unwrap());
    let stem: String = UNSAFE.replace_all(name, "").trim().replace(' ', "-").chars().take(60).collect();
    if stem.is_empty() { "template".into() } else { stem }
}

pub fn save_user_template(t: &Value) -> std::io::Result<PathBuf> {
    let folder = template_dir();
    std::fs::create_dir_all(&folder)?;
    let path = folder.join(format!("{}.json", file_stem(&name(t))));
    std::fs::write(&path, dumps(t))?; // json.dumps(t, indent=1) + "\n", as the Python app writes it
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Project {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../app/examples/rough_and_finish.alcam");
        Project::load(path).expect("the example part")
    }

    #[test]
    fn shaft_on_a_sample_part() {
        let mut p = sample();
        let before = p.operations.len();
        let shaft = &builtin()[0];
        assert_eq!(apply(&mut p, shaft, None).unwrap(), 4);
        assert_eq!(p.operations.len(), before + 4);
        match &p.operations[before + 1] {
            Operation::AreaClear(a) => {
                assert_eq!((a.tool, a.depth_of_cut, a.offset_x, a.offset_z, a.feed), (1, 2.0, 0.2, 0.1, 0.2));
                assert_eq!(a.contour, outer_profile(&p)); // picked from the drawing
                assert!(a.external);
            }
            op => panic!("area clear expected, not {op:?}"),
        }
        assert!(matches!(&p.operations[before + 3], Operation::FinishPass(f) if f.tool == 3 && f.feed == 0.1));
    }

    #[test]
    fn mm_template_on_an_inch_part() {
        let mut p = sample();
        p.units = "inch".into();
        p.billet.length = 2.0;
        p.operations.clear();
        apply(&mut p, &builtin()[1], None).unwrap();
        match &p.operations[1] {
            Operation::Drill(d) => {
                assert_eq!(d.depth, -2.04); // through the billet
                assert_eq!((d.peck, d.feed), (0.3937, 0.0039));
            }
            op => panic!("drill expected, not {op:?}"),
        }
        match &p.operations[3] {
            Operation::AreaClear(a) => {
                assert_eq!((a.depth_of_cut, a.offset_x, a.offset_z), (0.0394, 0.0079, 0.0039));
                assert!(!a.external);
                assert_eq!(a.tool, 12);
            }
            op => panic!("area clear expected, not {op:?}"),
        }
    }

    #[test]
    fn drill_through_a_mm_billet() {
        let mut p = sample();
        p.operations.clear();
        apply(&mut p, &builtin()[1], None).unwrap();
        assert!(matches!(&p.operations[1], Operation::Drill(d) if d.depth == -round(p.billet.length + 1.0, 4)));
    }

    #[test]
    fn part_round_trip() {
        let p = sample();
        let t = from_part(&p, "Rough and finish");
        for op in t["operations"].as_array().unwrap() {
            for k in FROM_PART {
                assert!(op.get(k).is_none(), "{k} left in {op}");
            }
        }
        let mut q = sample();
        q.operations.clear();
        assert_eq!(apply(&mut q, &t, None).unwrap(), p.operations.len());
        for (a, b) in p.operations.iter().zip(&q.operations) {
            assert_eq!((a.type_name(), a.tool(), a.feed()), (b.type_name(), b.tool(), b.feed()));
        }
    }

    #[test]
    fn templates_checked() {
        let mut p = sample();
        assert!(apply(&mut p, &json!({"format": "alcam/1", "operations": []}), None).is_err());
        assert!(!valid(&json!({"operations": [{"type": "knurl"}]})));
        assert!(valid(&builtin()[2]));
    }

    #[test]
    fn file_names() {
        assert_eq!(file_stem("Shaft: rough T1 / finish"), "Shaft-rough-T1--finish");
        assert_eq!(file_stem("Вал 20"), "Вал-20");
        assert_eq!(file_stem(" ?? "), "template");
    }
}
