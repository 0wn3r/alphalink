//! The command line: `post` gives the same bytes as the Python app (stdout and -o), `dump` and `--sd1` say what is
//! not ported yet instead of doing something else.

use std::path::{Path, PathBuf};
use std::process::Command;

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../alphacam/tests/fixtures/post")
}

fn alphacam() -> Command {
    Command::new(env!("CARGO_BIN_EXE_alphacam"))
}

#[test]
fn post_to_stdout_and_file() {
    let dir = fixtures();
    let expected = std::fs::read(dir.join("example_area_clear.PRG")).unwrap();
    let out = alphacam().arg("post").arg(dir.join("example_area_clear.alcam")).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    assert_eq!(out.stdout, expected);

    let target = std::env::temp_dir().join(format!("alphacam-cli-test-{}.PRG", std::process::id()));
    let out = alphacam().arg("post").arg(dir.join("example_area_clear.alcam")).arg("-o").arg(&target).output().unwrap();
    assert!(out.status.success());
    assert!(out.stdout.is_empty());
    assert_eq!(std::fs::read(&target).unwrap(), expected);
    std::fs::remove_file(&target).unwrap();
}

#[test]
fn post_with_sd1_is_not_ported_yet() {
    let target = std::env::temp_dir().join(format!("alphacam-cli-test-{}.SD1", std::process::id()));
    let out = alphacam()
        .args(["post", "--sd1"])
        .arg(&target)
        .arg(fixtures().join("example_area_clear.alcam"))
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(out.stdout.is_empty());
    assert!(String::from_utf8_lossy(&out.stderr).contains("not ported to Rust yet"));
    assert!(!target.exists());
}

#[test]
fn dump_reports_each_file() {
    let out = alphacam().args(["dump", "A.PRG", "B.PRG", "--moves"]).output().unwrap();
    assert_eq!(out.status.code(), Some(1));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("A.PRG: not ported to Rust yet") && err.contains("B.PRG: "), "{err}");
}

#[test]
fn usage_errors() {
    assert_eq!(alphacam().output().unwrap().status.code(), Some(2)); // argparse: a command is required
    assert_eq!(alphacam().arg("dump").output().unwrap().status.code(), Some(2)); // files: nargs="+"
}
