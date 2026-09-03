//! Answer the game-detection questions for a described process, as JSON, so
//! the Python implementation can be diffed against it.

use std::io::Read;

use gmp_core::gamedetect as gd;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let i: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let name = i["name"].as_str().unwrap_or_default();
    let exe = i["exe"].as_str().unwrap_or(name);
    let cmd = i["cmd"].as_str().unwrap_or_default();
    let signals = gd::Signals {
        gpu_load: i["gpu_load"].as_u64().unwrap_or(0) as u8,
        links_game_libs: i["links_game_libs"].as_bool().unwrap_or(false),
        rss_bytes: i["rss_bytes"].as_u64(),
    };
    let steam_app_name = i["steam_app_name"].as_str();
    let scored = gd::score(name, exe, cmd, signals, steam_app_name);

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "win_basename": gd::win_basename(exe),
            "blocked": gd::blocked(name, &gd::win_basename(exe)),
            "steam_appid": gd::steam_appid_from_cmd(cmd),
            "lutris_name": gd::lutris_name_from_cmd(cmd),
            "score": scored.as_ref().map(|s| serde_json::json!([
                s.score, s.source, s.display_name, s.app_id
            ])),
        }))
        .unwrap()
    );
}
