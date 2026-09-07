//! The probes that read the environment, `$PATH` and the odder corners of
//! sysfs, as JSON, so the Python can be diffed against them.
//!
//!     echo '{"cases": [{"release": "6.1.0-lts", "env": {}, "have": []}]}' \
//!         | cargo run -p gmp-core --example capabilities_env

use std::io::Read;
use std::path::Path;

use gmp_core::capabilities;
use serde_json::{json, Value};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: Value = serde_json::from_str(&raw).expect("input must be JSON");

    let out: Vec<Value> = input["cases"]
        .as_array()
        .map(|cases| {
            cases
                .iter()
                .map(|case| {
                    // `$PATH` and the environment are handed in as data, so a
                    // case can describe a machine that has gamescope and no
                    // MangoHud without either being installed here.
                    let env = |name: &str| case["env"][name].as_str().unwrap_or("").to_string();
                    let have = |tool: &str| {
                        case["have"]
                            .as_array()
                            .is_some_and(|v| v.iter().any(|t| t.as_str() == Some(tool)))
                    };
                    json!({
                        "kernel_flavor": capabilities::kernel_flavor(
                            case["release"].as_str().unwrap_or("")),
                        "compositor": capabilities::compositor(&env),
                        "package_manager": capabilities::package_manager(&have),
                        "session_recorder": capabilities::session_recorder(&have),
                        "fan_control": capabilities::has_writable_pwm(
                            Path::new(case["hwmon_root"].as_str().unwrap_or(""))),
                        "on_ac_power": capabilities::on_ac_power(
                            Path::new(case["supply_root"].as_str().unwrap_or(""))),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
