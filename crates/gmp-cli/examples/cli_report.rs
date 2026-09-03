//! Render every CLI report for one set of daemon replies, as JSON, so the
//! Python implementation can be diffed against it.
//!
//!     echo '{"status": {...}, "health": {...}, "sessions": [...],
//!            "preflight": [...], "fixes": {...}, "limit": 10}' \
//!         | cargo run -p gmp-cli --example cli_report

use std::io::Read;

use gmp_cli::report;
use serde_json::Value;

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: Value = serde_json::from_str(&buffer).expect("input must be JSON");

    let rows = input["sessions"].as_array().cloned().unwrap_or_default();
    let checks = input["preflight"].as_array().cloned().unwrap_or_default();
    let limit = input["limit"].as_i64().unwrap_or(15);

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": report::status(&input["status"]),
            "health": report::health(&input["health"]),
            "sessions": report::sessions(&rows, limit),
            "preflight": report::preflight(&checks),
            "preflight_fixes": report::preflight_fixes(&input["fixes"]),
            "games": report::games(&input["status"]),
        }))
        .unwrap()
    );
}
