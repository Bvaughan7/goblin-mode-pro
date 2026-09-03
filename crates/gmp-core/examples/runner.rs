//! Answer the launch questions for one argv against one config, as JSON, so
//! the Python runner can be diffed against it.

use std::io::Read;

use gmp_core::{config, runner};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let settings = config::from_value(&input["settings"]);
    let argv: Vec<String> =
        serde_json::from_value(input["argv"].clone()).expect("argv must be a list");
    let mangohud_dir = input["mangohud_dir"].as_str().unwrap_or("/tmp/mangohud");

    let matched = runner::resolve_profile_for_argv(&argv, &settings);

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "profile": matched.map(|p| p.exe.clone()),
            "display_name": matched.map(|p| p.display_name.clone()),
            "env": runner::print_env_for(&argv, &settings, mangohud_dir),
            "gamescope": runner::print_gamescope(&argv, &settings),
            "gamemode": runner::print_gamemode(&argv, &settings),
            "gamescope_args": matched.map(runner::gamescope_args),
            "session": runner::gamescope_session_argv(matched, None),
            "basenames": argv.iter().map(|a| runner::basename(a)).collect::<Vec<_>>(),
        }))
        .unwrap()
    );
}
