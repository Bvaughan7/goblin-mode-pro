//! What focus mode does on the way in, on the way out, and after a crash -
//! carried out against a world the case describes - so the Python can be
//! diffed against it.
//!
//!     echo '{"cases": [{"which": "enter", "tools": {...}, "state": {...},
//!                       "fails": ["balooctl6"]}]}' \
//!         | cargo run -p gmp-core --example focus
//!
//! The plan is walked rather than printed, because the two stopping rules -
//! first-that-starts and first-that-exists - are only visible in what actually
//! runs. `fails` names the tools that are installed and will not start.

use std::io::Read;

use gmp_core::focus::{self, State, Step, Tools};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let flag = |value: &serde_json::Value, key: &str| value[key].as_bool().unwrap_or(false);

    let out: Vec<serde_json::Value> = input["cases"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let tools = Tools {
                        balooctl6: flag(&row["tools"], "balooctl6"),
                        balooctl: flag(&row["tools"], "balooctl"),
                        tracker3: flag(&row["tools"], "tracker3"),
                    };
                    let state = State {
                        active: flag(&row["state"], "active"),
                        baloo_suspended: flag(&row["state"], "baloo_suspended"),
                        tracker_paused: flag(&row["state"], "tracker_paused"),
                    };
                    let fails: Vec<String> = row["fails"]
                        .as_array()
                        .map(|names| {
                            names
                                .iter()
                                .map(|n| n.as_str().unwrap_or("").to_string())
                                .collect()
                        })
                        .unwrap_or_default();
                    let plan = match row["which"].as_str().unwrap_or("") {
                        "exit" => focus::exit_plan(&tools, &state),
                        "restore" => focus::force_restore_plan(&tools),
                        _ => focus::enter_plan(&tools, &state),
                    };
                    let (ran, after) =
                        focus::carry_out(&plan, &state, &|argv| !fails.contains(&argv[0]));
                    let markers: Vec<&str> = plan
                        .iter()
                        .filter_map(|step| match step {
                            Step::InhibitIdle => Some("inhibit"),
                            Step::UninhibitIdle => Some("uninhibit"),
                            Step::SetKdeDnd(true) => Some("dnd_on"),
                            Step::SetKdeDnd(false) => Some("dnd_off"),
                            _ => None,
                        })
                        .collect();
                    serde_json::json!({
                        "ran": ran,
                        "markers": markers,
                        "baloo_suspended": after.baloo_suspended,
                        "tracker_paused": after.tracker_paused,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
