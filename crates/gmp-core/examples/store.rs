//! Read the on-disk formats from text, as JSON, so the Python readers can be
//! diffed against them.
//!
//!     echo '{"jsonl": "{\"exe\":\"a\"}\n", "exe": "a", "limit": 40,
//!            "config": "{}"}' | cargo run -p gmp-core --example store

use std::io::Read;

use gmp_core::store;

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    let jsonl = input["jsonl"].as_str().unwrap_or("");
    let exe = input["exe"].as_str();
    let limit = input["limit"].as_i64().unwrap_or(40);
    let config = input["config"].as_str().unwrap_or("");

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "lines": store::splitlines(jsonl),
            "records": store::parse_jsonl(jsonl),
            "sessions_all": store::sessions_history(jsonl, None, limit),
            "sessions_for_exe": store::sessions_history(jsonl, exe, limit),
            "incidents": store::incidents_history(jsonl, limit),
            "settings": store::settings_from_text(config),
        }))
        .unwrap()
    );
}
