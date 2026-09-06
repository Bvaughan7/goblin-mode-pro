//! What one frame-rate event implies, as JSON, so the Python
//! `Daemon._on_fps_event` can be diffed against it.
//!
//!     echo '{"kind": "recovered", "fps": 143.7, "duration_s": 12.4}' \
//!         | cargo run -p gmp-core --example fpsevent
//!
//! A dip also prints the window it is classified against, as the first entry,
//! because that is the shape the Python can be recorded in: the daemon reads
//! the recent samples and hands the three numbers to `describe_dip`, so
//! `describe_dip`'s arguments are where the window becomes observable.

use std::io::Read;

use gmp_core::diagnostics::{self, Sample};
use gmp_core::fpswatch::{self, FpsOutcome, FpsStep};

fn wire(step: &FpsStep) -> serde_json::Value {
    let (name, args): (&str, Vec<serde_json::Value>) = match step {
        FpsStep::RememberDip => ("RememberDip", vec![]),
        FpsStep::Raise { kind, detail } => {
            ("Raise", vec![kind.clone().into(), detail.clone().into()])
        }
    };
    serde_json::json!([name, serde_json::Value::Array(args)])
}

fn number(value: Option<f64>) -> serde_json::Value {
    match value {
        Some(v) => serde_json::json!(v),
        None => serde_json::Value::Null,
    }
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let mut out: Vec<serde_json::Value> = Vec::new();
    let outcome = if input["kind"] == "recovered" {
        FpsOutcome::Recovered {
            fps: input["fps"].as_f64().unwrap_or(0.0),
            duration_s: input["duration_s"].as_f64().unwrap_or(0.0),
        }
    } else {
        let samples: Vec<Sample> = serde_json::from_value(
            input
                .get("samples")
                .cloned()
                .unwrap_or(serde_json::json!([])),
        )
        .expect("bad samples");
        let context = diagnostics::dip_context(&samples);
        out.push(serde_json::json!([
            "DipContext",
            [
                number(context.cpu_load),
                number(context.cpu_core_max),
                number(context.disk_read),
            ]
        ]));
        FpsOutcome::Dip {
            detail: input["detail"].as_str().unwrap_or("").to_string(),
            real: input["real"].as_bool().unwrap_or(false),
        }
    };
    out.extend(fpswatch::fps_event_plan(&outcome).iter().map(wire));
    println!("{}", serde_json::to_string_pretty(&out).unwrap());
}
