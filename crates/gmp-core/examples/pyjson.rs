//! Render JSON documents the way CPython's `json.dumps` renders them, so the
//! two can be diffed byte for byte over a corpus neither side chose.
//!
//! Takes every document in one call - a corpus worth trusting is thousands of
//! documents long, and a process per document would make it slow enough that
//! somebody shortens it.
//!
//!     echo '{"documents": [{"indent": 2, "value": {...}}]}' \
//!         | cargo run -p gmp-core --example pyjson

use std::io::Read;

use gmp_core::pyjson;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let rendered: Vec<serde_json::Value> = input["documents"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let indent = row["indent"].as_u64().unwrap_or(0) as usize;
                    pyjson::dumps_indented(&row["value"], indent).into()
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&rendered).unwrap());
}
