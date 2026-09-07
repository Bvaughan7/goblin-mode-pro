//! The checks and projections the web lookups run on what comes back, as
//! JSON, so the Python can be diffed against them.
//!
//!     echo '{"urls": [...], "appids": [...], "summaries": [...],
//!            "lookups": [...], "digits": "abc123"}' \
//!         | cargo run -p gmp-core --example webdata

use std::io::Read;

use gmp_core::{pyfmt, webdata};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let list = |key: &str| input[key].as_array().cloned().unwrap_or_default();

    let urls: Vec<serde_json::Value> = list("urls")
        .iter()
        .map(|url| webdata::allowed_url(url.as_str().unwrap_or("")).into())
        .collect();

    // Both call sites: the ProtonDB one caps at twelve, the anti-cheat one
    // does not.
    let appids: Vec<serde_json::Value> = list("appids")
        .iter()
        .map(|value| {
            serde_json::json!([
                webdata::steam_appid(value, Some(12)),
                webdata::steam_appid(value, None),
            ])
        })
        .collect();

    let summaries: Vec<serde_json::Value> = list("summaries")
        .iter()
        .map(|data| webdata::protondb_projection(data).unwrap_or(serde_json::Value::Null))
        .collect();

    let lookups: Vec<serde_json::Value> = list("lookups")
        .iter()
        .map(|row| {
            webdata::anticheat_match(
                &row["db"],
                row["name"].as_str().unwrap_or(""),
                row["app_id"].as_str().unwrap_or(""),
            )
            .unwrap_or(serde_json::Value::Null)
        })
        .collect();

    let caches: Vec<serde_json::Value> = list("caches")
        .iter()
        .map(|row| {
            webdata::cache_is_fresh(
                row["mtime"].as_f64().unwrap_or(0.0),
                row["now"].as_f64().unwrap_or(0.0),
                row["ttl"].as_f64().unwrap_or(0.0),
            )
            .into()
        })
        .collect();

    // One character per position, so a disagreement names itself.
    let digits: String = input["digits"]
        .as_str()
        .unwrap_or("")
        .chars()
        .map(|c| if pyfmt::is_digit(c) { '1' } else { '0' })
        .collect();

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "urls": urls, "appids": appids, "summaries": summaries,
            "lookups": lookups, "digits": digits, "caches": caches,
        }))
        .unwrap()
    );
}
