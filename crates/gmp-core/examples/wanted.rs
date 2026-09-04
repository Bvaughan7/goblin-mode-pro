//! What the machine should be doing for a given set of active games, as JSON,
//! so the Python `_recompute_global` can be diffed against it.
//!
//!     echo '{"profiles": [...], "on_battery": false, "tdp_backend": "rapl"}' \
//!         | cargo run -p gmp-core --example wanted

use std::io::Read;

use gmp_core::{config, payload};
use payload::HelperStep;

/// One planned call, as the wire sees it: the method's name and its arguments
/// in order.
///
/// The Python is recorded at the same seam - `HelperClient._call`, which takes
/// the D-Bus name and a packed variant - so the two lists are comparable
/// without either side describing its own calls in its own words. Written out
/// here rather than derived, because a plan whose arguments arrived in the
/// wrong order would still serialize into a plausible-looking object.
fn wire(step: &HelperStep) -> serde_json::Value {
    let (name, args) = match step {
        HelperStep::SetGovernor { governor } => ("SetGovernor", vec![governor.clone().into()]),
        HelperStep::SetEpp { epp } => ("SetEPP", vec![epp.clone().into()]),
        HelperStep::ResetPowerLimits => ("ResetPowerLimits", vec![]),
        HelperStep::ResetTdp => ("ResetTDP", vec![]),
        HelperStep::SetPowerLimits { pl1_uw, pl2_uw } => {
            ("SetPowerLimits", vec![(*pl1_uw).into(), (*pl2_uw).into()])
        }
        HelperStep::SetTdp { watts } => ("SetTDP", vec![(*watts).into()]),
        HelperStep::SpinUpFans { percent } => ("SpinUpFans", vec![(*percent).into()]),
        HelperStep::RevertAll => ("RevertAll", vec![]),
    };
    serde_json::json!([name, serde_json::Value::Array(args)])
}

/// What the caller has already applied, as the input describes it.
fn helper_state(input: &serde_json::Value) -> payload::HelperState {
    let state = &input["helper_state"];
    payload::HelperState {
        tweaks_applied: state["tweaks_applied"].as_bool().unwrap_or(false),
        power_applied: state["power_applied"].as_bool().unwrap_or(false),
        power_backend: state["power_backend"].as_str().map(str::to_string),
        power_values: state["power_values"].as_array().map(|pair| {
            (
                pair[0].as_i64().unwrap_or_default(),
                pair[1].as_i64().unwrap_or_default(),
            )
        }),
        fan_spinup_applied: state["fan_spinup_applied"].as_bool().unwrap_or(false),
    }
}

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    let settings = config::from_value(&serde_json::json!({"profiles": input["profiles"]}));
    let on_battery = input["on_battery"].as_bool().unwrap_or(false);
    let backend = input["tdp_backend"].as_str();

    let wanted = payload::wanted(&settings.profiles, on_battery, backend);
    // `applied` is the scheduler already running, or absent for none.
    let applied = input["scx_applied"].as_str();
    let (pl1, pl2) = payload::desired_power_limits_uw(&settings.profiles, on_battery);

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "wanted": wanted,
            "power_limits_uw": [pl1, pl2],
            "scx_action": payload::scx_action(&settings.profiles, applied),
            "helper_calls": payload::helper_plan(&wanted, &helper_state(&input))
                .iter()
                .map(wire)
                .collect::<Vec<_>>(),
        }))
        .unwrap()
    );
}
