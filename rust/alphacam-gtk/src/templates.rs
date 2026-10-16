//! Job templates (templates.py): a saved list of operations with their tools and settings but no geometry.
//! STUB: being ported; the API below is what the window uses. A template is its JSON object.

use std::path::PathBuf;

use alphacam::model::Project;
use alphacam::model::tools::ToolLibrary;
use serde_json::Value;

pub fn name(t: &Value) -> String {
    t.get("name").and_then(Value::as_str).unwrap_or_default().to_string()
}

pub fn builtin() -> Vec<Value> {
    vec![]
}

/// (file, template) of the user's saved templates
pub fn user_templates() -> Vec<(PathBuf, Value)> {
    vec![]
}

/// Add the template's operations to the part; the number added.
pub fn apply(_project: &mut Project, _template: &Value, _library: Option<&ToolLibrary>) -> alphacam::error::Result<usize> {
    Ok(0)
}

pub fn from_part(project: &Project, name: &str) -> Value {
    serde_json::json!({"format": "alcam-template/1", "name": name, "units": project.units, "operations": []})
}

pub fn save_user_template(_template: &Value) -> std::io::Result<PathBuf> {
    Err(std::io::Error::other("not ported yet"))
}
