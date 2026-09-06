//! What a batch of saved profile edits makes the daemon redo, as JSON, so the
//! Python `Daemon._flush_profiles` can be diffed against it.
//!
//!     echo '{"settings": {...}, "dirty": ["Wow.exe"], "active": []}' \
//!         | cargo run -p gmp-core --example flushplan

use std::io::Read;

use gmp_core::config::{self, FlushStep};

fn wire(step: &FlushStep) -> serde_json::Value {
    let (name, exe) = match step {
        FlushStep::RetuneWatcher { exe } => ("RetuneWatcher", exe),
        FlushStep::WriteMangoHud { exe } => ("WriteMangoHud", exe),
        FlushStep::Reapply { exe } => ("Reapply", exe),
    };
    serde_json::json!([name, [exe]])
}

fn strings(value: &serde_json::Value) -> Vec<String> {
    value
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| row.as_str().unwrap_or("").to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let settings = config::from_value(&input["settings"]);
    let plan = config::flush_plan(
        &settings,
        &strings(&input["dirty"]),
        &strings(&input["active"]),
    );
    let wired: Vec<serde_json::Value> = plan.iter().map(wire).collect();
    println!("{}", serde_json::to_string_pretty(&wired).unwrap());
}
