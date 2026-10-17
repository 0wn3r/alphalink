//! Sensible new operations for the Add menu: tool, start point and contour taken from the part (op_factory.py).

use alphacam::checks::tool as find_tool;
use alphacam::drawing::{groove_elements, outer_profile, thread_lines};
use alphacam::model::geometry::{Element, Point};
use alphacam::model::operations::*;
use alphacam::model::tools::{Tool, ToolLibrary, standard_library};
use alphacam::model::Project;

use crate::i18n::gettext;

/// Each operation kind's names, from one place: (kind, in menus (header capitalization), in the list and the
/// editor (sentence case)), in the Add Operation menu's order.
pub const OP_NAMES: &[(&str, &str, &str)] = &[
    ("area_clear", "Area Clear", "Area clear"),
    ("finish", "Finish Pass", "Finish pass"),
    ("face_clear", "Face Clear", "Face clear"),
    ("groove", "Grooving", "Grooving"),
    ("thread", "Threading", "Threading"),
    ("drill", "Drilling", "Drilling"),
    ("tool_change", "Tool Change", "Tool change"),
    ("dwell", "Dwell", "Dwell"),
    ("move", "Move / Rapid", "Move / rapid"),
];

/// (menu label, operation kind) in the Add Operation menu's order
pub fn op_menu() -> Vec<(String, &'static str)> {
    OP_NAMES.iter().map(|(kind, menu, _)| (gettext(menu), *kind)).collect()
}

pub fn current_tool(project: &Project, at: usize) -> i32 {
    let mut tool = project.first_tool;
    for op in project.operations.iter().take(at) {
        if let Operation::ToolChange(tc) = op {
            tool = tc.tool;
        }
    }
    tool
}

fn round4(v: f64) -> f64 {
    alphacam::py::round(v, 4)
}

/// A new `kind` operation to insert at `at`. Panics on an unknown kind (the menu only offers known ones).
pub fn default_op(kind: &str, project: &Project, at: usize) -> Operation {
    let inch = project.units == "inch";
    let tool = current_tool(project, at);
    let mm = |v: f64| if inch { round4(v / 25.4) } else { v };
    // ALPHA-T's default tool start: billet diameter + 20, Z5 (the billet is already in the part's units)
    let mut start = Point::new(round4(project.billet.diameter + mm(20.0)), if inch { 0.2 } else { 5.0 });
    // the part's outer profile in cutting order, without loose construction or thread lines; grooves and threads
    // get their own elements preselected below
    let contour = outer_profile(project);
    if let Some(s) = project.operations.iter().take(at).rev().find_map(|op| op.start()) {
        start = s; // reuse the start of the operation before
    }
    let mut feed = standard_library().get(tool).map_or(0.15, |t| t.default_feed);
    if inch {
        feed = round4(feed / 25.4); // the library feeds are mm per rev; 0.15 in/rev would be 3.8 mm/rev
    }
    let doc = if inch { 0.0787 } else { 2.0 };
    match kind {
        "area_clear" => {
            Operation::AreaClear(AreaClear { tool, start, contour, depth_of_cut: doc, feed, ..Default::default() })
        }
        "finish" => Operation::FinishPass(FinishPass { tool, start, contour, feed, ..Default::default() }),
        "face_clear" => {
            Operation::FaceClear(FaceClear { tool, start, contour, depth_of_cut: doc, feed, ..Default::default() })
        }
        "groove" => {
            let grooves = groove_elements(project, &contour);
            Operation::Groove(Groove {
                tool,
                start,
                contour: if grooves.is_empty() { contour } else { grooves },
                feed,
                stepover: mm(2.0),
                tip_width: mm(3.0),
                ..Default::default()
            })
        }
        "thread" => {
            let (outer_line, inner_line) = thread_lines(project, &contour).unwrap_or_else(|| {
                // the vertical lines of the drawing: outermost and innermost
                let lines: Vec<(f64, &str)> = project
                    .geometry
                    .iter()
                    .filter_map(|g| match g {
                        Element::Line(l) if l.a.x == l.b.x => Some((l.a.x, l.id.as_str())),
                        _ => None,
                    })
                    .collect();
                // Python's max()/min() keep the first of equal keys
                let pick = |better: fn(f64, f64) -> bool| {
                    lines.iter().fold(None::<(f64, &str)>, |acc, &(x, id)| match acc {
                        Some((bx, _)) if !better(x, bx) => acc,
                        _ => Some((x, id)),
                    })
                };
                let outer = pick(|x, b| x > b).map_or(String::new(), |(_, id)| id.to_string());
                let inner = pick(|x, b| x < b).map_or(String::new(), |(_, id)| id.to_string());
                (outer, inner)
            });
            Operation::Thread(Thread {
                tool,
                start,
                outer_line,
                inner_line,
                pitch: if inch { 0.0625 } else { 1.5 },
                cut_depth: mm(1.0),
                lead_in: mm(5.0),
                ..Default::default()
            })
        }
        "drill" => Operation::Drill(Drill {
            tool,
            start: Point::new(0.0, start.z),
            depth: if inch { -0.8 } else { -20.0 },
            peck: if inch { 0.4 } else { 10.0 },
            feed,
            ..Default::default()
        }),
        "tool_change" => Operation::ToolChange(ToolChange {
            tool: if at == 0 { project.first_tool } else { tool },
            change_point: project.tool_change,
            feed,
        }),
        "dwell" => Operation::Dwell(Dwell { revs: 1.0 }),
        "move" => Operation::MoveTo(MoveTo { point: start, rapid: true, feed }),
        _ => panic!("unknown operation {kind}"),
    }
}

/// The tool types an operation needs (ALPHA-T's ATOOLIB naming); the first is chosen.
fn needs(kind: &str) -> &'static [&'static str] {
    match kind {
        "groove" => &["groove"],
        "thread" => &["thread"],
        "drill" => &["drill"],
        "area_clear" | "finish" => &["turn", "bore"],
        "face_clear" => &["turn"],
        _ => &[],
    }
}

fn internal(tool: &Tool) -> bool {
    let low = tool.name.to_lowercase();
    low.contains("internal") || low.contains("int.") || tool.kind == "bore" || tool.front_angle < 0.0
}

/// Tool change on demand: when the tool in use at `at` does not suit a new `kind` operation (a turning tool for a
/// groove, say), the first suitable tool of the library, external or internal like the operation; None when the
/// current one will do.
pub fn tool_for(kind: &str, project: &Project, at: usize, library: Option<&ToolLibrary>, external: bool) -> Option<i32> {
    let need = needs(kind);
    if need.is_empty() {
        return None;
    }
    let fits = |t: &Tool| need.contains(&t.kind.as_str()) && (kind == "drill" || internal(t) != external);
    if find_tool(project, current_tool(project, at), library).is_some_and(fits) {
        return None;
    }
    let lib = library.unwrap_or_else(|| standard_library());
    let mut tools: Vec<&Tool> = lib.tools.iter().collect();
    tools.sort_by_key(|t| t.number);
    need.iter().find_map(|ttype| tools.iter().find(|t| t.kind == *ttype && fits(t)).map(|t| t.number))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_change_first_uses_first_tool() {
        let p = Project { first_tool: 3, ..Default::default() };
        match default_op("tool_change", &p, 0) {
            Operation::ToolChange(tc) => assert_eq!(tc.tool, 3),
            _ => unreachable!(),
        }
    }

    #[test]
    fn groove_needs_a_groove_tool() {
        let p = Project::default();
        let t = tool_for("groove", &p, 0, None, true).expect("a groove tool");
        assert_eq!(standard_library().get(t).unwrap().kind, "groove");
        assert_eq!(tool_for("dwell", &p, 0, None, true), None);
    }
}
