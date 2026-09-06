//! The plan a game's exit implies, as JSON, so the Python `_on_game_event`
//! can be diffed against it.
//!
//!     echo '{"exe": "Wow.exe", "game": "WoW", "state": {...}}' \
//!         | cargo run -p gmp-core --example gameevent
//!
//! Each step is rendered as its name and its arguments in order, rather than
//! as an object per variant. The Python is recorded at the seam where it acts:
//! the call it makes on the payload, the timer it arms with its delay, the
//! notification it sends. A plan whose steps carried their arguments under
//! different names could not be compared against that at all.

use std::io::Read;

use gmp_core::gameevent::{self, ExitState, ExitStep};

fn wire(step: &ExitStep) -> serde_json::Value {
    let (name, args): (&str, Vec<serde_json::Value>) = match step {
        ExitStep::ForgetPid { exe } => ("ForgetPid", vec![exe.clone().into()]),
        ExitStep::Revert { exe } => ("Revert", vec![exe.clone().into()]),
        ExitStep::FinishSessionIn { seconds, exe, game } => (
            "FinishSessionIn",
            vec![(*seconds).into(), exe.clone().into(), game.clone().into()],
        ),
        ExitStep::FpsPostMortemIn { seconds } => ("FpsPostMortemIn", vec![(*seconds).into()]),
        ExitStep::StopDiagnostics => ("StopDiagnostics", vec![]),
        ExitStep::StopClip => ("StopClip", vec![]),
        ExitStep::AnnounceBoostOff => ("AnnounceBoostOff", vec![]),
        ExitStep::BroadcastStatus => ("BroadcastStatus", vec![]),
    };
    serde_json::json!([name, serde_json::Value::Array(args)])
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let flag = |key: &str| input["state"][key].as_bool().unwrap_or(false);
    let state = ExitState {
        others_running: flag("others_running"),
        fps_dip_seen: flag("fps_dip_seen"),
        gpu_available: flag("gpu_available"),
        forced_boost: flag("forced_boost"),
        clip_running: flag("clip_running"),
        boost_announced: flag("boost_announced"),
    };
    let plan = gameevent::exit_plan(
        input["exe"].as_str().unwrap_or(""),
        input["game"].as_str().unwrap_or(""),
        &state,
    );
    let wired: Vec<serde_json::Value> = plan.iter().map(wire).collect();
    println!("{}", serde_json::to_string_pretty(&wired).unwrap());
}
