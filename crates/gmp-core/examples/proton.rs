//! What a walk of the compatibility and cache directories means, as JSON, so
//! the Python can be diffed against it.
//!
//!     echo '{"candidates": [...], "caches": [...], "clear": ["..."]}' \
//!         | cargo run -p gmp-core --example proton

use std::io::Read;

use gmp_core::proton::{self, CacheDir, Candidate};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let flag = |row: &serde_json::Value, key: &str| row[key].as_bool().unwrap_or(false);

    let candidates: Vec<Candidate> = input["candidates"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| Candidate {
                    name: row["name"].as_str().unwrap_or("").to_string(),
                    path: row["path"].as_str().unwrap_or("").to_string(),
                    has_proton: flag(row, "has_proton"),
                    has_bin_wine: flag(row, "has_bin_wine"),
                    has_version: flag(row, "has_version"),
                    mtime: row["mtime"].as_f64().unwrap_or(0.0),
                })
                .collect()
        })
        .unwrap_or_default();

    let caches: Vec<CacheDir> = input["caches"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| CacheDir {
                    label: row["label"].as_str().unwrap_or("").to_string(),
                    path: row["path"].as_str().unwrap_or("").to_string(),
                    real: row["real"].as_str().unwrap_or("").to_string(),
                    bytes: row["bytes"].as_i64().unwrap_or(0),
                })
                .collect()
        })
        .unwrap_or_default();

    let listed = proton::shader_caches(&caches);
    let clear: Vec<serde_json::Value> = input["clear"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| proton::is_known_cache(row.as_str().unwrap_or(""), &listed).into())
                .collect()
        })
        .unwrap_or_default();

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "builds": proton::installed_builds(&candidates),
            "caches": listed,
            "clear": clear,
        }))
        .unwrap()
    );
}
