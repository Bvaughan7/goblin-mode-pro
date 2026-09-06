//! What the two switches that are not a game imply, as JSON, so the Python
//! `Daemon.set_master_enabled` and `Daemon.force_boost` can be diffed against
//! them.
//!
//!     echo '{"which": "master", "on": false, "forced_boost": true}' \
//!         | cargo run -p gmp-core --example switches
//!
//! `{"which": "profile"}` prints the profile force-boost applies instead, so
//! that the comparison covers what is applied as well as when.

use std::io::Read;

use gmp_core::payload::{self, SwitchStep};

fn wire(step: &SwitchStep) -> serde_json::Value {
    let name = match step {
        SwitchStep::RevertAll => "RevertAll",
        SwitchStep::ForgetEveryPid => "ForgetEveryPid",
        SwitchStep::StopDiagnostics => "StopDiagnostics",
        SwitchStep::ApplyForced => "ApplyForced",
        SwitchStep::EnsureDiagnostics => "EnsureDiagnostics",
        SwitchStep::RevertForced => "RevertForced",
        SwitchStep::BroadcastStatus => "BroadcastStatus",
    };
    serde_json::json!(name)
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let flag = |key: &str| input[key].as_bool().unwrap_or(false);

    if input["which"] == "profile" {
        let profile = payload::forced_profile();
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::to_value(&profile).unwrap()).unwrap()
        );
        return;
    }
    let plan = if input["which"] == "master" {
        payload::master_plan(flag("on"), flag("forced_boost"))
    } else {
        payload::force_boost_plan(flag("on"), flag("games_running"))
    };
    let wired: Vec<serde_json::Value> = plan.iter().map(wire).collect();
    println!("{}", serde_json::to_string_pretty(&wired).unwrap());
}
