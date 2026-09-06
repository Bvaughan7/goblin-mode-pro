//! What raising an incident implies, as JSON, so the Python
//! `Daemon._raise_incident` can be diffed against it.
//!
//!     echo '{"kind": "gpu_fault", "detail": "...", "clip_running": true,
//!            "active_exes": [], "pids": []}' \
//!         | cargo run -p gmp-core --example raise
//!
//! The `File` step carries the two fields the daemon fills in on the way past:
//! what was running, and which pid the incident is filed against. That is so
//! the comparison covers `game_label` and `game_pid` as well as the plan. They
//! are not part of the step in the library, where filing takes a whole
//! incident.

use std::io::Read;

use gmp_core::incidents::{self, RaiseStep};

fn wire(step: &RaiseStep, active_exes: &[String], pids: &[i64]) -> serde_json::Value {
    let (name, args): (&str, Vec<serde_json::Value>) = match step {
        RaiseStep::File => (
            "File",
            vec![
                incidents::game_label(active_exes).into(),
                match incidents::game_pid(pids) {
                    Some(pid) => pid.into(),
                    None => serde_json::Value::Null,
                },
            ],
        ),
        RaiseStep::Emit => ("Emit", vec![]),
        RaiseStep::SaveClip { kind } => ("SaveClip", vec![kind.clone().into()]),
        RaiseStep::Notify {
            title,
            body,
            urgency,
            tag,
        } => (
            "Notify",
            vec![
                title.clone().into(),
                body.clone().into(),
                (*urgency).into(),
                tag.clone().into(),
            ],
        ),
    };
    serde_json::json!([name, serde_json::Value::Array(args)])
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
    let active_exes = strings(&input["active_exes"]);
    let pids: Vec<i64> = input["pids"]
        .as_array()
        .map(|rows| rows.iter().filter_map(|row| row.as_i64()).collect())
        .unwrap_or_default();

    let plan = incidents::raise_plan(
        input["kind"].as_str().unwrap_or(""),
        input["detail"].as_str().unwrap_or(""),
        input["clip_running"].as_bool().unwrap_or(false),
    );
    let wired: Vec<serde_json::Value> = plan
        .iter()
        .map(|step| wire(step, &active_exes, &pids))
        .collect();
    println!("{}", serde_json::to_string_pretty(&wired).unwrap());
}
