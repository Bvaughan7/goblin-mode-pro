//! Everything one recompute implies, as JSON, so the Python
//! `_recompute_global` and `_restore_global` can be diffed against it.
//!
//!     echo '{"which": "recompute", "profiles": [...], "on_battery": false,
//!            "tdp_backend": "rapl", "state": {...}}' \
//!         | cargo run -p gmp-core --example recompute
//!
//! The helper's individual calls are COLLAPSED to one marker here. Their
//! contents and their order are already diffed by the `wanted` example against
//! the same seam the Python records them at; what this compares is the order
//! of the halves relative to each other, and the edge-triggering of the
//! display tweaks, which nothing else covers.

use std::io::Read;

use gmp_core::config::GameProfile;
use gmp_core::payload::{self, Applied, HelperState, RecomputeStep, ScxAction};

fn wire(step: &RecomputeStep) -> serde_json::Value {
    match step {
        // The restore is a different call in the Python, not the same one
        // with nothing in it.
        RecomputeStep::Helper(gmp_core::payload::HelperStep::RevertAll) => {
            serde_json::json!(["HelperRestore", []])
        }
        RecomputeStep::Helper(_) => serde_json::json!(["Helper", []]),
        RecomputeStep::EnableTearing => serde_json::json!(["EnableTearing", []]),
        RecomputeStep::RestoreTearing => serde_json::json!(["RestoreTearing", []]),
        RecomputeStep::EnableAdaptiveSync { outputs } => serde_json::json!([
            "EnableAdaptiveSync",
            [match outputs {
                Some(names) => serde_json::json!(names),
                None => serde_json::Value::Null,
            }]
        ]),
        RecomputeStep::RestoreAdaptiveSync => serde_json::json!(["RestoreAdaptiveSync", []]),
        RecomputeStep::EnableRefreshCap { hz } => {
            serde_json::json!(["EnableRefreshCap", [hz]])
        }
        RecomputeStep::RestoreRefreshCap => serde_json::json!(["RestoreRefreshCap", []]),
        RecomputeStep::Scx(ScxAction::Restore) => serde_json::json!(["ScxRestore", []]),
        RecomputeStep::Scx(ScxAction::Switch {
            scheduler,
            mode,
            remember_previous,
        }) => serde_json::json!(["ScxSwitch", [scheduler, mode, remember_previous]]),
        RecomputeStep::Scx(ScxAction::Nothing) => serde_json::json!(["ScxNothing", []]),
        RecomputeStep::EnterFocus => serde_json::json!(["EnterFocus", []]),
        RecomputeStep::ExitFocus => serde_json::json!(["ExitFocus", []]),
    }
}

/// One `Helper` marker where the Python makes one call into that half, and
/// none where it makes none. The condition is `want_helper`, not "the plan
/// came out non-empty": a fan spin-up that is already in force wants the
/// helper and asks it for nothing.
fn collapse(plan: Vec<RecomputeStep>, want_helper: bool) -> Vec<serde_json::Value> {
    let mut out = Vec::new();
    let mut seen_helper = false;
    for step in &plan {
        if let RecomputeStep::Helper(_) = step {
            if seen_helper {
                continue;
            }
            seen_helper = true;
        }
        out.push(wire(step));
    }
    if want_helper && !seen_helper {
        out.insert(0, serde_json::json!(["Helper", []]));
    }
    out
}

fn state_from(value: &serde_json::Value) -> Applied {
    let flag = |key: &str| value[key].as_bool().unwrap_or(false);
    Applied {
        helper: HelperState {
            tweaks_applied: flag("helper"),
            ..HelperState::default()
        },
        tearing: flag("tearing"),
        adaptive_sync: flag("adaptive_sync"),
        refresh_cap: flag("refresh_cap"),
        scx: value["scx"].as_str().map(str::to_string),
        focus_mode: flag("focus_mode"),
    }
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let profiles: Vec<GameProfile> =
        serde_json::from_value(input["profiles"].clone()).expect("bad profiles");
    let state = state_from(&input["state"]);

    let out = if input["which"] == "restore" {
        payload::restore_plan(&state)
            .iter()
            .map(wire)
            .collect::<Vec<_>>()
    } else {
        let wanted = payload::wanted(
            &profiles,
            input["on_battery"].as_bool().unwrap_or(false),
            input["tdp_backend"].as_str(),
        );
        let plan = payload::recompute_plan(&wanted, &profiles, &state);
        collapse(plan, wanted.helper)
    };
    println!("{}", serde_json::to_string_pretty(&out).unwrap());
}
