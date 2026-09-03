//! Answer the daemon's read-and-report questions, as JSON, so the Python
//! implementation can be diffed against it.
//!
//!     echo '{"results": [...], "tweaks": {...}, "gpu": {...},
//!            "rows": [...], "target": 20}' \
//!         | cargo run -p gmp-core --example status

use std::io::Read;

use gmp_core::status;

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    let results = input["results"].as_array().cloned().unwrap_or_default();
    let rows = input["rows"].as_array().cloned().unwrap_or_default();
    let target = input["target"].as_u64().unwrap_or(20) as usize;

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "health": status::health(&results),
            "fingerprint": status::tweaks_fingerprint(&input["tweaks"]),
            "gpu_summary": status::gpu_summary(&input["gpu"]),
            "downsampled": status::downsample(&rows, target),
        }))
        .unwrap()
    );
}
