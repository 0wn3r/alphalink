//! .alcam files: what the Python app wrote loads, and saves again byte for byte.

use alphacam::model::Project;
use std::path::PathBuf;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/alcam")
}

#[test]
fn examples_round_trip() {
    let mut n = 0;
    for entry in std::fs::read_dir(repo().join("app/examples")).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|e| e == "alcam") {
            let text = std::fs::read_to_string(&path).unwrap();
            let p = Project::loads(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
            // what the Python app writes for it now (older files lack newer keys)
            let expected = std::fs::read_to_string(fixtures().join(path.file_name().unwrap())).unwrap();
            assert_eq!(p.dumps(), expected, "{}", path.display());
            n += 1;
        }
    }
    assert!(n >= 9);
}

#[test]
fn rejects_other_files() {
    assert!(Project::loads(r#"{"format": "x"}"#).is_err());
    assert!(Project::loads(r#"{"format": "alcam/1", "units": "cm"}"#).is_err());
    assert!(Project::loads(r#"{"format": "alcam/1", "geometry": [{"type": "line", "a": [0, 0], "b": [1, 1], "q": 1}]}"#).is_err());
    let p = Project::loads(r#"{"format": "alcam/1", "operations": [{"type": "thread", "tool": 3, "start": [120, 5],
        "outer_line": "g0", "inner_line": "g1", "pitch": 1.5, "angle": 60.0, "spring_pass": true}]}"#).unwrap();
    match &p.operations[0] {
        alphacam::model::Operation::Thread(t) => assert_eq!((t.angle, t.spring_passes), (0.0, 1)),
        _ => panic!(),
    }
}
