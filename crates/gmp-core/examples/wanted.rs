//! What the machine should be doing for a given set of active games, as JSON,
//! so the Python `_recompute_global` can be diffed against it.
//!
//!     echo '{"profiles": [...], "on_battery": false, "tdp_backend": "rapl"}' \
//!         | cargo run -p gmp-core --example wanted

use std::io::Read;

use gmp_core::{config, payload};

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    let settings = config::from_value(&serde_json::json!({"profiles": input["profiles"]}));
    let on_battery = input["on_battery"].as_bool().unwrap_or(false);
    let backend = input["tdp_backend"].as_str();

    let wanted = payload::wanted(&settings.profiles, on_battery, backend);
    let (pl1, pl2) = payload::desired_power_limits_uw(&settings.profiles, on_battery);

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "wanted": wanted,
            "power_limits_uw": [pl1, pl2],
        }))
        .unwrap()
    );
}
