//! The replay buffer's command line, its debounce and its file picking, as
//! JSON, so the Python can be diffed against them.
//!
//!     echo '{"out_dir": "/v", "saves": [...], "picks": [...]}' \
//!         | cargo run -p gmp-core --example clip

use std::io::Read;

use gmp_core::clip::{self, Save};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let saves: Vec<serde_json::Value> = input["saves"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let answer = clip::may_save(
                        row["running"].as_bool().unwrap_or(false),
                        row["last_save"].as_f64(),
                        row["now"].as_f64().unwrap_or(0.0),
                    );
                    serde_json::json!(match answer {
                        Save::NotRunning => "not_running",
                        Save::TooSoon => "too_soon",
                        Save::Flush => "flush",
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let picks: Vec<serde_json::Value> = input["picks"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let before: Vec<&str> = row["before"]
                        .as_array()
                        .map(|names| names.iter().filter_map(|n| n.as_str()).collect())
                        .unwrap_or_default();
                    let after: Vec<(&str, f64)> = row["after"]
                        .as_array()
                        .map(|names| {
                            names
                                .iter()
                                .map(|n| {
                                    (n[0].as_str().unwrap_or(""), n[1].as_f64().unwrap_or(0.0))
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    match clip::saved_file(&before, &after) {
                        Some(name) => serde_json::json!(name),
                        None => serde_json::Value::Null,
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "argv": clip::start_argv(input["out_dir"].as_str().unwrap_or("")),
            "saves": saves,
            "picks": picks,
        }))
        .unwrap()
    );
}
