//! What the helper contributes to a status, as JSON, so `PerformancePayload.status`
//! can be diffed against it.
//!
//!     echo '{"replies": [{"kind": "answered", "governor": "performance",
//!                         "power_limits_uw": [45000000, 60000000]}]}' \
//!         | cargo run -p gmp-core --example statusreads

use std::io::Read;

use gmp_core::payload::{self, HelperReply};

fn reply_from(value: &serde_json::Value) -> HelperReply {
    let governor = value["governor"].as_str().map(str::to_string);
    match value["kind"].as_str().unwrap_or("") {
        "answered" => HelperReply::Answered {
            governor,
            power_limits_uw: (
                value["power_limits_uw"][0].as_i64().unwrap_or(0),
                value["power_limits_uw"][1].as_i64().unwrap_or(0),
            ),
        },
        "failed" => HelperReply::Failed { governor },
        _ => HelperReply::Unavailable,
    }
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let answers: Vec<serde_json::Value> = input["replies"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let reads = payload::status_reads(&reply_from(row));
                    serde_json::json!({
                        "governor": reads.governor,
                        "power_limits_w": reads.power_limits_w.map(|(a, b)| vec![a, b]),
                        "helper_available": reads.helper_available,
                        "limited_mode": reads.limited_mode,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&answers).unwrap());
}
