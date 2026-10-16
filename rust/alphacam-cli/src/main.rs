//! CLI:
//!     alphacam dump FILE.PRG [--moves]
//!     alphacam post PROJECT.alcam [-o FILE.PRG] [--sd1 FILE.SD1]
//!
//! (Python: `python -m alphacam`, app/alphacam/__main__.py.)

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use alphacam::io::prg_parse::parse;
use alphacam::model::{MachineProfile, Project};
use alphacam::post::alphat_prg::post;
use alphacam::post::alphat_sd1::sd1;
use alphacam::py::{fmt_g, repr};
use alphacam::Result;
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "alphacam")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// parse ALPHA-T .PRG files and print their structure
    Dump {
        #[arg(required = true)]
        files: Vec<PathBuf>,
        /// list every move
        #[arg(long)]
        moves: bool,
        #[arg(long, default_value_t = 2.0)]
        z_scale: f64,
        #[arg(long, default_value_t = 2.0)]
        f_scale: f64,
    },
    /// post an .alcam project to an ALPHA-T .PRG
    Post {
        project: PathBuf,
        /// output file (default: stdout)
        #[arg(short, long)]
        output: Option<PathBuf>,
        /// also write ALPHA-T's .SD1 listing to this file
        #[arg(long)]
        sd1: Option<PathBuf>,
        #[arg(long, default_value_t = 2.0)]
        z_scale: f64,
        #[arg(long, default_value_t = 2.0)]
        f_scale: f64,
    },
}

fn profile(z_scale: f64, f_scale: f64) -> MachineProfile {
    MachineProfile { z_scale, f_scale, ..MachineProfile::default() }
}

fn post_command(project: &Path, output: Option<&Path>, sd1_path: Option<&Path>, profile: &MachineProfile)
                -> Result<()> {
    let project = Project::load(project)?;
    let data = post(&project, Some(profile), None)?;
    if let Some(path) = sd1_path {
        std::fs::write(path, sd1(&project, None)?)?;
    }
    match output {
        Some(path) => std::fs::write(path, &data)?,
        None => {
            let mut out = std::io::stdout().lock();
            out.write_all(&data)?;
            out.flush()?;
        }
    }
    Ok(())
}

/// Python's `_fmt`: `{v:g}`, None as "?".
fn fmt(v: Option<f64>) -> String {
    v.map_or_else(|| "?".into(), fmt_g)
}

/// Python's min()/max() of floats: the first of the smallest (largest).
fn extreme(values: &[f64], larger: bool) -> f64 {
    let better = |v: f64, m: f64| if larger { v > m } else { v < m };
    values.iter().copied().reduce(|m, v| if better(v, m) { v } else { m }).unwrap_or(f64::NAN)
}

/// What Python prints of an OSError: `[Errno 2] No such file or directory: 'A.PRG'`.
fn os_error(e: &std::io::Error, path: &str) -> String {
    match e.raw_os_error() {
        Some(n) => {
            let text = e.to_string();
            let text = text.strip_suffix(&format!(" (os error {n})")).unwrap_or(&text).to_string();
            format!("[Errno {n}] {text}: {}", repr(path))
        }
        None => e.to_string(),
    }
}

/// Parse an ALPHA-T .PRG and print its structure. The inner error is the file's own (unreadable, not a program:
/// reported and the next file dumped), the outer one ends the command (Python's uncaught exceptions).
fn dump(path: &Path, show_moves: bool, profile: &MachineProfile) -> Result<std::result::Result<(), String>> {
    let name = path.to_string_lossy();
    let data = match std::fs::read(path) {
        Ok(data) => data,
        Err(e) => return Ok(Err(os_error(&e, &name))),
    };
    let prog = match parse(&data, Some(profile)) {
        Ok(prog) => prog,
        Err(e) => return Ok(Err(e.to_string())),
    };
    let mut out = std::io::stdout().lock();
    writeln!(out, "{name}: O{} program #{}, {}, {} lines, Z x{} F x{} (values below unscaled)", prog.o_number,
             prog.program_number()?, prog.units, prog.statements.len(), fmt_g(prog.z_scale), fmt_g(prog.f_scale))?;
    for b in &prog.blocks {
        let mut parts = vec![format!("N{} {}", b.label, b.op_name())];
        if let Some(tool) = b.tool() {
            parts.push(format!("T{tool}"));
        }
        if let Some((x, z)) = b.start() {
            let what = if b.op_kind() == Some(1) { "change" } else { "start" };
            parts.push(format!("{what} X{} Z{}", fmt(Some(x)), fmt(Some(z))));
        }
        if let Some((x, z)) = b.limit() {
            parts.push(format!("limit X{} Z{}", fmt(Some(x)), fmt(Some(z))));
        }
        if let Some(feed) = b.feed() {
            parts.push(format!("{}{}", if b.op_name() == "thread" { "pitch " } else { "F" }, fmt(Some(feed))));
        }
        if let Some(declared) = b.declared_passes() {
            parts.push(format!("{}/{declared} passes", b.passes.len()));
        }
        let moves = b.moves();
        if !moves.is_empty() {
            let xs: Vec<f64> = moves.iter().filter_map(|m| m.x).collect();
            let zs: Vec<f64> = moves.iter().filter_map(|m| m.z).collect();
            let extent = if xs.is_empty() || zs.is_empty() {
                String::new()
            } else {
                format!(", X {}..{} Z {}..{}", fmt_g(extreme(&xs, false)), fmt_g(extreme(&xs, true)),
                        fmt_g(extreme(&zs, false)), fmt_g(extreme(&zs, true)))
            };
            parts.push(format!("{} moves{extent}", moves.len()));
        }
        writeln!(out, "  {}", parts.join(", "))?;
        if show_moves {
            for p in &b.passes {
                for m in &p.moves {
                    let mut extra = m.feed.map_or_else(String::new, |f| format!(" F{}", fmt(Some(f))));
                    if let Some(revs) = m.dwell_revs {
                        extra = format!(" {} revs", fmt_g(revs));
                    }
                    let inc = if m.incremental { " (inc)" } else { "" };
                    writeln!(out, "    pass {:3} L{:<5} {:6} X{} Z{}{extra}{inc}", p.number, m.line, m.kind.as_str(),
                             fmt(m.x), fmt(m.z))?;
                }
            }
        }
    }
    out.flush()?;
    Ok(Ok(()))
}

fn run(cli: Cli) -> u8 {
    match cli.cmd {
        Cmd::Post { project, output, sd1, z_scale, f_scale } => {
            match post_command(&project, output.as_deref(), sd1.as_deref(), &profile(z_scale, f_scale)) {
                Ok(()) => 0,
                Err(e) => {
                    eprintln!("alphacam: {e}");
                    1
                }
            }
        }
        Cmd::Dump { files, moves, z_scale, f_scale } => {
            let profile = profile(z_scale, f_scale);
            let mut status = 0;
            for f in files {
                match dump(&f, moves, &profile) {
                    Ok(Ok(())) => {}
                    Ok(Err(e)) => {
                        eprintln!("{}: {e}", f.display());
                        status = 1;
                    }
                    Err(e) => {
                        eprintln!("alphacam: {e}");
                        return 1;
                    }
                }
            }
            status
        }
    }
}

fn main() -> ExitCode {
    ExitCode::from(run(Cli::parse()))
}
