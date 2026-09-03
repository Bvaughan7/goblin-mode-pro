//! Reporting a selftest run.
//!
//! A port of the pure slice of `src/goblinmode/selftest.py`: the `Result`
//! record, the status ordering, the rendered table, the JSON export, and the
//! two small formatters. Every probe stays in Python - they read this
//! machine's sysfs and answer questions about it, which is not something a
//! second implementation can usefully agree about.

use serde::{Deserialize, Serialize};

pub const PASS: &str = "PASS";
pub const FAIL: &str = "FAIL";
pub const SKIP: &str = "SKIP";
pub const INFO: &str = "INFO";

/// The order the summary line counts things in.
///
/// Not alphabetical and not the order they were produced: failures first,
/// because that is what somebody running this is looking for.
pub const STATUS_ORDER: [&str; 4] = [FAIL, SKIP, INFO, PASS];

/// Linux capability bit numbers, for decoding a `/proc/<pid>/status` mask.
pub const CAP_BITS: &[(&str, u32)] = &[
    ("CAP_SYS_NICE", 23),
    ("CAP_SYS_RESOURCE", 24),
    ("CAP_SYS_ADMIN", 21),
];

/// One capability's verdict. `detail` is a sentence, and never empty.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Result {
    pub name: String,
    pub title: String,
    pub status: String,
    pub detail: String,
    #[serde(default = "general")]
    pub section: String,
    #[serde(default)]
    pub observed: serde_json::Map<String, serde_json::Value>,
}

fn general() -> String {
    "General".to_string()
}

/// The capability names set in a mask, in table order.
pub fn decode_caps(mask: u64) -> Vec<&'static str> {
    CAP_BITS
        .iter()
        .filter(|(_, bit)| mask >> bit & 1 == 1)
        .map(|(name, _)| *name)
        .collect()
}

/// Microwatts as a human-readable wattage.
pub fn watts(uw: Option<i64>) -> String {
    match uw {
        None => "unknown".to_string(),
        // One decimal place, half-to-even, matching Python's format().
        Some(value) => format!("{:.1} W", value as f64 / 1_000_000.0),
    }
}

/// Turn a helper call failure into something a person can act on.
///
/// The timeout branch is the one that earns its keep. A helper call that
/// times out almost always means a polkit password dialog appeared and was
/// never answered - which looks like a hang, not like a prompt, if you are
/// running the tool over SSH or from a different session than the one that
/// owns the screen. Saying so is the difference between a bug report and a
/// user glancing at their other monitor.
pub fn explain_call_failure(error_type: &str, text: &str, method: &str) -> String {
    if text.contains("Timeout") {
        return format!(
            "{method} timed out on the bus. This usually means a polkit \
             password prompt appeared and wasn't answered - check for a \
             dialog on your desktop, or run this from the session that \
             owns the screen"
        );
    }
    if text.contains("AccessDenied") || text.to_lowercase().contains("not authorized") {
        return format!(
            "{method} was refused by polkit - the action is installed but \
             this session is not allowed to use it"
        );
    }
    format!("{method}: {error_type}: {text}")
}

/// `{status: count}` over the results.
pub fn counts(results: &[Result]) -> Vec<(String, usize)> {
    let mut out: Vec<(String, usize)> = Vec::new();
    for result in results {
        match out.iter_mut().find(|(status, _)| *status == result.status) {
            Some(slot) => slot.1 += 1,
            None => out.push((result.status.clone(), 1)),
        }
    }
    out
}

/// The export written by `--json`.
///
/// `machine` is passed in rather than probed: it comes from the capability
/// detection, which is this machine answering questions about itself.
pub fn to_json(
    results: &[Result],
    apply: bool,
    version: &str,
    machine: &serde_json::Value,
) -> serde_json::Value {
    let summary: serde_json::Map<String, serde_json::Value> = counts(results)
        .into_iter()
        .map(|(status, n)| (status, serde_json::json!(n)))
        .collect();
    serde_json::json!({
        "version": version,
        "mode": if apply { "apply" } else { "read-only" },
        "machine": machine,
        "summary": summary,
        "results": results,
    })
}

fn mark(status: &str) -> &str {
    match status {
        INFO => "info",
        other => other,
    }
}

fn tint(status: &str) -> &str {
    match status {
        PASS => "\u{1b}[32m",
        FAIL => "\u{1b}[31m",
        SKIP => "\u{1b}[33m",
        INFO => "\u{1b}[36m",
        _ => "",
    }
}

/// A human-readable table, grouped by section, widest-first aligned.
///
/// Sections come out in the order they were first produced, not sorted: the
/// suite runs them in a deliberate order and the report should read the way
/// the run happened.
pub fn render(results: &[Result], apply: bool, color: bool, machine: &serde_json::Value) -> String {
    let field = |key: &str| -> String {
        machine
            .get(key)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("unknown")
            .to_string()
    };
    let mut out = vec![
        format!(
            "goblin-mode-pro selftest - {}",
            if apply {
                "apply (round-trip)"
            } else {
                "read-only"
            }
        ),
        format!(
            "  {} / {} / {} {}",
            field("cpu"),
            field("gpu"),
            field("distro"),
            field("kernel")
        ),
        String::new(),
    ];

    // Python's max(..., default=10) over CHARACTER counts, since the titles
    // are what gets padded and a byte count would misalign any non-ASCII one.
    //
    // The default is unobservable in both implementations - with no results
    // there are no rows to pad - so it is carried across for fidelity rather
    // than because anything depends on it. Mutating it changes no output,
    // which is worth knowing before someone tries to write a test for it.
    let width = results
        .iter()
        .map(|r| r.title.chars().count())
        .max()
        .unwrap_or(10);

    let mut sections: Vec<&str> = Vec::new();
    for result in results {
        if !sections.contains(&result.section.as_str()) {
            sections.push(&result.section);
        }
    }
    for section in sections {
        out.push(section.to_string());
        for result in results.iter().filter(|r| r.section == section) {
            let label = if color {
                format!("{}{}\u{1b}[0m", tint(&result.status), mark(&result.status))
            } else {
                mark(&result.status).to_string()
            };
            let padding = " ".repeat(width.saturating_sub(result.title.chars().count()));
            out.push(format!(
                "  {label}  {}{padding}  {}",
                result.title, result.detail
            ));
        }
        out.push(String::new());
    }

    let tally: Vec<String> = STATUS_ORDER
        .iter()
        .filter_map(|status| {
            let n = results.iter().filter(|r| r.status == *status).count();
            (n > 0).then(|| format!("{} {n}", mark(status)))
        })
        .collect();
    out.push(tally.join("  "));

    if !apply {
        out.extend([
            String::new(),
            "Read-only: nothing was changed, and nothing above proves a write".into(),
            "path - these values are written by the root helper, so testing them".into(),
            "as your user would say nothing either way. Run `selftest --apply` to".into(),
            "round-trip each capability (apply, read back, revert, read back).".into(),
        ]);
    }
    out.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn result(status: &str, title: &str, section: &str) -> Result {
        Result {
            name: title.to_lowercase(),
            title: title.to_string(),
            status: status.to_string(),
            detail: "a sentence".to_string(),
            section: section.to_string(),
            observed: serde_json::Map::new(),
        }
    }

    fn machine() -> serde_json::Value {
        serde_json::json!({
            "cpu": "Core i7-10750H", "gpu": "nvidia, intel",
            "distro": "cachyos", "kernel": "7.2.2-1-cachyos",
        })
    }

    #[test]
    fn failures_are_counted_first() {
        // Not alphabetical, and not the order they happened: somebody running
        // this is looking for what broke.
        assert_eq!(STATUS_ORDER, [FAIL, SKIP, INFO, PASS]);
    }

    #[test]
    fn a_status_with_no_results_is_left_out_of_the_tally() {
        let results = [result(PASS, "Governor", "CPU")];
        let text = render(&results, false, false, &machine());
        assert!(text.contains("PASS 1"), "{text}");
        assert!(!text.contains("FAIL 0"), "an empty count is noise");
    }

    #[test]
    fn info_is_lowercase_in_the_table() {
        // The four labels are the same width so the column aligns; INFO is
        // written lowercase to read as a note rather than a verdict.
        let results = [result(INFO, "Handheld", "System")];
        assert!(render(&results, false, false, &machine()).contains("  info  "));
    }

    #[test]
    fn sections_keep_the_order_the_suite_produced_them_in() {
        let results = [
            result(PASS, "A", "Second"),
            result(PASS, "B", "First"),
            result(PASS, "C", "Second"),
        ];
        let text = render(&results, false, false, &machine());
        assert!(
            text.find("Second").unwrap() < text.find("First").unwrap(),
            "sorted rather than kept in order:\n{text}"
        );
    }

    #[test]
    fn titles_are_padded_to_the_widest_by_character_count() {
        let results = [
            result(PASS, "Short", "S"),
            result(PASS, "A much longer title", "S"),
        ];
        let text = render(&results, false, false, &machine());
        let lines: Vec<&str> = text.lines().filter(|l| l.contains("a sentence")).collect();
        let columns: Vec<usize> = lines
            .iter()
            .map(|l| l.find("a sentence").unwrap_or(0))
            .collect();
        assert_eq!(columns[0], columns[1], "detail column not aligned:\n{text}");
    }

    #[test]
    fn a_read_only_run_says_what_it_did_not_prove() {
        // The most important paragraph in the output: a wall of PASS from a
        // read-only run proves nothing about the write paths, and somebody
        // will otherwise take it as a clean bill of health.
        let text = render(&[result(PASS, "X", "S")], false, false, &machine());
        assert!(text.contains("nothing above proves a write"), "{text}");
        let applied = render(&[result(PASS, "X", "S")], true, false, &machine());
        assert!(!applied.contains("nothing above proves a write"));
        assert!(applied.contains("apply (round-trip)"));
    }

    #[test]
    fn a_timeout_is_explained_as_a_prompt_nobody_answered() {
        // It looks like a hang, and over SSH or from the wrong session that
        // is exactly what it is - the dialog is on another screen.
        let text = explain_call_failure("DBusError", "Timeout was reached", "SetGovernor");
        assert!(text.contains("polkit password prompt"), "{text}");
        assert!(text.contains("owns the screen"), "{text}");
    }

    #[test]
    fn a_refusal_is_explained_as_a_policy_decision() {
        for message in ["AccessDenied", "Not Authorized", "not authorized"] {
            let text = explain_call_failure("DBusError", message, "SetSysctl");
            assert!(text.contains("refused by polkit"), "{message}: {text}");
        }
    }

    #[test]
    fn anything_else_keeps_the_type_and_the_message() {
        // A failure nobody anticipated should still be reportable verbatim
        // rather than flattened into "something went wrong".
        let text = explain_call_failure("OSError", "No such file", "ReadUndervolt");
        assert_eq!(text, "ReadUndervolt: OSError: No such file");
    }

    #[test]
    fn capabilities_decode_from_a_proc_status_mask() {
        assert_eq!(decode_caps(0), Vec::<&str>::new());
        assert_eq!(decode_caps(1 << 23), vec!["CAP_SYS_NICE"]);
        assert_eq!(
            decode_caps((1 << 23) | (1 << 24) | (1 << 21)),
            vec!["CAP_SYS_NICE", "CAP_SYS_RESOURCE", "CAP_SYS_ADMIN"],
        );
        assert_eq!(decode_caps(u64::MAX).len(), CAP_BITS.len());
    }

    #[test]
    fn watts_round_the_way_python_formats_them() {
        assert_eq!(watts(None), "unknown");
        assert_eq!(watts(Some(45_000_000)), "45.0 W");
        assert_eq!(watts(Some(0)), "0.0 W");
        // The half-to-even rule only bites where the decimal is EXACTLY
        // representable in binary, which is rarer than it looks. 45.25 is,
        // and rounds down to the even digit. 45.35 is not - the stored value
        // is a hair above - so it rounds up, and the same is true of 45.45.
        // Both languages agree on all four because both are formatting the
        // same f64, which is the whole reason to format rather than to do
        // arithmetic here.
        assert_eq!(watts(Some(45_250_000)), "45.2 W");
        assert_eq!(watts(Some(45_150_000)), "45.1 W");
        assert_eq!(watts(Some(45_350_000)), "45.4 W");
        assert_eq!(watts(Some(45_450_000)), "45.5 W");
    }

    #[test]
    fn the_json_export_carries_the_counts_and_the_mode() {
        let results = [result(PASS, "A", "S"), result(FAIL, "B", "S")];
        let json = to_json(&results, false, "1.5.0", &machine());
        assert_eq!(json["mode"], "read-only");
        assert_eq!(json["summary"]["PASS"], 1);
        assert_eq!(json["summary"]["FAIL"], 1);
        assert_eq!(json["results"].as_array().expect("a list").len(), 2);
        assert_eq!(
            to_json(&results, true, "1.5.0", &machine())["mode"],
            "apply"
        );
    }

    #[test]
    fn an_empty_run_still_renders() {
        // Nothing to report is not a crash - it is what a machine with no
        // detected capabilities would legitimately produce.
        let text = render(&[], false, false, &machine());
        assert!(text.contains("goblin-mode-pro selftest"), "{text}");
    }
}
