//! The CPU layout read out of a sysfs tree, as JSON, so the Python can be
//! diffed against it.
//!
//!     echo '{"roots": ["/sys/devices/system/cpu"]}' \
//!         | cargo run -p gmp-core --example cpulayout

use std::io::Read;

use gmp_core::cpulayout;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let out: Vec<serde_json::Value> = input["roots"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let root = std::path::Path::new(row.as_str().unwrap_or(""));
                    serde_json::Value::Object(cpulayout::core_layout(root))
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
