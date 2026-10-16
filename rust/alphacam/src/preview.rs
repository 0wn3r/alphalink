//! Toolpath preview for the UI: post the project, parse the PRG back, and map its blocks to the operations.
//!
//! Going through the real post means the preview shows exactly what would be sent (scaling undone by the parser).
//! If an operation cannot be posted, the preview covers the operations before it and reports the error.
//!
//! The PRG parser (io::prg_parse) is ported separately, so [`preview`] takes it as an argument, and the parsed
//! program's structure ([`Program`], [`Block`], [`Pass`], [`Move`]) is declared here with what the preview, the
//! simulation ([`crate::moves`]), the cycle time and the safety check read of it. ALPHA-T's .SD1 listing of the
//! program (post::alphat_sd1) is not ported yet either: [`Preview::sd1`] stays None.

use std::collections::BTreeMap;

use serde::Deserialize;

use crate::error::{Error, Result};
use crate::model::{MachineProfile, OP_KINDS, Operation, Project, ToolLibrary};
use crate::post::alphat_prg::post;
use crate::toolpath::MoveKind;

/// One motion, in drawing units. x/z are the absolute end point; before the first absolute move of a block the
/// position is taken from the block's #123/#124 (see io::prg_parse). `incremental` is set for U/W moves.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Move {
    pub kind: MoveKind,
    /// PRG line of the move
    pub line: usize,
    #[serde(default)]
    pub x: Option<f64>,
    #[serde(default)]
    pub z: Option<f64>,
    /// mm/rev (or inch), unscaled; thread pitch for THREAD
    #[serde(default)]
    pub feed: Option<f64>,
    #[serde(default)]
    pub dwell_revs: Option<f64>,
    #[serde(default)]
    pub incremental: bool,
    /// arcs: absolute centre. I is a diameter increment, K a (scaled) Z increment
    #[serde(default)]
    pub centre_x: Option<f64>,
    #[serde(default)]
    pub centre_z: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Pass {
    /// #121
    pub number: i64,
    pub line: usize,
    #[serde(default)]
    pub moves: Vec<Move>,
}

/// A block N<k*10>. Block 1 is the tool change (#127=1); the last one only holds M65.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Block {
    pub label: u32,
    pub line: usize,
    /// assignments before the first M66, raw text
    #[serde(default)]
    pub registers: BTreeMap<u32, String>,
    #[serde(default)]
    pub passes: Vec<Pass>,
    #[serde(default)]
    pub m_codes: Vec<i64>,
    pub z_scale: f64,
    pub f_scale: f64,
}

impl Block {
    pub fn index(&self) -> u32 {
        self.label / 10
    }

    pub fn reg(&self, n: u32) -> Option<f64> {
        self.registers.get(&n).map(|v| v.parse().unwrap_or(f64::NAN))
    }

    pub fn op_kind(&self) -> Option<i32> {
        self.reg(127).map(|v| v as i32)
    }

    pub fn op_name(&self) -> String {
        match self.op_kind() {
            None => if self.m_codes.contains(&65) { "end" } else { "empty" }.into(),
            Some(k) => OP_KINDS.iter().find(|(n, _)| *n == k).map_or(format!("unknown_{k}"), |(_, name)| name.to_string()),
        }
    }

    pub fn tool(&self) -> Option<i32> {
        self.reg(122).map(|v| v as i32)
    }

    pub fn declared_passes(&self) -> Option<i64> {
        self.reg(120).map(|v| v as i64)
    }

    /// #123/#124: tool start (op blocks) or tool change point (block 1), unscaled.
    pub fn start(&self) -> Option<(f64, f64)> {
        self.registers.get(&123)?;
        Some((self.reg(123)?, self.reg(124).unwrap_or(f64::NAN) / self.z_scale))
    }

    /// #125/#126: billet or op limits (SD1 G115), unscaled.
    pub fn limit(&self) -> Option<(f64, f64)> {
        self.registers.get(&125)?;
        Some((self.reg(125)?, self.reg(126).unwrap_or(f64::NAN) / self.z_scale))
    }

    /// #130 unscaled: feed, or thread pitch for threading.
    pub fn feed(&self) -> Option<f64> {
        self.reg(130).map(|v| v / self.f_scale)
    }

    pub fn moves(&self) -> impl Iterator<Item = &Move> {
        self.passes.iter().flat_map(|p| p.moves.iter())
    }
}

/// The structure of a parsed .PRG (io::prg_parse's `Program` without its statements and header).
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Program {
    pub blocks: Vec<Block>,
}

/// The PRG parser: the program's bytes and the machine profile (its Z / feed scaling) in, its structure out.
pub type Parser<'a> = &'a dyn Fn(&[u8], &MachineProfile) -> Result<Program>;

#[derive(Debug, Clone, PartialEq)]
pub struct OpPreview {
    /// index into project.operations; None for the implicit first tool block
    pub op_index: Option<usize>,
    pub blocks: Vec<Block>,
}

impl OpPreview {
    pub fn moves(&self) -> Vec<&Move> {
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
    /// ALPHA-T's .SD1 listing of the same program (None: post::alphat_sd1 is not ported yet)
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
pub fn preview(project: &Project, profile: Option<&MachineProfile>, library: Option<&ToolLibrary>, parse: Parser)
               -> Result<Preview> {
    let default_profile = MachineProfile::default();
    let profile = profile.unwrap_or(&default_profile);
    let mut result = Preview::default();
    let ops = &project.operations;
    let mut n_ok = ops.len();
    let mut data = match post(project, Some(profile), library) {
        Ok(data) => {
            result.data = Some(data.clone());
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
    let program = match parse(&data, profile) {
        Ok(program) => program,
        Err(e) => {
            // never leave the window half refreshed: report it like a post error
            result.error = result.error.or_else(|| Some(message(&e)));
            result.data = None;
            result.error_op = result.error_op.or(Some(0));
            n_ok = 0;
            parse(&post_prefix(project, 0, profile, library)?, profile)?
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
