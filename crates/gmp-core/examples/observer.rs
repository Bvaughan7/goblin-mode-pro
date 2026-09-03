//! Answer the observer's questions about one process table against one config,
//! as JSON, so the Python observer can be diffed against it.
//!
//!     echo '{"settings": {...}, "procs": [{"pid": 1, "name": "Wow.exe", ...}]}' \
//!         | cargo run -p gmp-core --example observer

use std::io::Read;

use gmp_core::{config, observer};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let settings = config::from_value(&input["settings"]);
    let procs: Vec<observer::Process> =
        serde_json::from_value(input["procs"].clone()).expect("procs must be a list");

    // Per profile: everything it matches, and the one PID the daemon would
    // act on. The full match list is reported as well as the winner, because
    // a wrong winner and a wrong match set are different bugs and the diff
    // should say which one it found.
    let per_profile: Vec<serde_json::Value> = settings
        .enabled_profiles()
        .into_iter()
        .map(|profile| {
            serde_json::json!({
                "exe": profile.exe,
                "display_name": profile.display_name,
                "matched": procs.iter()
                    .filter(|p| observer::matches(profile, &p.name, &p.exe, &p.cmdline))
                    .map(|p| p.pid)
                    .collect::<Vec<_>>(),
                "pid": observer::pick_pid(profile, &procs),
            })
        })
        .collect();

    let names: Vec<serde_json::Value> = procs
        .iter()
        .map(|p| {
            serde_json::json!({
                "pid": p.pid,
                "names": observer::candidate_names(&p.name, &p.exe, &p.cmdline),
            })
        })
        .collect();

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "profiles": per_profile,
            "candidate_names": names,
            // Reported rather than left for the test to read out of the
            // source: it is a hand-maintained constant on both sides, so the
            // comparison has to go through the value the code actually uses.
            "wine_infra": observer::WINE_INFRA,
        }))
        .unwrap()
    );
}
