//! Run a MangoHud log through the watcher and print the events it produced,
//! as JSON, so the Python watcher can be diffed against it.
//!
//! The input is the log text plus the poll boundaries, because when a poll
//! ends is part of the behaviour: settling the unit and evaluating both happen
//! per poll, not per line.

use std::io::Read;

use gmp_core::fpswatch::{infer_divisor, Watcher};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    // The unit heuristic on its own, so it can be diffed away from the state
    // machine that consumes it.
    if let Some(raw_samples) = input.get("samples") {
        let samples: Vec<(f64, f64)> =
            serde_json::from_value(raw_samples.clone()).expect("samples must be [[delta, fps]]");
        println!(
            "{}",
            serde_json::json!({ "divisor": infer_divisor(&samples) })
        );
        return;
    }

    // Each entry is one poll's worth of lines.
    let polls: Vec<Vec<String>> =
        serde_json::from_value(input["polls"].clone()).expect("polls must be a list of lists");
    let dip_floor = input["dip_floor"].as_f64().unwrap_or(22.0);
    let dip_ratio = input["dip_ratio"].as_f64().unwrap_or(0.5);

    let mut watcher = Watcher::new(dip_floor, dip_ratio);
    let mut events = Vec::new();
    let mut per_poll = Vec::new();
    for poll in &polls {
        for line in poll {
            let line = line.trim();
            if !line.is_empty() {
                watcher.ingest(line);
            }
        }
        let event = watcher.poll_tail();
        per_poll.push(match &event {
            Some(e) => serde_json::to_value(e).unwrap_or(serde_json::Value::Null),
            None => serde_json::Value::Null,
        });
        if let Some(e) = event {
            events.push(e);
        }
    }

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "events": events,
            "per_poll": per_poll,
            "current_fps": watcher.current_fps(),
            "stats": watcher.stats(),
            "unit_div": watcher.unit_div(),
        }))
        .unwrap()
    );
}
