//! Render a set of selftest results and print both forms, as JSON, so the
//! Python reporting can be diffed against it.

use std::io::Read;

use gmp_cli::selftest::{self, Result};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let results: Vec<Result> =
        serde_json::from_value(input["results"].clone()).expect("results must be a list");
    let apply = input["apply"].as_bool().unwrap_or(false);
    let machine = input["machine"].clone();
    let version = input["version"].as_str().unwrap_or("0.0.0");

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "plain": selftest::render(&results, apply, false, &machine),
            "colored": selftest::render(&results, apply, true, &machine),
            "json": selftest::to_json(&results, apply, version, &machine),
            "failures": input["failures"].as_array().map(|cases| cases
                .iter()
                .map(|case| selftest::explain_call_failure(
                    case["type"].as_str().unwrap_or(""),
                    case["text"].as_str().unwrap_or(""),
                    case["method"].as_str().unwrap_or(""),
                ))
                .collect::<Vec<_>>()),
            "caps": input["masks"].as_array().map(|masks| masks
                .iter()
                .map(|m| selftest::decode_caps(m.as_u64().unwrap_or(0)))
                .collect::<Vec<_>>()),
            "watts": input["uw"].as_array().map(|values| values
                .iter()
                .map(|v| selftest::watts(v.as_i64()))
                .collect::<Vec<_>>()),
        }))
        .unwrap()
    );
}
