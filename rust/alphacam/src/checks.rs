//! Tool checks against the job (workflow quick win): warnings, never errors. The program is still written, so a
//! deliberate choice is never blocked; the app shows them on the operation and on the setup sheet.

use std::collections::BTreeMap;

use crate::i18n::tr;
use crate::model::{Operation, Project, Tool, ToolLibrary, standard_library};
use crate::py::fmt_g;

/// The part's own tools first, then the user's library, then ALPHA-T's standard ATOOLIB.
pub fn tool<'a>(project: &'a Project, number: i32, library: Option<&'a ToolLibrary>) -> Option<&'a Tool> {
    project
        .tools
        .iter()
        .find(|t| t.number == number)
        .or_else(|| library.and_then(|l| l.get(number)))
        .or_else(|| standard_library().get(number))
}

/// Whether an operation runs outside (true), inside (false), or has no such choice (None).
fn external(op: &Operation) -> Option<bool> {
    match op {
        Operation::AreaClear(o) => Some(o.external),
        Operation::Groove(o) => Some(o.external),
        Operation::FinishPass(o) => Some(o.external),
        Operation::Thread(o) => Some(o.external),
        _ => None,
    }
}

/// Operation index → what looks wrong about its tool.
pub fn tool_warnings(project: &Project, library: Option<&ToolLibrary>) -> BTreeMap<usize, Vec<String>> {
    let mut out = BTreeMap::new();
    let mut current = project.first_tool;
    for (i, op) in project.operations.iter().enumerate() {
        if let Operation::ToolChange(tc) = op {
            current = tc.tool;
            continue;
        }
        let Some(number) = op.tool() else { continue };
        let mut msgs = vec![];
        if number != current {
            msgs.push(tr("uses T{op} but T{cur} is in the turret: add a Tool Change",
                         &[("op", &number), ("cur", &current)]));
        }
        if let Some(t) = tool(project, number, library) {
            // a thread wants a threading tool, or a 60° neutral tool (the metric thread form; the Training manual's
            // T8)
            if matches!(op, Operation::Thread(_)) && t.kind != "thread" && !(t.hand == "N" && t.angle == 60.0) {
                msgs.push(tr("T{n} is not a threading tool", &[("n", &number)]));
            }
            if let Operation::Groove(g) = op {
                let k = if project.units == "inch" { 25.4 } else { 1.0 };
                if t.kind != "groove" {
                    msgs.push(tr("T{n} is not a grooving tool", &[("n", &number)]));
                } else if t.width > 0.0 && (t.width - g.tip_width * k).abs() > 1e-3 {
                    msgs.push(tr("tip width {w:g} differs from T{n}'s {tw:g}",
                                 &[("w:g", &fmt_g(g.tip_width)), ("n", &number), ("tw:g", &fmt_g(t.width))]));
                }
            }
            if matches!(op, Operation::Drill(_)) && t.kind != "drill" {
                msgs.push(tr("T{n} is not a drill", &[("n", &number)]));
            }
            match external(op) {
                Some(false) if t.kind == "turn" => {
                    msgs.push(tr("an internal operation with an external tool (T{n})", &[("n", &number)]));
                }
                Some(true) if t.kind == "bore" => {
                    msgs.push(tr("an external operation with a boring tool (T{n})", &[("n", &number)]));
                }
                _ => {}
            }
        }
        if !msgs.is_empty() {
            out.insert(i, msgs);
        }
    }
    out
}
