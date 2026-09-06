//! The plan a game starting or stopping implies, as JSON, so the Python
//! `_on_game_event` can be diffed against it.
//!
//!     echo '{"kind": "exit", "exe": "Wow.exe", "game": "WoW", "state": {...}}' \
//!         | cargo run -p gmp-core --example gameevent
//!
//! A launch prints the opening and the rest of the plan as ONE list, in the
//! order the daemon carries them out. They are two functions because the
//! fingerprint the second half is drawn from does not exist until the first
//! half has run - which is a fact about the daemon, not about the comparison.
//!
//! Each step is rendered as its name and its arguments in order, rather than
//! as an object per variant. The Python is recorded at the seam where it acts:
//! the call it makes on the payload, the timer it arms with its delay, the
//! notification it sends. A plan whose steps carried their arguments under
//! different names could not be compared against that at all.

use std::io::Read;

use gmp_core::gameevent::{self, ExitState, ExitStep, Launch, LaunchStep};

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

fn wire_launch(step: &LaunchStep) -> serde_json::Value {
    let (name, args): (&str, Vec<serde_json::Value>) = match step {
        LaunchStep::RememberPid { exe, pid } => {
            ("RememberPid", vec![exe.clone().into(), (*pid).into()])
        }
        LaunchStep::Apply { exe, pid } => ("Apply", vec![exe.clone().into(), (*pid).into()]),
        LaunchStep::EnsureDiagnostics => ("EnsureDiagnostics", vec![]),
        LaunchStep::StartSession { exe, game, tweaks } => (
            "StartSession",
            vec![
                exe.clone().into(),
                game.clone().into(),
                tweaks.clone().into(),
            ],
        ),
        LaunchStep::AnnounceBoostOn { title, body } => (
            "AnnounceBoostOn",
            vec![title.clone().into(), body.clone().into()],
        ),
        LaunchStep::StartClip => ("StartClip", vec![]),
        LaunchStep::PrewarmShaders { steam_app_id } => {
            ("PrewarmShaders", vec![steam_app_id.clone().into()])
        }
        LaunchStep::BroadcastStatus => ("BroadcastStatus", vec![]),
    };
    serde_json::json!([name, serde_json::Value::Array(args)])
}

fn launch(input: &serde_json::Value) -> Vec<serde_json::Value> {
    let game = Launch {
        exe: input["exe"].as_str().unwrap_or("").to_string(),
        display_name: input["game"].as_str().unwrap_or("").to_string(),
        pid: input["pid"].as_i64().unwrap_or(0),
        clip_on_incident: input["clip_on_incident"].as_bool().unwrap_or(false),
        steam_app_id: input["steam_app_id"].as_str().unwrap_or("").to_string(),
    };
    let tweaks: Vec<String> = input["tweaks"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| row.as_str().unwrap_or("").to_string())
                .collect()
        })
        .unwrap_or_default();
    let announced = input["state"]["boost_announced"].as_bool().unwrap_or(false);
    gameevent::launch_opening(&game.exe, game.pid)
        .iter()
        .chain(gameevent::launch_plan(&game, &tweaks, announced).iter())
        .map(wire_launch)
        .collect()
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    if input["kind"] == "launch" {
        println!("{}", serde_json::to_string_pretty(&launch(&input)).unwrap());
        return;
    }
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
