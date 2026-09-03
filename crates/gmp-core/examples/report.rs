//! Render one report every way the CLI and GUI can, as JSON, so the Python
//! implementation can be diffed against it.
//!
//! `profile_json` is passed in rather than produced here: the "works for me"
//! note embeds `json.dumps(..., indent=2)` output, and matching CPython's JSON
//! writer byte for byte is a separate problem from rendering the document.
//!
//!     echo '{"rep": {...}, "profile_json": "{}", "repo": "o/r"}' \
//!         | cargo run -p gmp-core --example report

use std::io::Read;

use gmp_core::report;

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    let rep = &input["rep"];
    let profile_json = input["profile_json"].as_str().unwrap_or("{}");
    let repo = input["repo"]
        .as_str()
        .unwrap_or("Bvaughan7/goblin-mode-pro");

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "markdown": report::as_markdown(rep),
            "works_for_me": report::works_for_me_markdown(rep, profile_json),
            "works_for_me_url": report::works_for_me_issue_url(rep, profile_json, repo),
            "issue_url": report::github_issue_url(rep, repo),
        }))
        .unwrap()
    );
}
