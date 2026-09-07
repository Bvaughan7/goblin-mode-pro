//! What one `Notify` call carries, as JSON, so the Python can be diffed
//! against it.
//!
//!     echo '{"cases": [{"title": "t", "body": "b", "replace": true,
//!                       "urgency": 1, "tag": "status", "sent": {}}]}' \
//!         | cargo run -p gmp-core --example notify

use std::collections::HashMap;
use std::io::Read;

use gmp_core::notify;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let out: Vec<serde_json::Value> = input["cases"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let sent: HashMap<String, u32> = row["sent"]
                        .as_object()
                        .map(|map| {
                            map.iter()
                                .filter_map(|(k, v)| v.as_u64().map(|id| (k.clone(), id as u32)))
                                .collect()
                        })
                        .unwrap_or_default();
                    let n = notify::notification(
                        row["title"].as_str().unwrap_or(""),
                        row["body"].as_str().unwrap_or(""),
                        row["replace"].as_bool().unwrap_or(true),
                        row["urgency"].as_i64().unwrap_or(1),
                        row["tag"].as_str().unwrap_or("status"),
                        &sent,
                    );
                    // The tuple the Python packs, in order.
                    serde_json::json!([
                        n.app, n.replaces_id, n.icon, n.title, n.body, n.actions,
                        {"urgency": n.urgency, "desktop-entry": n.desktop_entry},
                        n.timeout_ms,
                    ])
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
