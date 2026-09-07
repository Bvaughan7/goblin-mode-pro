//! Which files the pruner deletes, as JSON, so `housekeeping.prune` can be
//! diffed against it.
//!
//!     echo '{"files": [{"name": "a.log", "mtime": 1.0, "size": 10}],
//!            "keep_newest": 40, "max_bytes": 500}' \
//!         | cargo run -p gmp-core --example housekeeping

use std::io::Read;

use gmp_core::housekeeping::{self, Entry};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let files: Vec<Entry> = input["files"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| Entry {
                    name: row["name"].as_str().unwrap_or("").to_string(),
                    mtime: row["mtime"].as_f64().unwrap_or(0.0),
                    size: row["size"].as_i64().unwrap_or(0),
                })
                .collect()
        })
        .unwrap_or_default();
    let doomed = housekeeping::to_delete(
        &files,
        input["keep_newest"].as_u64().unwrap_or(40) as usize,
        input["max_bytes"].as_i64().unwrap_or(500 * 1024 * 1024),
    );
    println!("{}", serde_json::to_string(&doomed).unwrap());
}
