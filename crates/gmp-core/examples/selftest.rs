//! What the self-test makes of what the system tells it, as JSON, so the
//! Python can be diffed against it.
//!
//!     echo '{"masks": [8388608], "statuses": [...], "pkchecks": [...],
//!            "watts": [...], "ryzenadj": ["..."]}' \
//!         | cargo run -p gmp-core --example selftest

use std::io::Read;

use gmp_core::selftest;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let list = |key: &str| input[key].as_array().cloned().unwrap_or_default();

    let caps: Vec<serde_json::Value> = list("masks")
        .iter()
        .map(|m| serde_json::json!(selftest::decode_caps(m.as_u64().unwrap_or(0))))
        .collect();

    let sets: Vec<serde_json::Value> = list("statuses")
        .iter()
        .map(|row| {
            match selftest::read_cap_set(
                row["status"].as_str().unwrap_or(""),
                row["field"].as_str().unwrap_or(""),
            ) {
                Some(value) => serde_json::json!(value),
                None => serde_json::Value::Null,
            }
        })
        .collect();

    let checks: Vec<serde_json::Value> = list("pkchecks")
        .iter()
        .map(|row| {
            let answer = selftest::pkcheck_answer(
                row["installed"].as_bool().unwrap_or(true),
                row["code"].as_i64().unwrap_or(0) as i32,
                row["output"].as_str().unwrap_or(""),
            );
            serde_json::json!([answer.state, answer.detail])
        })
        .collect();

    let watts: Vec<serde_json::Value> = list("watts")
        .iter()
        .map(|v| serde_json::json!(selftest::watts(v.as_i64())))
        .collect();

    let stapm: Vec<serde_json::Value> = list("ryzenadj")
        .iter()
        .map(
            |v| match selftest::ryzenadj_stapm(v.as_str().unwrap_or("")) {
                Some(w) => serde_json::json!(w),
                None => serde_json::Value::Null,
            },
        )
        .collect();

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "caps": caps, "sets": sets, "pkchecks": checks,
            "watts": watts, "ryzenadj": stapm,
        }))
        .unwrap()
    );
}
