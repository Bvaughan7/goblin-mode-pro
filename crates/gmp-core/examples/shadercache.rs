//! What a shader-cache pre-warm decides, as JSON, so the Python can be diffed
//! against it.
//!
//!     echo '{"cases": [{"app_id": "1091500", "tool": "/usr/bin/x",
//!                       "archives": ["/a.foz"]}]}' \
//!         | cargo run -p gmp-core --example shadercache

use std::io::Read;

use gmp_core::shadercache::{self, Prewarm};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let out: Vec<serde_json::Value> = input["cases"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    let archives: Vec<String> = row["archives"]
                        .as_array()
                        .map(|names| {
                            names
                                .iter()
                                .map(|n| n.as_str().unwrap_or("").to_string())
                                .collect()
                        })
                        .unwrap_or_default();
                    let plan = shadercache::prewarm(
                        row["app_id"].as_str().unwrap_or(""),
                        row["tool"].as_str(),
                        &archives,
                    );
                    // What the caller ends up reporting: the refusal's words,
                    // or the outcome of the run the case describes.
                    let (ok, message, argv) = match &plan {
                        Prewarm::Run { argv, archives } => {
                            let code = row["exit_code"].as_i64().unwrap_or(0) as i32;
                            if code == 0 {
                                (true, shadercache::ran_message(*archives), argv.clone())
                            } else {
                                (false, shadercache::failed_message(code), argv.clone())
                            }
                        }
                        refusal => (
                            false,
                            refusal.message().unwrap_or_default().to_string(),
                            Vec::new(),
                        ),
                    };
                    serde_json::json!({"ok": ok, "message": message, "argv": argv})
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
