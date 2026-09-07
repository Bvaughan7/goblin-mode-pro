//! The three things the API layer decides for itself, as JSON, so the Python
//! can be diffed against them.
//!
//!     echo '{"sizes": [0], "actives": [...], "rows": [...]}' \
//!         | cargo run -p gmp-core --example daemonapi

use std::io::Read;

use gmp_core::daemon_api;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let list = |key: &str| input[key].as_array().cloned().unwrap_or_default();

    let froms: Vec<serde_json::Value> = list("sizes")
        .iter()
        .map(|s| serde_json::json!(daemon_api::analyse_from(s.as_u64().unwrap_or(0))))
        .collect();

    let appids: Vec<serde_json::Value> = list("actives")
        .iter()
        .map(|row| {
            let active: Vec<String> = row["active"]
                .as_array()
                .map(|names| {
                    names
                        .iter()
                        .map(|n| n.as_str().unwrap_or("").to_string())
                        .collect()
                })
                .unwrap_or_default();
            let profiles = row["appids"].clone();
            let lookup = |exe: &str| {
                profiles
                    .get(exe)
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
            };
            serde_json::json!(daemon_api::appid_for_active(&active, &lookup))
        })
        .collect();

    let incidents: Vec<serde_json::Value> = list("rows")
        .iter()
        .map(daemon_api::incident_from_history)
        .collect();

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "froms": froms, "appids": appids, "incidents": incidents,
        }))
        .unwrap()
    );
}
