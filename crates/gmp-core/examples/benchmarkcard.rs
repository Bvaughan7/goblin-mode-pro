//! The comparison table for two benchmark runs, as JSON, so `diff_sessions`
//! can be diffed against it.
//!
//!     echo '{"pairs": [{"a": {...}, "b": {...}}]}' \
//!         | cargo run -p gmp-core --example benchmarkcard

use std::io::Read;

use gmp_core::benchmarkcard;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let out: Vec<serde_json::Value> = input["pairs"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| serde_json::json!(benchmarkcard::diff_sessions(&row["a"], &row["b"])))
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
