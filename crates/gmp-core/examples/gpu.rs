//! Answer the GPU judgement questions for a fixture snapshot, as JSON, so the
//! Python implementation can be diffed against it.
//!
//! describe_dip MUTATES the state it is given, so the state after the call is
//! part of the answer and is returned too.

use std::io::Read;

use gmp_core::gpu;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let state: gpu::State = input["state"].as_object().cloned().unwrap_or_default();
    let cpu_load = input["cpu_load"].as_f64();
    let disk_read = input["disk_read"].as_f64();
    let cpu_core_max = input["cpu_core_max"].as_f64();
    let under_load = input["under_load"].as_bool().unwrap_or(true);

    let mut described = state.clone();
    let (detail, is_real) = gpu::describe_dip(
        &mut described,
        input["fps"].as_f64().unwrap_or(0.0),
        input["baseline"].as_f64().unwrap_or(0.0),
        cpu_load,
        disk_read,
        cpu_core_max,
    );

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "classify_dip": gpu::classify_dip(&state, cpu_load, disk_read),
            "assess": gpu::assess(&state, under_load),
            "describe_detail": detail,
            "describe_is_real": is_real,
            "state_after": described,
            "post_mortem": gpu::post_mortem(&state),
        }))
        .unwrap()
    );
}
