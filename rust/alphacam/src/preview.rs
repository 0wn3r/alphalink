//! Toolpath preview for the UI: post the project, parse the PRG back, and map its blocks to the operations.
//!
//! Going through the real post means the preview shows exactly what would be sent (scaling undone by the parser).
//! If an operation cannot be posted, the preview covers the operations before it and reports the error.

use crate::error::{Error, Result};
pub use crate::io::prg_parse::{Block, Move, Pass, Program};
use crate::io::prg_parse::parse;
use crate::model::{MachineProfile, Operation, Project, ToolLibrary};
use crate::post::alphat_prg::post;
use crate::post::alphat_sd1::sd1;

#[derive(Debug, Clone, PartialEq)]
pub struct OpPreview {
    /// index into project.operations; None for the implicit first tool block
    pub op_index: Option<usize>,
    pub blocks: Vec<Block>,
}

impl OpPreview {
    pub fn moves(&self) -> Vec<Move> {
        self.blocks.iter().flat_map(Block::moves).collect()
    }

    pub fn passes(&self) -> i64 {
        self.blocks.iter().map(|b| b.declared_passes().unwrap_or(0)).sum()
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Preview {
    /// the PRG, when the whole project posts
    pub data: Option<Vec<u8>>,
    /// ALPHA-T's .SD1 listing of the same program (None if the SD1 writer can't do it)
    pub sd1: Option<Vec<u8>>,
    pub program: Option<Program>,
    pub ops: Vec<OpPreview>,
    pub error: Option<String>,
    /// index of the operation that failed
    pub error_op: Option<usize>,
}

impl Preview {
    pub fn for_op(&self, index: usize) -> Option<&OpPreview> {
        self.ops.iter().find(|p| p.op_index == Some(index))
    }
}

fn post_prefix(project: &Project, n: usize, profile: &MachineProfile, library: Option<&ToolLibrary>)
               -> Result<Vec<u8>> {
    let mut trial = project.clone();
    trial.operations.truncate(n);
    post(&trial, Some(profile), library)
}

/// What Python shows of an exception: its text, or its class name when it has none.
fn message(e: &Error) -> String {
    let text = e.to_string();
    if !text.is_empty() {
        return text;
    }
    match e {
        Error::Value(_) => "ValueError".into(),
        Error::NotImplemented(_) => "NotImplementedError".into(),
        Error::Cancelled(_) => "Cancelled".into(),
        Error::TransferTimeout { .. } => "TransferTimeout".into(),
        Error::Io(_) => "OSError".into(),
    }
}

/// Errors only when not even the part without its operations posts (or its program does not parse).
pub fn preview(project: &Project, profile: Option<&MachineProfile>, library: Option<&ToolLibrary>) -> Result<Preview> {
    let default_profile = MachineProfile::default();
    let profile = profile.unwrap_or(&default_profile);
    let mut result = Preview::default();
    let ops = &project.operations;
    let mut n_ok = ops.len();
    let mut data = match post(project, Some(profile), library) {
        Ok(data) => {
            result.data = Some(data.clone());
            result.sd1 = sd1(project, library).ok();
            data
        }
        Err(e) => {
            // find the first operation that cannot be posted
            n_ok = 0;
            while n_ok < ops.len() {
                if let Err(e2) = post_prefix(project, n_ok + 1, profile, library) {
                    result.error = Some(message(&e2));
                    result.error_op = Some(n_ok);
                    break;
                }
                n_ok += 1;
            }
            if result.error.is_none() {
                result.error = Some(message(&e));
            }
            post_prefix(project, n_ok, profile, library)?
        }
    };
    if project.output_units.as_ref().is_some_and(|u| *u != project.units) {
        // the program is written in other units than the drawing: draw the drawing-units version of it
        let mut shown = project.clone();
        shown.output_units = None;
        data = post_prefix(&shown, n_ok, profile, library)?;
    }
    let program = match parse(&data, Some(profile)) {
        Ok(program) => program,
        Err(e) => {
            // never leave the window half refreshed: report it like a post error
            let text = e.to_string();
            result.error = result.error.or(Some(if text.is_empty() { "PrgSyntaxError".into() } else { text }));
            result.data = None;
            result.error_op = result.error_op.or(Some(0));
            n_ok = 0;
            parse(&post_prefix(project, 0, profile, library)?, Some(profile))?
        }
    };

    let mut blocks = program.blocks.iter().filter(|b| !matches!(b.op_name().as_str(), "empty" | "end")).cloned();
    let mut next = || blocks.next().ok_or_else(|| Error::Value("the program has fewer blocks than operations".into()));
    let mut current_tool = false;
    if !matches!(ops.first(), Some(Operation::ToolChange(_))) {
        result.ops.push(OpPreview { op_index: None, blocks: vec![next()?] });
        current_tool = true;
    }
    for (i, op) in ops[..n_ok].iter().enumerate() {
        if matches!(op, Operation::MoveTo(_) | Operation::Dwell(_)) {
            if current_tool {
                result.ops.push(OpPreview { op_index: Some(i), blocks: vec![] }); // shown inside the tool block
            }
            continue;
        }
        let mut pv = OpPreview { op_index: Some(i), blocks: vec![next()?] };
        match op {
            // its own finish block (#127=10/11/12)
            Operation::AreaClear(o) if o.pre_finish => pv.blocks.push(next()?),
            Operation::FaceClear(o) if o.pre_finish => pv.blocks.push(next()?),
            // the #127=21 spring pass block
            Operation::Thread(t) if t.spring_passes > 0 => pv.blocks.push(next()?),
            Operation::ToolChange(_) => current_tool = true,
            _ => {}
        }
        result.ops.push(pv);
    }
    result.program = Some(program);
    Ok(result)
}
