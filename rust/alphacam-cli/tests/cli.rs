//! The command line against the Python app's (`python -m alphacam`): the same exit status, stdout, stderr and
//! written files for every case tools/export_rust_cli_fixtures.py recorded, run from the repository root.

use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;

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
fn same_as_the_python_cli() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/cases.json");
    let cases: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = cases.as_object().unwrap();
    assert!(cases.len() >= 9);
    for (name, case) in cases {
        let scratch = std::env::temp_dir().join(format!("alphacam-cli-test-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&scratch).unwrap();
        let out_dir = scratch.to_str().unwrap();
        let args: Vec<String> =
            case["args"].as_array().unwrap().iter().map(|a| a.as_str().unwrap().replace("{out}", out_dir)).collect();
        let out = alphacam().args(&args).current_dir(&root).output().unwrap();
        assert_eq!(out.status.code(), case["status"].as_i64().map(|s| s as i32), "{name}");
        assert_eq!(String::from_utf8_lossy(&out.stdout), case["stdout"].as_str().unwrap(), "{name}: stdout");
        assert_eq!(String::from_utf8_lossy(&out.stderr), case["stderr"].as_str().unwrap(), "{name}: stderr");
        let mut written: Vec<String> =
            std::fs::read_dir(&scratch).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
        written.sort();
        let expected = case["files"].as_object().unwrap();
        assert_eq!(written, expected.keys().cloned().collect::<Vec<_>>(), "{name}: files");
        for (file, text) in expected {
            let got = std::fs::read(scratch.join(file)).unwrap();
            assert_eq!(String::from_utf8_lossy(&got), text.as_str().unwrap(), "{name}: {file}");
        }
        std::fs::remove_dir_all(&scratch).unwrap();
    }
}

#[test]
fn usage_errors() {
    assert_eq!(alphacam().output().unwrap().status.code(), Some(2)); // argparse: a command is required
    assert_eq!(alphacam().arg("dump").output().unwrap().status.code(), Some(2)); // files: nargs="+"
}
