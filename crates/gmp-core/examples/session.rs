//! Answer the session questions for a fixture, as JSON, so the Python side can
//! be diffed against it.
//!
//!     echo '{"csv": "...", "q": 0.01, "current_1low": 55,
//!            "current_avg": 100, "prior": [{"fps_1low": 60}]}' \
//!       | cargo run -p gmp-core --example session

use std::io::Read;

use gmp_core::sessions;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let series = sessions::parse_csv(input["csv"].as_str().unwrap_or_default());
    let q = input["q"].as_f64().unwrap_or(0.5);
    let mut sorted = series.fps.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());

    let prior: Vec<sessions::PriorSession> =
        serde_json::from_value(input["prior"].clone()).unwrap_or_default();
    let regression = sessions::detect_regression(
        input["current_1low"].as_f64(),
        input["current_avg"].as_f64(),
        &prior,
    );

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "fps": series.fps,
            "cpu_temp": series.cpu_temp,
            "gpu_temp": series.gpu_temp,
            "frametime_ms": series.frametime_ms,
            "percentile": sessions::percentile(&sorted, q),
            "regression": regression,
            "headline": regression.as_ref().map(|r| r.headline("TestGame")),
        }))
        .unwrap()
    );
}
