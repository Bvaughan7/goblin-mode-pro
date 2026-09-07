//! One poll of the Proton log watcher, as JSON, so `LogWatcher.poll` can be
//! diffed against it.
//!
//!     echo '{"content": "...", "pos": 0, "now": 100.0,
//!            "last_hit_at": 0.0, "cooldown": 30.0, "recent": []}' \
//!         | cargo run -p gmp-core --example logwatch
//!
//! The reading is done here from the content the case provides rather than
//! from a file, because the file handling is the caller's and the arithmetic
//! is what is being compared. After the window is chosen the read always runs
//! to the end: what is left is at most the cap either way, and a text-mode
//! read of `cap` characters cannot stop short of `cap` bytes.

use std::io::Read;

use gmp_core::logwatch::{self, MAX_READ};

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let content = input["content"].as_str().unwrap_or("");
    let pos = input["pos"].as_u64().unwrap_or(0);

    let window = logwatch::read_window(content.len() as u64, pos, MAX_READ);
    let mut new = &content[window.seek_to as usize..];
    if window.realign {
        // Drop the fragment the seek landed in the middle of.
        new = match new.find('\n') {
            Some(i) => &new[i + 1..],
            None => "",
        };
    }

    let recent: Vec<String> = input["recent"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|row| row.as_str().unwrap_or("").to_string())
                .collect()
        })
        .unwrap_or_default();
    let out = logwatch::scan(
        new,
        &recent,
        input["last_hit_at"].as_f64().unwrap_or(0.0),
        input["now"].as_f64().unwrap_or(0.0),
        input["cooldown"].as_f64().unwrap_or(30.0),
    );

    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "hit": out.hit.map(|h| serde_json::json!({
                "label": h.label, "line": h.line, "context": h.context,
            })),
            "recent": out.recent,
            "pos": content.len(),
        }))
        .unwrap()
    );
}
