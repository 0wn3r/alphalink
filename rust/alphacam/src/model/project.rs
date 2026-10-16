//! Project and machine profile; native file *.alcam (JSON, versioned, DESIGN.md 4.1/4.2).

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::path::Path;

use super::geometry::{Element, Point, Text, resolve};
use super::operations::{OP_KINDS, Operation};
use super::tools::Tool;
use crate::error::{Error, Result, value};
use crate::i18n::tr;

pub const FORMAT: &str = "alcam/1";
pub const UNITS: &[&str] = &["mm", "inch"];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Billet {
    pub diameter: f64,
    pub length: f64,
    pub face_allowance: f64,
    /// inner diameter of a tube billet; 0 = solid bar
    pub bore: f64,
    /// external (gripping the bar, as ALPHA-T draws) | internal (expanding in the bore)
    pub jaws: String,
    /// show the tailstock centre at the front face (ALPHA-T "Display tailstock")
    pub tailstock: bool,
}

impl Default for Billet {
    fn default() -> Self {
        Self { diameter: 100.0, length: 100.0, face_allowance: 0.8, bore: 0.0, jaws: "external".into(), tailstock: false }
    }
}

/// G92 machine reference (SDG settings block).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Reference {
    pub g92_x: f64,
    pub g92_z: f64,
}

impl Default for Reference {
    fn default() -> Self {
        Self { g92_x: 163.3803, g92_z: 120.0 }
    }
}

/// Post settings for one machine. z_scale/f_scale are DESIGN.md C4: x2 as in every ALPHA-T sample; the T macros
/// halve Z and feed (confirmed on the control, RE_NOTES Progress 29).
#[derive(Debug, Clone, PartialEq)]
pub struct MachineProfile {
    pub id: String,
    pub post: String,
    pub z_scale: f64,
    pub f_scale: f64,
    pub decimals_mm: usize,
    pub decimals_inch: usize,
    pub o_number: i32,
    pub verified_on_machine: bool,
    pub op_kinds: Vec<(i32, &'static str)>,
}

impl Default for MachineProfile {
    fn default() -> Self {
        Self {
            id: "harrison-alpha-400t".into(),
            post: "alphat_prg".into(),
            z_scale: 2.0,
            f_scale: 2.0,
            decimals_mm: 3,
            decimals_inch: 4,
            o_number: 2,
            verified_on_machine: false,
            op_kinds: OP_KINDS.to_vec(),
        }
    }
}

impl MachineProfile {
    pub fn decimals(&self, units: &str) -> usize {
        if units == "mm" { self.decimals_mm } else { self.decimals_inch }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Project {
    pub units: String,
    pub machine: String,
    pub billet: Billet,
    pub reference: Reference,
    pub tool_change: Point,
    pub program_number: i64,
    /// units the program is written in; None = the drawing's (ALPHA-T asks on save)
    pub output_units: Option<String>,
    /// tool of the N10 block when no ToolChange op comes first (SDG settings[8])
    pub first_tool: i32,
    pub tools: Vec<Tool>,
    pub geometry: Vec<Element>,
    pub operations: Vec<Operation>,
    pub texts: Vec<Text>,
    /// for the operator, printed on the setup sheet
    pub notes: String,
    /// further setups of the same part in the same file (setup 2: the part turned around), each a project of its
    /// own with its drawing, billet, operations and program number; this project is setup 1
    pub setups: Vec<Project>,
}

impl Default for Project {
    fn default() -> Self {
        Self {
            units: "mm".into(),
            machine: "harrison-alpha-400t".into(),
            billet: Billet::default(),
            reference: Reference::default(),
            tool_change: Point::new(100.0, 50.0),
            program_number: 10,
            output_units: None,
            first_tool: 1,
            tools: vec![],
            geometry: vec![],
            operations: vec![],
            texts: vec![],
            notes: String::new(),
            setups: vec![],
        }
    }
}

fn units_error(units: &str) -> Error {
    Error::Value(tr(
        "units must be one of {units}, not {units2}",
        &[("units", &"('mm', 'inch')"), ("units2", &format!("'{units}'"))],
    ))
}

fn json_error(e: serde_json::Error) -> Error {
    Error::Value(e.to_string())
}

impl Project {
    /// Checks what the types cannot (units, tools, thread pitch) and names unnamed elements g0, g1, ...
    pub fn validate(&mut self) -> Result<()> {
        if !UNITS.contains(&self.units.as_str()) {
            return Err(units_error(&self.units));
        }
        if let Some(u) = &self.output_units
            && !UNITS.contains(&u.as_str()) {
                return Err(units_error(u));
            }
        for (i, g) in self.geometry.iter_mut().enumerate() {
            if g.id().is_empty() {
                g.set_id(format!("g{i}"));
            }
        }
        for t in &self.tools {
            t.validate()?;
        }
        for op in &self.operations {
            if let Operation::Thread(t) = op
                && t.pitch <= 0.0 {
                    return value(tr("thread pitch must be > 0", &[]));
                }
        }
        for s in &mut self.setups {
            s.validate()?;
        }
        Ok(())
    }

    pub fn contour(&self, ids: &[String]) -> Result<Vec<&Element>> {
        resolve(&self.geometry, ids)
    }

    /// The units the program is written in.
    pub fn program_units(&self) -> &str {
        self.output_units.as_deref().unwrap_or(&self.units)
    }

    // --- JSON -------------------------------------------------------------------------------------------

    pub fn to_json(&self, with_setups: bool) -> Value {
        let mut m = Map::new();
        m.insert("format".into(), json!(FORMAT));
        m.insert("units".into(), json!(self.units));
        m.insert("machine".into(), json!(self.machine));
        m.insert("billet".into(), json!(self.billet));
        m.insert("reference".into(), json!(self.reference));
        m.insert("tool_change".into(), json!({"x": self.tool_change.x, "z": self.tool_change.z}));
        m.insert("program_number".into(), json!(self.program_number));
        m.insert("first_tool".into(), json!(self.first_tool));
        m.insert("tools".into(), json!(self.tools));
        m.insert("geometry".into(), json!(self.geometry));
        m.insert("operations".into(), json!(self.operations));
        if !self.texts.is_empty() {
            m.insert("texts".into(), json!(self.texts));
        }
        if !self.notes.is_empty() {
            m.insert("notes".into(), json!(self.notes));
        }
        if with_setups && !self.setups.is_empty() {
            m.insert("setups".into(), Value::Array(self.setups.iter().map(|s| s.to_json(false)).collect()));
        }
        if let Some(u) = &self.output_units
            && *u != self.units {
                m.insert("output_units".into(), json!(u));
            }
        Value::Object(m)
    }

    pub fn from_json(data: &Value) -> Result<Self> {
        let fmt = data.get("format");
        if fmt.and_then(Value::as_str) != Some(FORMAT) {
            let shown = match fmt {
                Some(Value::String(s)) => format!("'{s}'"),
                Some(v) => v.to_string(),
                None => "None".into(),
            };
            return value(tr(
                "not an AlphaLink CAM part (format {fmt}, expected {expected})",
                &[("fmt", &shown), ("expected", &format!("'{FORMAT}'"))],
            ));
        }
        let get = |k: &str| data.get(k).cloned();
        let parse = |k: &str| -> Result<Option<Value>> { Ok(get(k).filter(|v| !v.is_null())) };
        let mut p = Project::default();
        if let Some(v) = parse("units")? {
            p.units = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = parse("machine")? {
            p.machine = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = parse("billet")? {
            p.billet = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = parse("reference")? {
            p.reference = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = parse("tool_change")? {
            p.tool_change = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = parse("program_number")? {
            p.program_number = as_int(&v)?;
        }
        p.output_units = get("output_units").and_then(|v| v.as_str().map(String::from));
        if let Some(v) = parse("first_tool")? {
            p.first_tool = as_int(&v)? as i32;
        }
        if let Some(v) = parse("tools")? {
            p.tools = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = parse("geometry")? {
            p.geometry = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(Value::Array(ops)) = parse("operations")? {
            p.operations = ops
                .into_iter()
                .map(|o| serde_json::from_value(legacy_op(o)).map_err(json_error))
                .collect::<Result<_>>()?;
        }
        if let Some(v) = parse("texts")? {
            p.texts = serde_json::from_value(v).map_err(json_error)?;
        }
        if let Some(v) = get("notes") {
            p.notes = match v {
                Value::String(s) => s,
                Value::Null => String::new(),
                other => other.to_string(),
            };
        }
        if let Some(Value::Array(setups)) = parse("setups")? {
            p.setups = setups.iter().map(Project::from_json).collect::<Result<_>>()?;
        }
        p.validate()?;
        Ok(p)
    }

    /// The .alcam text: JSON indented by one space, with [x, z] pairs and id lists kept on one line.
    pub fn dumps(&self) -> String {
        crate::model::serde_text::dumps(&self.to_json(true))
    }

    pub fn loads(text: &str) -> Result<Self> {
        Self::from_json(&serde_json::from_str(text).map_err(json_error)?)
    }

    pub fn save(&self, path: impl AsRef<Path>) -> Result<()> {
        std::fs::write(path, self.dumps()).map_err(|e| Error::Value(e.to_string()))
    }

    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        Self::loads(&std::fs::read_to_string(path).map_err(|e| Error::Value(e.to_string()))?)
    }
}

fn as_int(v: &Value) -> Result<i64> {
    v.as_i64()
        .or_else(|| v.as_f64().map(|f| f.trunc() as i64))
        .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
        .ok_or_else(|| Error::Value(format!("not an integer: {v}")))
}

/// Parts saved before 2026-10-13 have a thread "angle" of 60 (the profile angle, which the post never used); the
/// field is now ALPHA-T's infeed angle, where 0 is what those parts posted. Their "spring_pass" yes/no is now a
/// count.
fn legacy_op(mut o: Value) -> Value {
    if o.get("type").and_then(Value::as_str) == Some("thread")
        && let Some(m) = o.as_object_mut() {
            if m.get("angle").and_then(Value::as_f64) == Some(60.0) {
                m.insert("angle".into(), json!(0.0));
            }
            if let Some(sp) = m.remove("spring_pass") {
                let on = match sp {
                    Value::Bool(b) => b,
                    Value::Number(n) => n.as_f64() != Some(0.0),
                    Value::Null => false,
                    _ => true,
                };
                m.insert("spring_passes".into(), json!(on as i32));
            }
        }
    o
}
