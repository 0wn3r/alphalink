//! CLI:
//!     alphacam dump FILE.PRG [--moves]
//!     alphacam post PROJECT.alcam [-o FILE.PRG] [--sd1 FILE.SD1]
//!
//! (Python: `python -m alphacam`, app/alphacam/__main__.py.)

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use alphacam::i18n::tr;
use alphacam::model::{MachineProfile, Project};
use alphacam::post::alphat_prg::post;
use alphacam::{Error, Result};
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

fn not_ported(what: &str) -> Error {
    Error::NotImplemented(tr("not ported to Rust yet: {what}", &[("what", &what)]))
}

fn profile(z_scale: f64, f_scale: f64) -> MachineProfile {
    MachineProfile { z_scale, f_scale, ..MachineProfile::default() }
}

fn post_command(project: &Path, output: Option<&Path>, sd1: Option<&Path>, profile: &MachineProfile) -> Result<()> {
    let project = Project::load(project)?;
    let data = post(&project, Some(profile), None)?;
    if sd1.is_some() {
        // TODO: alphacam::post::alphat_sd1::sd1(&project) once ported; write it to `sd1` here, before the PRG
        // (Python writes the .SD1 first).
        return Err(not_ported("the .SD1 writer (post/alphat_sd1.py)"));
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

/// Parse an ALPHA-T .PRG and print its structure.
///
/// TODO: port once alphacam::io::prg_parse exists (parse_file(path, profile) -> program). Python prints
/// `{path}: O{o_number} program #{program_number}, {units}, {n} lines, Z x{z_scale:g} F x{f_scale:g} (values below
/// unscaled)`, then one line per block (`N{label} {op_name}`, `T{tool}`, `change`/`start X Z`, `limit X Z`,
/// `pitch `/`F`, `{passes}/{declared} passes`, `{n} moves, X a..b Z c..d`) and with --moves every move as
/// `    pass {number:3} L{line:<5} {kind:6} X Z[ F| revs][ (inc)]`; numbers with alphacam::py::fmt_g, None as "?".
fn dump(_path: &Path, _show_moves: bool, _profile: &MachineProfile) -> Result<()> {
    Err(not_ported("the PRG parser (io/prg_parse.py)"))
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
                if let Err(e) = dump(&f, moves, &profile) {
                    eprintln!("{}: {e}", f.display());
                    status = 1;
                }
            }
            status
        }
    }
}

fn main() -> ExitCode {
    ExitCode::from(run(Cli::parse()))
}
