//! Load a config the way the daemon would and print the result, as JSON, so
//! the Python loader can be diffed against it.
//!
//! The comparison that matters for this module is a round trip: read a file,
//! normalise it, write it back. A key that goes missing between those two
//! steps is a setting the user loses.

use std::io::Read;

use gmp_core::config;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let settings = config::from_value(&input);
    let saved = serde_json::to_value(&settings).expect("serialises");

    let env: Vec<Vec<(String, String)>> = settings
        .profiles
        .iter()
        .map(config::GameProfile::env_assignments)
        .collect();

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "saved": saved,
            "env": env,
            "enabled": settings
                .enabled_profiles()
                .iter()
                .map(|p| p.exe.clone())
                .collect::<Vec<_>>(),
        }))
        .unwrap()
    );
}
