//! The Prometheus textfile document, and the float spelling it depends on, so
//! the Python can be diffed against both byte for byte.
//!
//!     echo '{"which": "render", "statuses": [{...}]}' \
//!         | cargo run -p gmp-core --example exporter
//!
//! `{"which": "g", "values": [...]}` formats numbers instead - `%g` is the
//! only reason two implementations of this file could disagree while both
//! looking right.

use std::io::Read;

use gmp_core::exporter;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    if input["which"] == "g" {
        let out: Vec<String> = input["values"]
            .as_array()
            .map(|rows| {
                rows.iter()
                    .map(|row| match row.as_f64() {
                        Some(v) => exporter::format_value(v),
                        None => String::new(),
                    })
                    .collect()
            })
            .unwrap_or_default();
        println!("{}", serde_json::to_string(&out).unwrap());
        return;
    }

    let out: Vec<String> = input["statuses"]
        .as_array()
        .map(|rows| rows.iter().map(exporter::render).collect())
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
