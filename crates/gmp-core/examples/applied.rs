//! Read an `applied.json` the way the cold-revert path does, as JSON, so the
//! Python implementation can be diffed against it.
//!
//! The state file is passed as raw text rather than as parsed JSON, because
//! how each side reacts to a file it cannot use is the most important thing
//! this pair has to agree on.
//!
//!     echo '{"raw": "{}", "path": "/x/applied.json"}' \
//!         | cargo run -p gmp-core --example applied

use std::io::Read;

use gmp_core::applied;

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    // A null `raw` is the file being absent or unreadable - the OSError arm of
    // the Python loader, which no string can stand in for.
    let state = input["raw"].as_str().and_then(applied::parse);
    let path = input["path"].as_str().unwrap_or("/x/applied.json");

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "parsed": state.is_some(),
            "dirty": applied::is_dirty(state.as_ref()),
            "describe": applied::describe(state.as_ref(), path),
            "plan": applied::revert_plan(state.as_ref()),
        }))
        .unwrap()
    );
}
