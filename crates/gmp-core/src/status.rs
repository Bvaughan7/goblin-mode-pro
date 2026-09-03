//! What the daemon reports about itself.
//!
//! The read-and-report half of the daemon's D-Bus surface, which
//! `daemon_api.py` already describes as the part that owns no state: it reads
//! collaborators and hands the answer back. Those answers are judgement, so
//! they move; the collaborators they read stay in Python.
//!
//! Four things live here, and all four end up somewhere a person or a later
//! run can see them - the readiness score on the dashboard and the CLI's
//! status line, the tweak fingerprint written into `sessions.jsonl` and read
//! back when comparing two runs, the GPU block quoted in a bug report, and the
//! metric window an incident carries.

use serde_json::{Map, Value};

use crate::pyfmt::{fields, text};
use crate::round::one_dp;

/// The statuses the readiness count starts from, in the order they are shown.
///
/// Seeded rather than discovered so that a run with no warnings still reports
/// `0 warn` rather than leaving the key out.
pub const HEALTH_STATUSES: &[&str] = &["ok", "warn", "fail", "info", "unknown"];

/// How much each status costs against the score.
const FAIL_PENALTY: f64 = 2.0;
const WARN_PENALTY: f64 = 0.6;
/// The denominator's slack: at `1.0` a single failure among two checks would
/// zero the score, which reads as broken rather than as degraded.
const SPREAD: f64 = 1.4;
/// How many failing checks the dashboard names.
const WORST_SHOWN: usize = 3;

/// The cached 0-10 "is this box game-ready?" answer.
///
/// `checked_at` is not here: it is a clock reading, and the caller that has a
/// clock is the one that should stamp it.
pub fn health(results: &[Value]) -> Value {
    let mut counts: Map<String, Value> = HEALTH_STATUSES
        .iter()
        .map(|status| ((*status).to_string(), Value::from(0u64)))
        .collect();

    for result in results {
        // A status outside the five ADDS a key rather than being folded into
        // "unknown", and it counts towards the total - so an unfamiliar status
        // from a newer check dilutes the penalty rather than inflating it.
        // That is the existing behaviour and it errs towards the safe side.
        let status = text(fields(Some(result)).get("status"), "None");
        let seen = counts.get(&status).and_then(Value::as_u64).unwrap_or(0);
        counts.insert(status, Value::from(seen + 1));
    }

    let total = counts.values().filter_map(Value::as_u64).sum::<u64>();
    // `sum(...) or 1`: no checks at all divides by one, not by zero.
    let total = if total == 0 { 1.0 } else { total as f64 };
    let count = |key: &str| counts.get(key).and_then(Value::as_u64).unwrap_or(0) as f64;

    let penalty = count("fail") * FAIL_PENALTY + count("warn") * WARN_PENALTY;
    let raw = one_dp(10.0 * (1.0 - penalty / (total * SPREAD)));

    // `max(0, x)` in Python returns whichever ARGUMENT won, not a coerced
    // value - so a clamped score is the integer 0 and every other score is a
    // float. That reaches the CLI, where the difference is "0" against "0.0".
    let score = if raw > 0.0 {
        Value::from(raw)
    } else {
        Value::from(0u64)
    };

    let worst: Vec<String> = results
        .iter()
        .filter(|r| text(fields(Some(r)).get("status"), "") == "fail")
        .map(|r| text(fields(Some(r)).get("title"), "None"))
        .take(WORST_SHOWN)
        .collect();

    serde_json::json!({"score": score, "counts": counts, "worst": worst})
}

/// A short, readable list of what is currently applied.
///
/// Stored with the session so a regression can be read against what changed,
/// which means these strings are a format read back by later runs and not just
/// something shown once.
pub fn tweaks_fingerprint(tweaks: &Value) -> Vec<String> {
    let tweaks = fields(Some(tweaks));
    let truthy = |key: &str| tweaks.get(key).is_some_and(crate::config::truthy);
    let mut out = Vec::new();

    // The governor counts as applied when it is pinned OR when only the finer
    // EPP knob was moved - on `intel_pstate` the second happens without the
    // first, and a session tuned that way is not an untuned session.
    if text(tweaks.get("governor"), "") == "performance" || truthy("epp_boosted") {
        out.push("governor".to_string());
    }
    if truthy("tearing") {
        out.push("tearing".to_string());
    }
    if truthy("adaptive_sync") {
        out.push("vrr".to_string());
    }
    if truthy("reniced") {
        out.push("renice".to_string());
    }
    // Only the first pinned process is named, in insertion order: the string
    // records which pinning MODE was in force, and a second entry would say
    // the same thing again.
    if let Some((_exe, mode)) = fields(tweaks.get("pinned")).iter().next() {
        out.push(format!("pin:{}", text(Some(mode), "None")));
    }
    if truthy("power_limited") {
        if let Some(limits) = tweaks.get("power_limits_w").and_then(Value::as_array) {
            // Both ends or neither. The Python indexes [0] and [1] behind a
            // truthiness test that a one-element list passes, so a half-filled
            // pair raised where it should have said nothing.
            if let [first, second, ..] = limits.as_slice() {
                out.push(format!(
                    "pl:{}/{}",
                    text(Some(first), "None"),
                    text(Some(second), "None")
                ));
            }
        }
    }
    out
}

/// The GPU fields a status reply and a bug report quote.
///
/// A fixed projection rather than the whole probe output: the deep read
/// carries more than this, and a report should not grow new fields because a
/// driver started answering a new question.
pub fn gpu_summary(state: &Value) -> Map<String, Value> {
    const KEYS: &[&str] = &[
        "vram_used_mb",
        "vram_total_mb",
        "vram_free_mb",
        "pcie_gen",
        "pcie_gen_max",
        "pcie_width",
        "pcie_width_max",
        "pstate",
        "clock_gfx_mhz",
        "clock_gfx_max_mhz",
    ];
    let state = fields(Some(state));
    if state.is_empty() {
        return Map::new();
    }
    KEYS.iter()
        .map(|key| {
            (
                (*key).to_string(),
                state.get(*key).cloned().unwrap_or(Value::Null),
            )
        })
        .collect()
}

/// Evenly thin a list of metric samples, always keeping the last one.
///
/// `target` is a literal at both call sites (20 for an incident's metric
/// window, 30 for its frame-rate trace), so a target of zero cannot reach the
/// division below.
pub fn downsample(rows: &[Value], target: usize) -> Vec<Value> {
    // `<=` rather than `<` matches the Python, but the equal case is a
    // shortcut and not a rule: a list already the target length downsamples to
    // itself, because the step is exactly one and the last index is the last
    // row. Changing it to `<` is unobservable.
    if rows.len() <= target {
        return rows.to_vec();
    }
    let step = rows.len() as f64 / target as f64;
    let mut picked: Vec<Value> = (0..target)
        .map(|i| rows[(i as f64 * step) as usize].clone())
        .collect();
    // The Python compares identity here; for rows parsed out of JSON no two
    // are the same object, so this is the same test as "did the arithmetic
    // land on the last index".
    if ((target - 1) as f64 * step) as usize != rows.len() - 1 {
        let last = rows.len() - 1;
        picked[target - 1] = rows[last].clone();
    }
    picked
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn check(status: &str, title: &str) -> Value {
        json!({"status": status, "title": title, "value": "v"})
    }

    #[test]
    fn a_clean_machine_scores_ten() {
        let results: Vec<Value> = (0..10).map(|i| check("ok", &format!("c{i}"))).collect();
        assert_eq!(health(&results)["score"], json!(10.0));
    }

    #[test]
    fn a_clamped_score_is_an_integer_and_every_other_score_is_a_float() {
        // `max(0, x)` hands back the argument that won, so the type of this
        // field depends on its value. The CLI prints "0" or "0.0" from it.
        let bad: Vec<Value> = (0..3).map(|i| check("fail", &format!("c{i}"))).collect();
        let score = &health(&bad)["score"];
        assert_eq!(score, &json!(0));
        assert!(score.is_u64(), "a clamped score is an integer");

        let mixed = vec![check("fail", "a"), check("ok", "b"), check("ok", "c")];
        assert!(health(&mixed)["score"].is_f64());
    }

    #[test]
    fn no_checks_at_all_does_not_divide_by_zero() {
        assert_eq!(health(&[])["score"], json!(10.0));
    }

    #[test]
    fn the_score_rounds_the_way_python_rounds() {
        let results = vec![check("warn", "a"), check("ok", "b")];
        // 10 * (1 - 0.6 / 2.8) = 7.857..., to one decimal place.
        assert_eq!(health(&results)["score"], json!(7.9));
    }

    #[test]
    fn an_unfamiliar_status_gets_its_own_key_and_dilutes_the_penalty() {
        let with_unknown = vec![check("fail", "a"), check("elsewhere", "b")];
        let only_fail = vec![check("fail", "a")];
        let counts = health(&with_unknown)["counts"].clone();
        assert_eq!(counts["elsewhere"], json!(1));
        assert_eq!(counts["unknown"], json!(0), "not folded into unknown");
        assert!(
            health(&with_unknown)["score"].as_f64().unwrap()
                > health(&only_fail)["score"].as_f64().unwrap_or(0.0)
        );
    }

    #[test]
    fn the_five_seeded_statuses_are_always_present_and_in_order() {
        let counts = health(&[])["counts"].clone();
        let keys: Vec<&String> = counts.as_object().unwrap().keys().collect();
        assert_eq!(keys, HEALTH_STATUSES.iter().collect::<Vec<_>>());
    }

    #[test]
    fn at_most_three_failures_are_named() {
        let results: Vec<Value> = (0..5).map(|i| check("fail", &format!("c{i}"))).collect();
        assert_eq!(health(&results)["worst"], json!(["c0", "c1", "c2"]));
    }

    #[test]
    fn only_failures_are_named() {
        let results = vec![check("warn", "w"), check("fail", "f"), check("ok", "o")];
        assert_eq!(health(&results)["worst"], json!(["f"]));
    }

    #[test]
    fn the_fingerprint_reads_epp_as_a_tuned_governor() {
        // On intel_pstate the governor stays put and only EPP moves, and a
        // session tuned that way is not an untuned session.
        assert_eq!(
            tweaks_fingerprint(&json!({"epp_boosted": true})),
            vec!["governor"]
        );
        assert_eq!(
            tweaks_fingerprint(&json!({"governor": "performance"})),
            vec!["governor"]
        );
        assert!(tweaks_fingerprint(&json!({"governor": "powersave"})).is_empty());
    }

    #[test]
    fn a_half_filled_power_limit_pair_says_nothing_rather_than_failing() {
        assert_eq!(
            tweaks_fingerprint(&json!({"power_limited": true, "power_limits_w": [45, 60]})),
            vec!["pl:45/60"]
        );
        assert!(
            tweaks_fingerprint(&json!({"power_limited": true, "power_limits_w": [45]})).is_empty()
        );
        assert!(
            tweaks_fingerprint(&json!({"power_limited": true, "power_limits_w": []})).is_empty()
        );
    }

    #[test]
    fn only_the_first_pinned_process_is_named() {
        let tweaks = json!({"pinned": {"Wow.exe": "performance", "other.exe": "efficiency"}});
        assert_eq!(tweaks_fingerprint(&tweaks), vec!["pin:performance"]);
    }

    #[test]
    fn an_empty_gpu_state_summarises_to_nothing() {
        assert!(gpu_summary(&json!({})).is_empty());
        assert!(gpu_summary(&Value::Null).is_empty());
    }

    #[test]
    fn a_gpu_summary_carries_every_key_even_the_absent_ones() {
        let summary = gpu_summary(&json!({"pstate": "P2", "extra": 1}));
        assert_eq!(summary.len(), 10);
        assert_eq!(summary["pstate"], json!("P2"));
        assert_eq!(summary["vram_used_mb"], Value::Null);
        assert!(!summary.contains_key("extra"), "the projection is fixed");
    }

    #[test]
    fn a_short_list_is_not_downsampled() {
        let rows: Vec<Value> = (0..5).map(|i| json!({"i": i})).collect();
        assert_eq!(downsample(&rows, 20), rows);
        assert_eq!(downsample(&rows, 5), rows);
    }

    #[test]
    fn downsampling_always_keeps_the_last_sample() {
        // The last one is the interesting one: it is the sample the incident
        // fired on.
        for length in [21usize, 40, 99, 100, 1000] {
            let rows: Vec<Value> = (0..length).map(|i| json!({"i": i})).collect();
            let thinned = downsample(&rows, 20);
            assert_eq!(thinned.len(), 20, "{length}");
            assert_eq!(thinned[19], rows[length - 1], "{length}");
            assert_eq!(thinned[0], rows[0], "{length}");
        }
    }
}
