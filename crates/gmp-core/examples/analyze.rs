//! Print what `gmp-core` makes of a log, as JSON, so the Python implementation
//! can be diffed against it.
//!
//! This is the parity mechanism for the domain-logic port, and it is the same
//! idea as `gmp-helper --introspect`: rather than asserting the two agree,
//! give something outside both a way to ask each of them and compare. A test
//! that only checks the Rust side against itself can only ever agree with
//! whatever the Rust side does.
//!
//!     cargo run -p gmp-core --example analyze -- [appid] < some.log

use std::io::Read;

fn main() {
    let appid = std::env::args().nth(1).unwrap_or_default();
    let mut text = String::new();
    std::io::stdin()
        .read_to_string(&mut text)
        .expect("could not read the log from stdin");

    let findings: Vec<serde_json::Value> = gmp_core::logrules::analyze_text(&text, &appid)
        .into_iter()
        .map(|f| {
            serde_json::json!({
                "rule_id": f.rule_id,
                "label": f.label,
                "category": f.category,
                "cause": f.cause,
                "fix": f.fix,
                "severity": f.severity,
                "count": f.count,
                "sample": f.sample,
                "fix_cmd": f.fix_cmd,
            })
        })
        .collect();
    println!("{}", serde_json::to_string_pretty(&findings).unwrap());
}
