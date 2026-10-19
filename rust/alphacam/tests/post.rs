//! The ALPHA-T post against the Python app: every case exported by the retired Python app's exporter (the golden
//! samples, every ALPHA-T sample rebuilt from its .SD1, the examples) must give the same bytes or the same refusal.

use std::path::{Path, PathBuf};

use alphacam::Error;
use alphacam::model::Project;
use alphacam::post::alphat_prg::post;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

#[test]
fn same_programs_as_the_python_app() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/post");
    let manifest: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("manifest.json")).unwrap()).unwrap();
    let mut failures = vec![];
    let cases = manifest.as_object().unwrap();
    for (name, case) in cases {
        let project = Project::load(dir.join(format!("{name}.alcam"))).unwrap();
        let out = post(&project, None, None);
        match (case.get("error").and_then(|e| e.as_str()), out) {
            (Some("NotImplementedError"), Err(Error::NotImplemented(_))) => {}
            (Some("ValueError" | "KeyError"), Err(Error::Value(_))) => {}
            (Some(e), out) => failures.push(format!("{name}: expected {e}, got {out:?}")),
            (None, Err(e)) => failures.push(format!("{name}: {e:?}")),
            (None, Ok(out)) => {
                let expected = std::fs::read(repo().join(case["expected"].as_str().unwrap())).unwrap();
                if out != expected {
                    let (a, b) = (String::from_utf8_lossy(&out), String::from_utf8_lossy(&expected));
                    let first = a.lines().zip(b.lines()).position(|(x, y)| x != y).unwrap_or(0);
                    let near = |s: &str| s.lines().skip(first.saturating_sub(2)).take(5).collect::<Vec<_>>().join(" | ");
                    failures.push(format!("{name}: line {}\n  rust:   {}\n  python: {}", first + 1, near(&a), near(&b)));
                }
            }
        }
    }
    assert!(cases.len() >= 190, "{} cases", cases.len());
    assert!(failures.is_empty(), "{} of {} cases differ:\n{}", failures.len(), cases.len(), failures.join("\n"));
}

/// ALPHA-T's labels: operation blocks N10, N20..., restart points N502, N503... A long program would repeat one, and
/// the control's restart (GOTO[#129*10], searching forward from the top) would start inside an earlier operation, so
/// the post refuses it; the preview names the first operation that brings the repeat.
#[test]
fn a_program_that_would_repeat_a_label_is_refused() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/alcam");
    let mut project = Project::load(dir.join("area_clear.alcam")).unwrap();
    let ops = project.operations.clone();
    while project.operations.len() < 60 {
        project.operations.extend(ops.iter().cloned());
    }
    match post(&project, None, None) {
        Err(Error::Value(m)) => assert!(m.contains("would be used twice"), "{m}"),
        other => panic!("expected a refusal, got {:?}", other.map(|d| d.len())),
    }
    let p = alphacam::preview::preview(&project, None, None).unwrap();
    let at = p.error_op.expect("the preview names the operation");
    assert!(at > 0 && at < 60, "{at}");
    assert!(p.error.unwrap().contains("would be used twice"));
    project.operations.truncate(at);
    let text = String::from_utf8(post(&project, None, None).unwrap()).unwrap();
    let labels: Vec<&str> = text.lines().filter(|l| l.starts_with('N')).collect();
    let unique: std::collections::HashSet<&&str> = labels.iter().collect();
    assert_eq!(unique.len(), labels.len());
}
