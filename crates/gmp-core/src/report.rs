//! Rendering a bug report.
//!
//! `build_report` gathers - it probes the machine, runs the pre-flight checks,
//! reads the newest Wine/Proton log and runs a read-only selftest - and stays
//! in Python. What is here is what comes out the other end: the markdown a
//! person pastes into a thread, and the pre-filled issue links.
//!
//! Those links are the whole "upload" mechanism for this project. There is no
//! server, no account and no telemetry; a report reaches the maintainer only
//! because the user clicks a URL with the body already in it. So the encoding
//! of that URL is not a detail - it is the transport - and it is the one thing
//! in this module that two implementations could plausibly disagree about
//! without anybody noticing until a report arrived mangled.

use serde_json::Value;

use crate::pyfmt::{fields, name, text};

/// The tweaks named in the report's "Active tweaks" section.
///
/// Deliberately the same list, in the same order, as the CLI's status line.
/// They are separate constants because they are separate surfaces that could
/// legitimately diverge, not because they currently do.
pub const TWEAK_KEYS: &[&str] = &[
    "governor",
    "epp_boosted",
    "tearing",
    "adaptive_sync",
    "power_limited",
    "focus_mode",
];

/// GitHub caps an issue URL long before this, but the truncation exists to
/// keep the *link* usable; the full report goes on the clipboard regardless.
const MAX_ISSUE_BODY: usize = 6000;

fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.as_object()?.get(key)
}

fn truthy_field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    field(value, key).filter(|v| crate::config::truthy(v))
}

fn rows<'a>(value: &'a Value, key: &str) -> &'a [Value] {
    match field(value, key) {
        Some(Value::Array(items)) => items,
        _ => &[],
    }
}

/// `urllib.parse.quote_plus`.
///
/// Written out rather than taken from a crate because the exact rule is what
/// matters and the popular crates each differ from Python somewhere: the
/// unreserved set here is `A-Za-z0-9` plus `-._~` and nothing else, a space
/// becomes `+` rather than `%20`, every other byte of the UTF-8 encoding
/// becomes `%` and two UPPERCASE hex digits.
pub fn quote_plus(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                out.push(byte as char)
            }
            b' ' => out.push('+'),
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

/// `urllib.parse.urlencode` over an ordered list of pairs.
pub fn urlencode(pairs: &[(&str, &str)]) -> String {
    pairs
        .iter()
        .map(|(key, value)| format!("{}={}", quote_plus(key), quote_plus(value)))
        .collect::<Vec<_>>()
        .join("&")
}

/// The report as a markdown paste.
pub fn as_markdown(rep: &Value) -> String {
    let system = fields(field(rep, "system"));
    let sys = |key: &str| text(system.get(key), "?");
    let mut lines: Vec<String> = Vec::new();

    let generated: String = text(field(rep, "generated"), "").chars().take(19).collect();
    lines.push(format!("## Goblin Mode Pro report — {generated}Z"));
    if let Some(note) = truthy_field(rep, "user_note") {
        lines.push(format!("\n> {}\n", text(Some(note), "")));
    }

    lines.push("\n### System".to_string());
    lines.push(format!("- **CPU** {}", sys("cpu")));
    // The driver falls back to the Mesa version when there is no NVIDIA one,
    // and both can be absent - on a machine with neither, this is the line
    // that used to read "driver None".
    let driver = match system.get("nvidia_driver") {
        Some(Value::Null) | None => sys("mesa_gl"),
        Some(other) => text(Some(other), "?"),
    };
    lines.push(format!("- **GPU** {}  ·  driver {driver}", sys("gpu")));
    lines.push(format!(
        "- **Kernel** {}  ·  {}  ·  {} / {}",
        sys("kernel"),
        sys("distro"),
        sys("desktop"),
        sys("session_type")
    ));
    lines.push(format!(
        "- **RAM** {} GB  ·  GMP {}",
        sys("ram_gb"),
        sys("gmp_version")
    ));
    if let Some(game) = truthy_field(rep, "game") {
        lines.push(format!("- **Game** {}", text(Some(game), "")));
    }

    let summary = fields(field(rep, "preflight_summary"));
    let count = |key: &str| text(summary.get(key), "0");
    lines.push(format!(
        "\n### Pre-flight  ({} ok · {} warn · {} fail)",
        count("ok"),
        count("warn"),
        count("fail")
    ));
    let flags = rows(rep, "preflight_flags");
    if flags.is_empty() {
        lines.push("- all clear".to_string());
    } else {
        for flag in flags {
            let flag = fields(Some(flag));
            // `detail or why`: a check that carries no detail explains itself
            // with the reason it exists instead.
            let detail = match flag.get("detail").filter(|v| crate::config::truthy(v)) {
                Some(value) => text(Some(value), ""),
                None => text(flag.get("why"), "None"),
            };
            lines.push(format!(
                "- **{}** {} = `{}` — {detail}",
                text(flag.get("status"), "None").to_uppercase(),
                text(flag.get("title"), "None"),
                text(flag.get("value"), "None"),
            ));
        }
    }

    let log_file = text(field(rep, "log_file"), "");
    let has_log = truthy_field(rep, "log_file").is_some();
    lines.push(format!(
        "\n### Wine/Proton log{}",
        if has_log {
            format!("  (`{log_file}`)")
        } else {
            String::new()
        }
    ));
    let findings = rows(rep, "log_findings");
    if !findings.is_empty() {
        for finding in findings {
            let finding = fields(Some(finding));
            lines.push(format!(
                "- **{}** ×{} ({}) — {}",
                text(finding.get("label"), "None"),
                text(finding.get("count"), "None"),
                text(finding.get("category"), "None"),
                text(finding.get("cause"), "None"),
            ));
            lines.push(format!("  - fix: {}", text(finding.get("fix"), "None")));
            // Backticks are stripped before the sample goes inside a code
            // span, and the cut is 200 CHARACTERS - a log line is as likely
            // to be UTF-8 as not, and a byte cut could split one.
            let sample: String = text(finding.get("sample"), "None")
                .replace('`', "")
                .chars()
                .take(200)
                .collect();
            lines.push(format!("  - `{sample}`"));
        }
    } else if has_log {
        lines.push("- no known failure patterns matched".to_string());
    } else {
        lines.push(
            "- no captured log (set the launch option / command prefix to \
             `goblin-run %command%`)"
                .to_string(),
        );
    }

    if truthy_field(rep, "incident").is_some() {
        let incident = fields(field(rep, "incident"));
        lines.push(format!(
            "\n### Last incident — {}",
            text(incident.get("kind"), "?")
        ));
        lines.push(format!("- {}", text(incident.get("detail"), "")));
        // The GPU block is gated on the COERCED record, so a `gpu_state`
        // holding a list produces no line rather than a line of placeholders.
        let gpu = fields(incident.get("gpu_state"));
        if !gpu.is_empty() {
            let g = |key: &str| text(gpu.get(key), "None");
            lines.push(format!(
                "- GPU: {}/{} MB VRAM · PCIe Gen{}×{} · pstate {} · clock {}/{} MHz",
                g("vram_used_mb"),
                g("vram_total_mb"),
                g("pcie_gen"),
                g("pcie_width"),
                g("pstate"),
                g("clock_gfx_mhz"),
                g("clock_gfx_max_mhz"),
            ));
        }
    }

    let selftest = fields(field(rep, "capability_selftest"));
    if let Some(results) = selftest.get("results").filter(|v| crate::config::truthy(v)) {
        // Counts are sorted by key, not left in the order the suite produced
        // them, so the heading reads the same between runs.
        let counts = fields(selftest.get("summary"));
        let mut pairs: Vec<_> = counts.iter().collect();
        pairs.sort_by(|a, b| a.0.cmp(b.0));
        let tally: Vec<String> = pairs
            .into_iter()
            .map(|(key, value)| format!("{} {}", text(Some(value), "None"), key.to_lowercase()))
            .collect();
        lines.push(format!("\n### Capabilities  ({})", tally.join(" · ")));

        // Only the things that are not fine: a FAIL is a broken privileged
        // path and a SKIP is a capability this machine does not have, and
        // between them that is usually the answer to "why not for me".
        let notable: Vec<&Value> = results
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .filter(|r| {
                        matches!(
                            text(fields(Some(r)).get("status"), "").as_str(),
                            "FAIL" | "SKIP"
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        for result in &notable {
            let result = fields(Some(result));
            lines.push(format!(
                "- **{}** {} — {}",
                text(result.get("status"), "None"),
                text(result.get("title"), "None"),
                text(result.get("detail"), "None"),
            ));
        }
        if notable.is_empty() {
            lines.push("- every privileged path this machine has is reachable".to_string());
        }
    }

    // Coerced BEFORE the emptiness test, so an `active_tweaks` holding a list
    // produces no section at all rather than a section saying "none".
    let tweaks = fields(field(rep, "active_tweaks"));
    if !tweaks.is_empty() {
        let truthy = |key: &str| tweaks.get(key).filter(|v| crate::config::truthy(v));
        let mut on: Vec<String> = TWEAK_KEYS
            .iter()
            .filter(|key| truthy(key).is_some())
            .map(|key| (*key).to_string())
            .collect();
        if let Some(scheduler) = truthy("scx_scheduler") {
            on.push(format!("scx_{}", name(scheduler)));
        }
        let reniced = fields(tweaks.get("reniced"))
            .keys()
            .cloned()
            .collect::<Vec<_>>()
            .join(", ");
        lines.push(format!(
            "\n### Active tweaks\n- {}  ·  reniced: {}",
            if on.is_empty() {
                "none".to_string()
            } else {
                on.join(", ")
            },
            if reniced.is_empty() { "none" } else { &reniced },
        ));
    }

    lines.join("\n") + "\n"
}

/// The "works for me" note, which carries no incident and no log excerpt.
pub fn works_for_me_markdown(rep: &Value, profile_json: &str) -> String {
    let system = fields(field(rep, "system"));
    let sys = |key: &str| text(system.get(key), "?");

    let mut lines = vec![
        format!("## Works for me — {}", text(field(rep, "game"), "?")),
        String::new(),
    ];
    if let Some(note) = truthy_field(rep, "note") {
        lines.push(format!("> {}", text(Some(note), "")));
        lines.push(String::new());
    }
    lines.extend([
        format!("- **CPU** {}", sys("cpu")),
        format!("- **GPU** {}", sys("gpu")),
        format!(
            "- **Kernel** {}  ·  {}  ·  {} / {}",
            sys("kernel"),
            sys("distro"),
            sys("desktop"),
            sys("session_type")
        ),
        format!("- **GMP** {}", sys("gmp_version")),
    ]);
    if let Some(app_id) = truthy_field(rep, "steam_app_id") {
        lines.push(format!("- **Steam AppID** {}", text(Some(app_id), "")));
    }
    // The profile block is `json.dumps(..., indent=2)`, which is rendered by
    // the caller: matching CPython's JSON writer byte for byte is a separate
    // problem from rendering this document, and only one of the two is worth
    // solving twice.
    lines.extend([
        String::new(),
        "### Profile settings".to_string(),
        "```json".to_string(),
        profile_json.to_string(),
        "```".to_string(),
    ]);
    lines.join("\n") + "\n"
}

/// The pre-filled "works for me" issue link.
pub fn works_for_me_issue_url(rep: &Value, profile_json: &str, repo: &str) -> String {
    let body = works_for_me_markdown(rep, profile_json);
    let title = format!(
        "[works for me] {}",
        match truthy_field(rep, "game") {
            Some(game) => text(Some(game), ""),
            None => "game".to_string(),
        }
    );
    let query = urlencode(&[
        ("title", &title),
        ("body", &body),
        ("labels", "works-for-me"),
    ]);
    format!("https://github.com/{repo}/issues/new?{query}")
}

/// The pre-filled bug-report issue link.
pub fn github_issue_url(rep: &Value, repo: &str) -> String {
    let full = as_markdown(rep);
    // 6000 CHARACTERS, not bytes: a report from a machine with a non-ASCII
    // path or game name would otherwise be cut at a different point by each
    // implementation, and could be cut mid-character by one of them.
    let body = if full.chars().count() > MAX_ISSUE_BODY {
        let head: String = full.chars().take(MAX_ISSUE_BODY).collect();
        format!("{head}\n\n*(truncated - full report is on the clipboard)*\n")
    } else {
        full
    };
    let title = format!(
        "[{}] ",
        match truthy_field(rep, "game") {
            Some(game) => text(Some(game), ""),
            None => "game".to_string(),
        }
    );
    let query = urlencode(&[("title", &title), ("body", &body), ("labels", "triage")]);
    format!("https://github.com/{repo}/issues/new?{query}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn quote_plus_matches_pythons_unreserved_set() {
        assert_eq!(quote_plus("a b"), "a+b");
        assert_eq!(quote_plus("-._~"), "-._~");
        assert_eq!(quote_plus("x&y=z"), "x%26y%3Dz");
        assert_eq!(quote_plus("\n"), "%0A");
        assert_eq!(quote_plus("%"), "%25");
        assert_eq!(quote_plus("ゲーム"), "%E3%82%B2%E3%83%BC%E3%83%A0");
    }

    #[test]
    fn hex_digits_are_upper_case() {
        // Lower case is a valid encoding and a different string, and GitHub
        // is not the only thing that ever compares these.
        assert_eq!(quote_plus("\u{ff}"), "%C3%BF");
    }

    #[test]
    fn a_missing_driver_does_not_read_as_the_word_none() {
        // Reachable: `mesa_gl` is set to None on any machine without glxinfo,
        // and the key being present is what stopped the default applying.
        let rep = json!({
            "generated": "2026-09-03T14:00:00+00:00",
            "system": {"gpu": "RTX 2060", "ram_gb": null, "mesa_gl": null},
            "preflight_summary": {}, "preflight_flags": [], "log_file": "",
            "log_findings": [],
        });
        let markdown = as_markdown(&rep);
        assert!(markdown.contains("driver ?"), "{markdown}");
        assert!(markdown.contains("**RAM** ? GB"), "{markdown}");
        assert!(!markdown.contains("None"), "{markdown}");
    }

    #[test]
    fn an_nvidia_driver_wins_over_the_mesa_version() {
        let rep = json!({
            "generated": "", "system": {"nvidia_driver": "550.90", "mesa_gl": "4.6"},
            "preflight_summary": {}, "preflight_flags": [], "log_file": "",
            "log_findings": [],
        });
        assert!(as_markdown(&rep).contains("driver 550.90"));
    }

    #[test]
    fn no_log_says_how_to_capture_one() {
        let rep = json!({"generated": "", "system": {}, "preflight_summary": {},
                         "preflight_flags": [], "log_file": "", "log_findings": []});
        assert!(as_markdown(&rep).contains("goblin-run %command%"));
    }

    #[test]
    fn a_log_with_no_findings_says_so_rather_than_nothing() {
        let rep = json!({"generated": "", "system": {}, "preflight_summary": {},
                         "preflight_flags": [], "log_file": "steam-1234.log",
                         "log_findings": []});
        let markdown = as_markdown(&rep);
        assert!(markdown.contains("(`steam-1234.log`)"));
        assert!(markdown.contains("no known failure patterns matched"));
    }

    #[test]
    fn a_sample_is_cut_at_two_hundred_characters_not_bytes() {
        let rep = json!({
            "generated": "", "system": {}, "preflight_summary": {},
            "preflight_flags": [], "log_file": "x.log",
            "log_findings": [{"label": "l", "count": 1, "category": "c",
                              "cause": "z", "fix": "f",
                              "sample": "ゲ".repeat(300)}],
        });
        let markdown = as_markdown(&rep);
        let sample_line = markdown
            .lines()
            .find(|l| l.trim_start().starts_with("- `"))
            .unwrap();
        assert_eq!(sample_line.matches('ゲ').count(), 200);
    }

    #[test]
    fn backticks_are_stripped_from_a_sample() {
        let rep = json!({
            "generated": "", "system": {}, "preflight_summary": {},
            "preflight_flags": [], "log_file": "x.log",
            "log_findings": [{"label": "l", "count": 1, "category": "c",
                              "cause": "z", "fix": "f", "sample": "a`b`c"}],
        });
        assert!(as_markdown(&rep).contains("  - `abc`"));
    }

    #[test]
    fn a_flag_with_no_detail_falls_back_to_why_it_exists() {
        let rep = json!({
            "generated": "", "system": {}, "preflight_summary": {"ok": 1},
            "preflight_flags": [{"status": "warn", "title": "t", "value": "v",
                                 "detail": "", "why": "because"}],
            "log_file": "", "log_findings": [],
        });
        assert!(as_markdown(&rep).contains("- **WARN** t = `v` — because"));
    }

    #[test]
    fn the_capability_tally_is_sorted_by_status() {
        let rep = json!({
            "generated": "", "system": {}, "preflight_summary": {},
            "preflight_flags": [], "log_file": "", "log_findings": [],
            "capability_selftest": {"summary": {"PASS": 9, "FAIL": 1},
                                    "results": [{"status": "PASS", "title": "t",
                                                 "detail": "d"}]},
        });
        let markdown = as_markdown(&rep);
        assert!(
            markdown.contains("### Capabilities  (1 fail · 9 pass)"),
            "{markdown}"
        );
        assert!(markdown.contains("every privileged path this machine has is reachable"));
    }

    #[test]
    fn an_issue_body_past_the_cap_is_truncated_and_says_so() {
        let rep = json!({
            "generated": "", "system": {"cpu": "x".repeat(9000)},
            "preflight_summary": {}, "preflight_flags": [], "log_file": "",
            "log_findings": [], "game": "WoW",
        });
        let url = github_issue_url(&rep, "o/r");
        assert!(url.contains("truncated"));
        assert!(url.starts_with("https://github.com/o/r/issues/new?title=%5BWoW%5D+&body="));
    }

    #[test]
    fn a_report_with_no_game_still_titles_the_issue() {
        let rep = json!({"generated": "", "system": {}, "preflight_summary": {},
                         "preflight_flags": [], "log_file": "", "log_findings": []});
        assert!(github_issue_url(&rep, "o/r").contains("title=%5Bgame%5D+&"));
    }
}
