//! Answer the display-parsing questions for one `kscreen-doctor -o` dump, as
//! JSON, so the Python implementation can be diffed against it.

use std::io::Read;

use gmp_core::compositor;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let stdout = input["stdout"].as_str().unwrap_or("");
    let output = input["output"].as_str().unwrap_or("");
    let hz = input["hz"].as_u64().unwrap_or(0);

    // Rendered the way Python renders it: {name: {"modes": {id: [w,h,hz]},
    // "current": id}}, so the diff is against the same shape.
    let parsed: serde_json::Map<String, serde_json::Value> = compositor::parse_output_modes(stdout)
        .into_iter()
        .map(|(name, out)| {
            let modes: serde_json::Map<String, serde_json::Value> = out
                .modes
                .iter()
                .map(|(id, (w, h, hz))| (id.clone(), serde_json::json!([w, h, hz])))
                .collect();
            (
                name,
                serde_json::json!({"modes": modes, "current": out.current}),
            )
        })
        .collect();

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "parsed": parsed,
            "internal_panel": compositor::internal_panel_output(stdout),
            "vrr": compositor::vrr_outputs(stdout)
                .into_iter()
                .map(|(name, state)| (name, serde_json::Value::from(state)))
                .collect::<serde_json::Map<String, serde_json::Value>>(),
            "plan": compositor::plan_refresh_change(stdout, output, hz),
            "valid_vrr": compositor::valid_vrr_policy(input["policy"].as_str().unwrap_or("")),
        }))
        .unwrap()
    );
}
