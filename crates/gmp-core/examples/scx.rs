//! Answer the scheduler-name and mode questions for one input, as JSON, so the
//! Python implementation can be diffed against it.

use std::io::Read;

use gmp_core::scx;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let name = input["name"].as_str().unwrap_or("");
    let mode = input["mode"].as_str().unwrap_or("");

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "mode_id": scx::mode_id(mode),
            "short_name": scx::short_name(name),
            "full_name": scx::full_name(name),
            "valid_name": scx::valid_name(name),
        }))
        .unwrap()
    );
}
