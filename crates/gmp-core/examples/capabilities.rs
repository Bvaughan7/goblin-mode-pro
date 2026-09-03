//! Answer the capabilities questions for a fixture, as JSON, so the Python
//! implementation can be diffed against it.

use std::io::Read;

use gmp_core::capabilities as cap;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let i: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let pkgs: Vec<String> = i["pkgs"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    let pkg_refs: Vec<&str> = pkgs.iter().map(String::as_str).collect();
    let (why, cmd) = cap::kernel_upgrade_tip(i["distro"].as_str().unwrap_or(""));

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "cpu_list": cap::parse_cpu_list(i["cpu_list"].as_str().unwrap_or("")),
            "install_command": cap::install_command(
                i["package_manager"].as_str().unwrap_or(""), &pkg_refs),
            "kernel_tip": [why, cmd],
            "controllers": cap::controllers_from_blob(i["devices"].as_str().unwrap_or("")),
        }))
        .unwrap()
    );
}
