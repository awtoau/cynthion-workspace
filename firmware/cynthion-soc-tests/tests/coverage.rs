//! Nothing may have a `mod tests` that this crate does not include.
//!
//! The failure it guards is the one #337 is about: a test module that exists,
//! reads as coverage, and executes nowhere. `cargo test` passing says nothing
//! about how many modules it reached, so the count is checked against the
//! source tree rather than against a number someone remembers to update.

use std::fs;
use std::path::{Path, PathBuf};

/// Every `.rs` under `cynthion-soc/src`, recursively.
fn sources(dir: &Path, found: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(dir).expect("cynthion-soc/src is not where it should be") {
        let path = entry.expect("unreadable directory entry").path();
        if path.is_dir() {
            sources(&path, found);
        } else if path.extension().is_some_and(|e| e == "rs") {
            found.push(path);
        }
    }
}

#[test]
fn every_test_module_in_the_firmware_is_included_here() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let src = root.join("../cynthion-soc/src");
    let lib = fs::read_to_string(root.join("src/lib.rs")).expect("no src/lib.rs");

    let mut files = Vec::new();
    sources(&src, &mut files);
    files.sort();
    assert!(files.len() > 40, "only {} sources found -- wrong path?", files.len());

    let mut missing = Vec::new();
    for file in &files {
        let text = fs::read_to_string(file).expect("unreadable source");
        if !text.contains("#[cfg(test)]") {
            continue;
        }
        // The `#[path]` attributes are written relative to this crate's `src/`.
        let relative = file.strip_prefix(&src).expect("not under src");
        let attribute = format!("#[path = \"../../cynthion-soc/src/{}\"]", relative.display());
        if !lib.contains(&attribute) {
            missing.push(attribute);
        }
    }
    assert!(
        missing.is_empty(),
        "test modules that would never run -- add to src/lib.rs:\n{}",
        missing.join("\n")
    );
}
