//! Run a sequence of samples through the throttle assessment and print what
//! fired, as JSON, so the Python engine can be diffed against it.
//!
//! The engine is stateful, so a single sample proves nothing - the whole
//! sequence is the question and the whole list of verdicts is the answer.

use std::io::Read;

use gmp_core::diagnostics::{self, Engine, Sample};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let samples: Vec<Sample> =
        serde_json::from_value(input["samples"].clone()).expect("samples must be a list");

    // The two rate readings, which nothing else in this example reaches.
    let rate_pair = |row: &serde_json::Value, key: &str| {
        row.get(key)
            .and_then(|v| Some((v[0].as_f64()?, v[1].as_i64()?)))
    };
    let rates: Vec<serde_json::Value> = input["rates"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let prev = rate_pair(row, "prev");
                    let now = row["now"].as_f64().unwrap_or(0.0);
                    let value = row["value"].as_i64().unwrap_or(0);
                    serde_json::json!([
                        diagnostics::rate_mbps(prev, now, value),
                        diagnostics::package_power(prev, now, value),
                    ])
                })
                .collect()
        })
        .unwrap_or_default();

    let mut engine = Engine::default();
    let verdicts: Vec<serde_json::Value> = samples
        .iter()
        .map(|s| match engine.assess(s) {
            Some((kind, detail)) => serde_json::json!([kind, detail]),
            None => serde_json::Value::Null,
        })
        .collect();

    // The issues each sample sees, from a second engine, so the per-sample
    // detection is diffable independently of the episode machine on top of it.
    let mut issues_engine = Engine::default();
    let issues: Vec<Vec<(String, String)>> = samples
        .iter()
        .map(|s| issues_engine.current_issues(s))
        .collect();

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "verdicts": verdicts,
            "issues": issues,
            "reasons": samples.iter()
                .map(|s| diagnostics::parse_gpu_reasons(&s.gpu_throttle_reasons).to_string())
                .collect::<Vec<_>>(),
            "rates": rates,
        }))
        .unwrap()
    );
}
