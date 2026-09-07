//! Render the applied-state file the way the Python daemon writes it, so the
//! two can be diffed byte for byte.
//!
//!     echo '{"active": ["Wow.exe"], "governor_applied": true, ...}' \
//!         | cargo run -p gmp-core --example appliedstate

use std::io::Read;

use gmp_core::applied::{self, Record};
use serde_json::{Map, Value};

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| row.as_str().unwrap_or("").to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn optional(value: &Value) -> Option<String> {
    value.as_str().map(str::to_string)
}

fn object(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let flag = |key: &str| input[key].as_bool().unwrap_or(false);
    let record = Record {
        active: strings(&input["active"]),
        governor_applied: flag("governor_applied"),
        power_applied: flag("power_applied"),
        power_backend: optional(&input["power_backend"]),
        tearing_applied: flag("tearing_applied"),
        adaptive_sync_applied: flag("adaptive_sync_applied"),
        refresh_cap_applied: flag("refresh_cap_applied"),
        focus_mode: flag("focus_mode"),
        scx_applied: optional(&input["scx_applied"]),
        scx_previous: optional(&input["scx_previous"]),
        reniced: object(&input["reniced"]),
        compositor: object(&input["compositor"]),
    };
    print!("{}", applied::render(&record));
}
