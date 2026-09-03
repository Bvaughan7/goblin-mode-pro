//! Print the LLM payload gmp-core builds for an incident, so the Python
//! implementation can be diffed against it byte for byte.
//!
//! The payload is text a user pastes somewhere, so the comparison is on the
//! exact string and not on a parsed structure: key order, indentation and the
//! fence all count.
//!
//!     echo '{"incident": {...}, "system": {...}, "hint": "", "home": "/home/x",
//!            "user": "x"}' | cargo run -p gmp-core --example payload

use std::io::Read;

use gmp_core::{incidents, logrules};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let incident: incidents::Incident =
        serde_json::from_value(input["incident"].clone()).expect("bad incident");
    let system = input
        .get("system")
        .cloned()
        .unwrap_or(serde_json::json!({}));
    let hint = input.get("hint").and_then(|v| v.as_str()).unwrap_or("");
    // Identity is passed in rather than read from the environment so both
    // implementations redact against the same one.
    let home = input.get("home").and_then(|v| v.as_str()).unwrap_or("");
    let user = input.get("user").and_then(|v| v.as_str()).unwrap_or("");

    print!(
        "{}",
        incidents::build_llm_payload(&incident, &system, hint, |line| {
            logrules::redact_as(line, home, user)
        })
    );
}
