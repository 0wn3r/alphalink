//! Parse ALPHA-T .PRG programs (DESIGN.md section 5, data/program_formats.json).
//!
//! Two layers:
//!   * lex: every line becomes a Statement that keeps its exact text, so `Program::to_bytes()` reproduces the file;
//!   * structure: header, blocks (N10, N20, ...), passes (#121=n) and moves, with values converted back to drawing
//!     units (Z, F and thread W divided by the profile's z_scale/f_scale; X is 1:1).
//!
//! The parser is strict: any line it does not recognise raises PrgSyntaxError with its line number, so a new
//! ALPHA-T construct shows up as a test failure instead of being silently dropped.

use std::collections::HashSet;
use std::fmt;
use std::path::Path;
use std::sync::LazyLock;

use regex::Regex;

use super::{py_float, py_int, read_file};
use crate::error::Error;
use crate::model::{MachineProfile, OP_KINDS};
use crate::toolpath::MoveKind;

/// A line the parser does not accept. `line` is None for the plain value errors Python raises without one (a pass
/// number that is not an integer).
#[derive(Debug, Clone, PartialEq)]
pub struct PrgSyntaxError {
    pub line: Option<usize>,
    pub text: String,
    pub reason: String,
}

impl PrgSyntaxError {
    fn new(line: usize, text: &str, reason: impl Into<String>) -> Self {
        PrgSyntaxError { line: Some(line), text: text.into(), reason: reason.into() }
    }
}

impl fmt::Display for PrgSyntaxError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self.line {
            Some(line) => write!(f, "line {line}: {}: {}", self.reason, py_repr(&self.text)),
            None => write!(f, "{}", self.reason),
        }
    }
}

impl std::error::Error for PrgSyntaxError {}

impl From<PrgSyntaxError> for Error {
    fn from(e: PrgSyntaxError) -> Self {
        Error::Value(e.to_string())
    }
}

type PResult<T> = std::result::Result<T, PrgSyntaxError>;

/// Python's `repr()` of a str (the line text is ASCII).
fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7F => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

// --- lexer -----------------------------------------------------------------------------------------------

const NUM: &str = r"-?(?:\d+\.?\d*|\.\d+)";

fn full(pattern: &str) -> Regex {
    Regex::new(&format!(r"\A(?:{pattern})\z")).expect("a valid pattern")
}

static RE_PROGRAM: LazyLock<Regex> = LazyLock::new(|| full(r"O(\d+)"));
static RE_ASSIGN: LazyLock<Regex> = LazyLock::new(|| full(r"#(\d+)=(.+)"));
static RE_RESTART: LazyLock<Regex> = LazyLock::new(|| full(r"IF\[#140EQ(\d+)\] GOTO (\d+)"));
static RE_IF: LazyLock<Regex> = LazyLock::new(|| full(r"IF\[.+\]GOTO\[.+\]"));
static RE_MCODE: LazyLock<Regex> = LazyLock::new(|| full(r"M(\d+)"));
static RE_LABEL: LazyLock<Regex> = LazyLock::new(|| full(r"N(\d+)"));
static RE_MOTION: LazyLock<Regex> = LazyLock::new(|| full(&format!(r"(?:G(\d+))?((?:[XZUWIKFP]{NUM})*)")));
static RE_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!(r"([XZUWIKFP])({NUM})")).unwrap());

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Percent,
    /// O<n>
    Program,
    /// #nnn=<value or expression>
    Assign,
    /// IF[#140EQn] GOTO <label>
    Restart,
    /// header restart logic, kept verbatim
    If,
    Mcode,
    /// N<n>
    Label,
    /// G0/G1/G2/G3/G4/G32 words, or modal words only (U21.W-3.)
    Motion,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Percent => "percent",
            Kind::Program => "program",
            Kind::Assign => "assign",
            Kind::Restart => "restart",
            Kind::If => "if",
            Kind::Mcode => "mcode",
            Kind::Label => "label",
            Kind::Motion => "motion",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Statement {
    /// 1-based
    pub line: usize,
    pub text: String,
    pub kind: Kind,
    /// O / M / N number, register, G code, or restart #140 value
    pub number: Option<i64>,
    /// assignment right-hand side
    pub value: Option<String>,
    /// restart label
    pub target: Option<i64>,
    /// motion words, raw text, in the order written
    pub words: Vec<(char, String)>,
}

impl Statement {
    fn new(line: usize, text: &str, kind: Kind) -> Self {
        Statement { line, text: text.into(), kind, number: None, value: None, target: None, words: vec![] }
    }

    pub fn word(&self, k: char) -> Option<&str> {
        self.words.iter().find(|(c, _)| *c == k).map(|(_, v)| v.as_str())
    }
}

fn number(line: usize, text: &str, digits: &str) -> PResult<i64> {
    digits.parse().map_err(|_| PrgSyntaxError::new(line, text, "number too large"))
}

pub fn lex_line(line: usize, text: &str) -> PResult<Statement> {
    if text == "%" {
        return Ok(Statement::new(line, text, Kind::Percent));
    }
    if let Some(m) = RE_PROGRAM.captures(text) {
        return Ok(Statement { number: Some(number(line, text, &m[1])?), ..Statement::new(line, text, Kind::Program) });
    }
    if let Some(m) = RE_ASSIGN.captures(text) {
        return Ok(Statement { number: Some(number(line, text, &m[1])?), value: Some(m[2].into()),
                              ..Statement::new(line, text, Kind::Assign) });
    }
    if let Some(m) = RE_RESTART.captures(text) {
        return Ok(Statement { number: Some(number(line, text, &m[1])?), target: Some(number(line, text, &m[2])?),
                              ..Statement::new(line, text, Kind::Restart) });
    }
    if RE_IF.is_match(text) {
        return Ok(Statement::new(line, text, Kind::If));
    }
    if let Some(m) = RE_MCODE.captures(text) {
        return Ok(Statement { number: Some(number(line, text, &m[1])?), ..Statement::new(line, text, Kind::Mcode) });
    }
    if let Some(m) = RE_LABEL.captures(text) {
        return Ok(Statement { number: Some(number(line, text, &m[1])?), ..Statement::new(line, text, Kind::Label) });
    }
    if let Some(m) = RE_MOTION.captures(text) {
        let g = m.get(1);
        if !text.is_empty() && (g.is_some() || !m[2].is_empty()) {
            let found: Vec<(char, String)> = RE_WORD
                .captures_iter(&m[2])
                .map(|w| (w[1].chars().next().unwrap(), w[2].to_string()))
                .collect();
            let mut words: Vec<(char, String)> = vec![];
            for (k, v) in found.iter().cloned() {
                if words.iter().any(|(c, _)| *c == k) {
                    return Err(PrgSyntaxError::new(line, text, "repeated word"));
                }
                words.push((k, v));
            }
            let number = g.map(|g| number(line, text, g.as_str())).transpose()?;
            return Ok(Statement { number, words, ..Statement::new(line, text, Kind::Motion) });
        }
    }
    Err(PrgSyntaxError::new(line, text, "unrecognised statement"))
}

/// Split into statements. Returns (statements, line ending). The file must be 7-bit ASCII.
pub fn lex(data: &[u8]) -> PResult<(Vec<Statement>, String)> {
    if let Some(start) = data.iter().position(|&b| b >= 0x80) {
        let line = data[..start].iter().filter(|&&b| b == b'\n').count() + 1;
        return Err(PrgSyntaxError::new(line, "", format!("non-ASCII byte {:#04x}", data[start])));
    }
    let text = std::str::from_utf8(data).expect("ASCII");
    let eol = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut lines: Vec<&str> = text.split(eol).collect();
    if lines.last() == Some(&"") {
        lines.pop();
    } else {
        return Err(PrgSyntaxError::new(lines.len(), lines.last().unwrap_or(&""), "missing final line ending"));
    }
    let mut stmts = vec![];
    for (i, line) in lines.iter().enumerate() {
        if line.contains(['\r', '\n']) {
            return Err(PrgSyntaxError::new(i + 1, line, "mixed line endings"));
        }
        stmts.push(lex_line(i + 1, line)?);
    }
    Ok((stmts, eol.into()))
}

// --- structure -------------------------------------------------------------------------------------------

fn motion_g(g: i64) -> Option<MoveKind> {
    Some(match g {
        0 => MoveKind::Rapid,
        1 => MoveKind::Feed,
        2 => MoveKind::ArcCw,
        3 => MoveKind::ArcCcw,
        4 => MoveKind::Dwell,
        32 => MoveKind::Thread,
        _ => return None,
    })
}

/// written with a decimal point
pub const FLOAT_REGISTERS: &[i64] = &[123, 124, 125, 126, 130];
pub const INT_REGISTERS: &[i64] = &[120, 121, 122, 127, 128, 129, 132, 140];

/// One motion, in drawing units. x/z are the absolute end point; before the first absolute move of a block
/// the position is taken from the block's #123/#124 (see parse). `incremental` is set for U/W moves.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Move {
    pub kind: MoveKind,
    pub line: usize,
    pub x: Option<f64>,
    pub z: Option<f64>,
    /// mm/rev (or inch), unscaled; thread pitch for THREAD
    pub feed: Option<f64>,
    pub dwell_revs: Option<f64>,
    pub incremental: bool,
    /// arcs: absolute centre. I is a diameter increment, K a (scaled) Z increment
    pub centre_x: Option<f64>,
    pub centre_z: Option<f64>,
}

impl Move {
    fn new(kind: MoveKind, line: usize, x: Option<f64>, z: Option<f64>) -> Self {
        Move { kind, line, x, z, feed: None, dwell_revs: None, incremental: false, centre_x: None, centre_z: None }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Pass {
    /// #121
    pub number: i64,
    pub line: usize,
    pub moves: Vec<Move>,
    /// (#140 value, label) from IF[#140EQn] GOTO label
    pub restart: Option<(i64, i64)>,
}

/// A block N<k*10>. Block 1 is the tool change (#127=1); the last one only holds M65.
#[derive(Debug, Clone, PartialEq)]
pub struct Block {
    pub label: i64,
    pub line: usize,
    /// assignments before the first M66, raw text, in the order written
    pub registers: Vec<(i64, String)>,
    pub passes: Vec<Pass>,
    pub m_codes: Vec<i64>,
    /// restart labels (N5nn) inside the block
    pub labels: Vec<i64>,
    pub z_scale: f64,
    pub f_scale: f64,
}

impl Block {
    pub fn index(&self) -> i64 {
        self.label / 10
    }

    /// The register's raw text.
    pub fn register(&self, n: i64) -> Option<&str> {
        self.registers.iter().find(|(k, _)| *k == n).map(|(_, v)| v.as_str())
    }

    /// The register as a number (the registers read here are checked to be numbers when parsed).
    pub fn reg(&self, n: i64) -> Option<f64> {
        self.register(n).and_then(|v| py_float(v).ok())
    }

    pub fn op_kind(&self) -> Option<i64> {
        self.reg(127).map(|v| v as i64)
    }

    pub fn op_name(&self) -> String {
        match self.op_kind() {
            None => if self.m_codes.contains(&65) { "end" } else { "empty" }.into(),
            Some(k) => OP_KINDS
                .iter()
                .find(|(n, _)| *n as i64 == k)
                .map_or_else(|| format!("unknown_{k}"), |(_, name)| name.to_string()),
        }
    }

    pub fn tool(&self) -> Option<i64> {
        self.reg(122).map(|v| v as i64)
    }

    pub fn declared_passes(&self) -> Option<i64> {
        self.reg(120).map(|v| v as i64)
    }

    /// #123/#124: tool start (op blocks) or tool change point (block 1), unscaled.
    pub fn start(&self) -> Option<(f64, f64)> {
        Some((self.reg(123)?, self.reg(124)? / self.z_scale))
    }

    /// #125/#126: billet or op limits (SD1 G115), unscaled.
    pub fn limit(&self) -> Option<(f64, f64)> {
        Some((self.reg(125)?, self.reg(126)? / self.z_scale))
    }

    /// #130 unscaled: feed, or thread pitch for threading.
    pub fn feed(&self) -> Option<f64> {
        self.reg(130).map(|v| v / self.f_scale)
    }

    pub fn moves(&self) -> Vec<Move> {
        self.passes.iter().flat_map(|p| p.moves.iter().copied()).collect()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Program {
    pub statements: Vec<Statement>,
    pub eol: String,
    pub o_number: i64,
    /// "mm" (M71) or "inch" (M70)
    pub units: String,
    pub header: Vec<Statement>,
    pub blocks: Vec<Block>,
    pub z_scale: f64,
    pub f_scale: f64,
}

impl Program {
    pub fn program_number(&self) -> crate::Result<i64> {
        for s in &self.header {
            if s.kind == Kind::Assign && s.number == Some(132) {
                return py_int(s.value.as_deref().unwrap_or_default());
            }
        }
        Err(Error::Value("'#132'".into()))
    }

    pub fn operations(&self) -> Vec<&Block> {
        self.blocks.iter().filter(|b| b.op_kind().is_some_and(|k| k != 1)).collect()
    }

    pub fn moves(&self) -> Vec<Move> {
        self.blocks.iter().flat_map(|b| b.moves()).collect()
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        self.statements.iter().flat_map(|s| [s.text.as_bytes(), self.eol.as_bytes()].concat()).collect()
    }
}

fn units_mcode(n: Option<i64>) -> Option<&'static str> {
    match n {
        Some(71) => Some("mm"),
        Some(70) => Some("inch"),
        _ => None,
    }
}

pub fn parse(data: &[u8], profile: Option<&MachineProfile>) -> PResult<Program> {
    let default = MachineProfile::default();
    let profile = profile.unwrap_or(&default);
    let (zs, fs) = (profile.z_scale, profile.f_scale);
    let (stmts, eol) = lex(data)?;

    if stmts.len() < 2 || stmts[0].kind != Kind::Percent || stmts[stmts.len() - 1].kind != Kind::Percent {
        let text = stmts.first().map_or("", |s| s.text.as_str());
        return Err(PrgSyntaxError::new(1, text, "program must start and end with %"));
    }
    if stmts[1].kind != Kind::Program {
        return Err(PrgSyntaxError::new(2, &stmts[1].text, "expected O<number>"));
    }
    let o_number = stmts[1].number.unwrap_or_default();

    let restart_targets: HashSet<i64> = stmts.iter().filter(|s| s.kind == Kind::Restart).filter_map(|s| s.target).collect();
    let last = &stmts[stmts.len() - 1];

    // Header: everything up to the units M-code (M71 mm / M70 inch).
    let mut i = 2;
    while i < stmts.len() - 1 && !(stmts[i].kind == Kind::Mcode && units_mcode(stmts[i].number).is_some()) {
        let s = &stmts[i];
        if !matches!(s.kind, Kind::Assign | Kind::If) {
            return Err(PrgSyntaxError::new(s.line, &s.text, "unexpected statement in header"));
        }
        i += 1;
    }
    if i >= stmts.len() - 1 {
        return Err(PrgSyntaxError::new(last.line, &last.text, "no units M-code (M70/M71) in header"));
    }
    let header = stmts[1..i + 1].to_vec();
    let units = units_mcode(stmts[i].number).unwrap().to_string();
    i += 1;

    let mut blocks: Vec<Block> = vec![];
    // the current pass is the last one of the current block's passes
    let mut has_pass = false;
    let mut in_registers = false;
    let mut modal_g: Option<i64> = None;
    let (mut pos_x, mut pos_z): (Option<f64>, Option<f64>) = (None, None);

    for s in &stmts[i..stmts.len() - 1] {
        if s.kind == Kind::Label && !s.number.is_some_and(|n| restart_targets.contains(&n)) {
            let expected = 10 * (blocks.len() as i64 + 1);
            if s.number != Some(expected) {
                return Err(PrgSyntaxError::new(s.line, &s.text, format!("expected block label N{expected}")));
            }
            blocks.push(Block { label: expected, line: s.line, registers: vec![], passes: vec![], m_codes: vec![],
                                labels: vec![], z_scale: zs, f_scale: fs });
            has_pass = false;
            in_registers = true;
            continue;
        }
        let Some(block) = blocks.last_mut() else {
            return Err(PrgSyntaxError::new(s.line, &s.text, "statement before the first block label"));
        };

        match s.kind {
            Kind::Label => block.labels.push(s.number.unwrap_or_default()),
            Kind::Assign => {
                let reg = s.number.unwrap_or_default();
                let value = s.value.as_deref().unwrap_or_default();
                if reg == 121 {
                    let n = py_int(value).map_err(|e| PrgSyntaxError { line: None, text: s.text.clone(),
                                                                        reason: e.to_string() })?;
                    let current = if has_pass { block.passes.last().map(|p| p.number) } else { None };
                    match current {
                        Some(c) if n != c + 1 => {
                            if n != c {
                                return Err(PrgSyntaxError::new(s.line, &s.text, format!("pass {n} after pass {c}")));
                            }
                        }
                        _ => {
                            block.passes.push(Pass { number: n, line: s.line, moves: vec![], restart: None });
                            has_pass = true;
                        }
                    }
                }
                if in_registers {
                    if block.register(reg).is_some() {
                        return Err(PrgSyntaxError::new(s.line, &s.text, "register set twice in block header"));
                    }
                    if FLOAT_REGISTERS.contains(&reg) || INT_REGISTERS.contains(&reg) {
                        check_register(s, reg, value)?;
                    }
                    block.registers.push((reg, value.into()));
                } else if reg != 121 {
                    return Err(PrgSyntaxError::new(s.line, &s.text, "assignment after the block header"));
                }
            }
            Kind::Mcode => {
                let n = s.number.unwrap_or_default();
                if n == 66 && in_registers {
                    in_registers = false;
                    // Assumed: after the M66/M63 macros the tool sits at #123/#124 (tool change point in block 1,
                    // tool start in op blocks). Every op sample starts with an absolute move, so only moves
                    // appended to the tool block (dwell, move/rapid) depend on this.
                    if let Some((x, z)) = block.start() {
                        (pos_x, pos_z) = (Some(x), Some(z));
                    }
                }
                block.m_codes.push(n);
            }
            Kind::Restart => {
                if has_pass {
                    block.passes.last_mut().unwrap().restart = Some((s.number.unwrap(), s.target.unwrap()));
                }
            }
            Kind::Motion => {
                if !has_pass {
                    // e.g. the rapid to the tool change point in the block before a mid-program tool change
                    block.passes.push(Pass { number: 0, line: s.line, moves: vec![], restart: None });
                    has_pass = true;
                }
                let g = s.number.or(modal_g);
                let Some(kind) = g.and_then(motion_g) else {
                    let shown = g.map_or("None".to_string(), |g| g.to_string());
                    return Err(PrgSyntaxError::new(s.line, &s.text, format!("unsupported motion G{shown}")));
                };
                if s.number.is_some() && g != Some(4) {
                    modal_g = g;
                }
                let mv;
                (mv, pos_x, pos_z) = make_move(s, kind, pos_x, pos_z, zs, fs)?;
                block.passes.last_mut().unwrap().moves.push(mv);
            }
            _ => return Err(PrgSyntaxError::new(s.line, &s.text, "unexpected statement in block")),
        }
    }

    if !blocks.last().is_some_and(|b| b.m_codes.contains(&65)) {
        return Err(PrgSyntaxError::new(last.line, &last.text, "program does not end with an M65 block"));
    }

    Ok(Program { statements: stmts.clone(), eol, o_number, units, header, blocks, z_scale: zs, f_scale: fs })
}

static RE_NUM: LazyLock<Regex> = LazyLock::new(|| full(NUM));

fn check_register(s: &Statement, reg: i64, v: &str) -> PResult<()> {
    if !RE_NUM.is_match(v) {
        return Err(PrgSyntaxError::new(s.line, &s.text, "register value is not a number"));
    }
    if FLOAT_REGISTERS.contains(&reg) && !v.contains('.') {
        return Err(PrgSyntaxError::new(s.line, &s.text, "coordinate/feed register without a decimal point"));
    }
    if INT_REGISTERS.contains(&reg) && v.contains('.') {
        return Err(PrgSyntaxError::new(s.line, &s.text, "integer register with a decimal point"));
    }
    Ok(())
}

type Pos = Option<f64>;

fn make_move(s: &Statement, kind: MoveKind, x: Pos, z: Pos, zs: f64, fs: f64) -> PResult<(Move, Pos, Pos)> {
    let err = |reason: &str| PrgSyntaxError::new(s.line, &s.text, reason);
    let mut w: Vec<(char, f64)> = vec![];
    for (k, v) in &s.words {
        w.push((*k, py_float(v).map_err(|e| err(&e.to_string()))?));
    }
    for (k, v) in &s.words {
        if "XZUWIKF".contains(*k) && !v.contains('.') {
            return Err(err(&format!("{k} value without a decimal point")));
        }
    }
    let get = |k: char| w.iter().find(|(c, _)| *c == k).map(|(_, v)| *v);
    let has = |k: char| get(k).is_some();
    if kind == MoveKind::Dwell {
        if !(w.len() == 1 && has('P')) {
            return Err(err("dwell needs exactly P"));
        }
        return Ok((Move { dwell_revs: Some(get('P').unwrap() / 1000.0), ..Move::new(kind, s.line, x, z) }, x, z));
    }
    if has('P') {
        return Err(err("P word on a motion"));
    }
    let arc = matches!(kind, MoveKind::ArcCw | MoveKind::ArcCcw);
    if arc != (has('I') || has('K')) {
        return Err(err("I/K only (and always) on G2/G3"));
    }
    let mut centre = None;
    if arc {
        let (Some(x), Some(z)) = (x, z) else { return Err(err("arc needs a known start and absolute end")) };
        if has('U') || has('W') {
            return Err(err("arc needs a known start and absolute end"));
        }
        centre = Some((x + get('I').unwrap_or(0.0), z + get('K').unwrap_or(0.0) / zs));
    }
    let absolute = has('X') || has('Z');
    let incremental = has('U') || has('W');
    if absolute && incremental {
        return Err(err("mixed absolute and incremental words"));
    }
    if !(absolute || incremental) {
        return Err(err("motion without an axis word"));
    }
    let (x, z) = if absolute {
        (get('X').or(x), get('Z').map(|v| v / zs).or(z))
    } else {
        (x.map(|x| x + get('U').unwrap_or(0.0)), z.map(|z| z + get('W').unwrap_or(0.0) / zs))
    };
    let mv = Move {
        feed: get('F').map(|f| f / fs),
        incremental,
        centre_x: centre.map(|c| c.0),
        centre_z: centre.map(|c| c.1),
        ..Move::new(kind, s.line, x, z)
    };
    Ok((mv, x, z))
}

pub fn parse_file(path: impl AsRef<Path>, profile: Option<&MachineProfile>) -> crate::Result<Program> {
    Ok(parse(&read_file(path.as_ref())?, profile)?)
}
