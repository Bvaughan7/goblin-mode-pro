//! Which cpus a pinning mode means, as JSON, so `cpuset.target_cpus` can be
//! diffed against it.
//!
//!     echo '{"cases": [{"mode": "cache0", "layout": {...}}]}' \
//!         | cargo run -p gmp-core --example cpuset

use std::io::Read;

use gmp_core::cpuset;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let answers: Vec<serde_json::Value> = input["cases"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let mode = row["mode"].as_str().unwrap_or("");
                    match cpuset::target_cpus(mode, &row["layout"]) {
                        Some(cpus) => serde_json::json!(cpus),
                        None => serde_json::Value::Null,
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&answers).unwrap());
}
