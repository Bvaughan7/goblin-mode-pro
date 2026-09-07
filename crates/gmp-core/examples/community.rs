//! The community-profile checks, as JSON, so the Python can be diffed against
//! them.
//!
//!     echo '{"slugs": ["wow"], "indexes": [[...]], "profiles": [{...}],
//!            "urls": ["https://..."]}' \
//!         | cargo run -p gmp-core --example community

use std::io::Read;

use gmp_core::community;

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

/// An `Err` is rendered as null: the Python raises, and what a caller sees is
/// "no answer" either way.
fn or_null<T: Into<serde_json::Value>>(result: Result<T, String>) -> serde_json::Value {
    result.map_or(serde_json::Value::Null, Into::into)
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let slugs: Vec<serde_json::Value> = strings(&input["slugs"])
        .iter()
        .map(|slug| or_null(community::safe_slug(slug)))
        .collect();
    let urls: Vec<serde_json::Value> = strings(&input["urls"])
        .iter()
        .map(|url| community::allowed_url(url).into())
        .collect();
    let indexes: Vec<serde_json::Value> = input["indexes"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| match community::index_entries(row) {
                    Ok(entries) => serde_json::json!(entries),
                    Err(_) => serde_json::Value::Null,
                })
                .collect()
        })
        .unwrap_or_default();
    let profiles: Vec<serde_json::Value> = input["profiles"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| community::shareable(row).unwrap_or(serde_json::Value::Null))
                .collect()
        })
        .unwrap_or_default();

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "slugs": slugs, "urls": urls, "indexes": indexes, "profiles": profiles,
        }))
        .unwrap()
    );
}
