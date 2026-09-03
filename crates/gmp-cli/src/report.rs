//! What the CLI prints, given what the daemon answered.
//!
//! Every command here is the same two steps: ask the daemon something over the
//! session bus, then turn the reply into lines. The asking is plumbing and
//! stays in Python. The lines are what a person reads, and they are what moves.
//!
//! The interesting constraint is that these replies come across a **frozen**
//! interface, and every one of them arrives as a JSON string rather than as a
//! typed D-Bus structure - `GetStatus`, `GetHealth`, `GetSessions` and
//! `RunPreflight` all declare `s`. So the signature guarantees nothing about
//! what is inside, and the freeze exists precisely because the daemon on the
//! other end may be a different build from the CLI asking. A field of an
//! unexpected type is therefore a thing that can happen, not a thing that
//! cannot, and the rule throughout is the one [`gmp_core::pyfmt`] is built on:
//! say what is there, do not fail. `sessions` has the same problem from a
//! different direction - it renders a JSONL file that accumulates across
//! versions of this program.

use gmp_core::pyfmt::{name, names, number, scalar, text};
use serde_json::Value;

/// The tweaks named on the status line, in the order they are named.
///
/// Fixed rather than derived from the reply, so a newer daemon reporting a
/// tweak this build has never heard of does not print it under a key the user
/// cannot interpret - and so the order does not change between runs.
pub const TWEAK_KEYS: &[&str] = &[
    "governor",
    "epp_boosted",
    "tearing",
    "adaptive_sync",
    "power_limited",
    "focus_mode",
];

const EM_DASH: &str = "—";

/// `d[key]`, for a value that may not be an object at all.
fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.as_object()?.get(key)
}

/// `d.get(key) or default` - Python's falsy-or, not its missing-or.
fn truthy_field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    field(value, key).filter(|v| gmp_core::config::truthy(v))
}

/// `goblin-mode-pro-cli status`.
pub fn status(status: &Value) -> Vec<String> {
    let mut lines = Vec::new();

    let master = truthy_field(status, "master_enabled").is_some();
    lines.push(format!(
        "master      : {}",
        if master { "on" } else { "off" }
    ));

    let games = truthy_field(status, "active_games")
        .map(name)
        .unwrap_or_default();
    lines.push(format!(
        "active game  : {}",
        if games.is_empty() { EM_DASH } else { &games }
    ));
    lines.push(format!(
        "governor     : {}",
        text(field(status, "governor"), "?")
    ));

    let tweaks = field(status, "tweaks").cloned().unwrap_or(Value::Null);
    let mut on: Vec<String> = TWEAK_KEYS
        .iter()
        .filter(|key| truthy_field(&tweaks, key).is_some())
        .map(|key| (*key).to_string())
        .collect();
    if let Some(scheduler) = truthy_field(&tweaks, "scx_scheduler") {
        on.push(format!("scx_{}", name(scheduler)));
    }
    lines.push(format!(
        "active tweaks : {}",
        if on.is_empty() {
            "none".to_string()
        } else {
            on.join(", ")
        }
    ));

    lines.push(format!(
        "helper       : {}",
        if truthy_field(status, "helper_available").is_some() {
            "connected"
        } else {
            "limited mode"
        }
    ));

    let caps = field(status, "capabilities")
        .cloned()
        .unwrap_or(Value::Null);
    lines.push(format!(
        "machine      : {} · {} · kernel {}",
        text(field(&caps, "cpu_model"), "?"),
        truthy_field(&caps, "gpu_vendors")
            .map(name)
            .unwrap_or_default(),
        text(field(&caps, "kernel_release"), "?"),
    ));

    lines
}

/// `goblin-mode-pro-cli health`.
pub fn health(health: &Value) -> Vec<String> {
    let score = field(health, "score")
        .filter(|v| !v.is_null())
        .map(scalar)
        .unwrap_or_else(|| "?".to_string());
    let mut lines = vec![format!("system readiness: {score} / 10")];

    let counts = field(health, "counts").cloned().unwrap_or(Value::Null);
    let count = |key: &str| text(field(&counts, key), "0");
    lines.push(format!(
        "  {} ok · {} warn · {} fail",
        count("ok"),
        count("warn"),
        count("fail")
    ));

    for worst in truthy_field(health, "worst").map(names).unwrap_or_default() {
        lines.push(format!("  ✗ {worst}"));
    }
    lines
}

/// `goblin-mode-pro-cli sessions`.
///
/// `rows` is the whole history and `limit` the tail to show, applied here
/// rather than by the caller because the "no sessions recorded yet" line
/// depends on which of the two is empty.
pub fn sessions(rows: &[Value], limit: i64) -> Vec<String> {
    if rows.is_empty() {
        return vec!["no sessions recorded yet".to_string()];
    }
    // Python's `rows[-limit:]`, and `limit` is signed because `--limit` is a
    // bare `type=int` with no bounds, so both surprises below are reachable
    // from the command line rather than hypothetical.
    let tail = &rows[slice_start(-limit, rows.len())..];

    tail.iter()
        .map(|session| {
            let tag = if truthy_field(session, "benchmark").is_some() {
                " [benchmark]"
            } else {
                ""
            };
            let started: String = text(field(session, "started"), "")
                .chars()
                .take(16)
                .collect();
            let game = text(field(session, "game"), "?");
            let average = number(field(session, "fps_avg"));
            let low = number(field(session, "fps_1low"));

            let fps = match (average, low) {
                // Both numbers and both non-zero, because the Python tests
                // these for truth rather than for presence and a rate of zero
                // is falsy. It guarded on the average alone and then formatted
                // both, so a record carrying an average without a 1% low -
                // which no run this program writes produces, but an older or
                // hand-edited history can - raised instead of printing.
                (Some(average), Some(low)) if average != 0.0 && low != 0.0 => {
                    format!("  avg {average:.0}  1% {low:.0}")
                }
                _ => "  (no fps log)".to_string(),
            };
            format!("{started}  {game:24}{tag}{fps}")
        })
        .collect()
}

/// Where `xs[k:]` starts, for a `k` that may be negative or out of range.
///
/// Two results are worth naming. `--limit 0` negates to `0` and `xs[0:]` is
/// the WHOLE history rather than none of it. `--limit -5` negates to `5` and
/// drops five rows off the FRONT.
fn slice_start(index: i64, length: usize) -> usize {
    if index < 0 {
        (length as i64 + index).max(0) as usize
    } else {
        (index as usize).min(length)
    }
}

/// `goblin-mode-pro-cli preflight`.
pub fn preflight(checks: &[Value]) -> Vec<String> {
    checks
        .iter()
        .map(|check| {
            let mark = match text(field(check, "status"), "").as_str() {
                "ok" => "✓",
                "warn" => "!",
                "fail" => "✗",
                "info" => "i",
                _ => "?",
            };
            format!(
                "{mark} {:34} {}",
                text(field(check, "title"), "?"),
                text(field(check, "value"), "")
            )
        })
        .collect()
}

/// The two lines `preflight --fix` adds after the checks.
pub fn preflight_fixes(result: &Value) -> Vec<String> {
    let applied = truthy_field(result, "applied")
        .map(name)
        .unwrap_or_default();
    let mut lines = vec![format!(
        "\napplied: {}",
        if applied.is_empty() {
            "nothing"
        } else {
            &applied
        }
    )];
    if let Some(failed) = truthy_field(result, "failed") {
        lines.push(format!("failed : {}", name(failed)));
    }
    lines
}

/// `goblin-mode-pro-cli games`.
///
/// The forced-boost pseudo-profile is skipped: it is how a manual boost is
/// stored, not a game anybody added.
pub fn games(status: &Value) -> Vec<String> {
    let Some(profiles) = field(status, "profiles").and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    profiles
        .iter()
        .filter(|profile| text(field(profile, "exe"), "") != "__forced__")
        .map(|profile| {
            let mark = if truthy_field(profile, "enabled").is_some() {
                "●"
            } else {
                "○"
            };
            format!(
                "{mark} {:28} ({}, {})",
                text(field(profile, "display_name"), "?"),
                text(field(profile, "exe"), "None"),
                text(field(profile, "match_mode"), "None"),
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_status_reply_reads_as_the_six_lines() {
        let lines = status(&json!({
            "master_enabled": true,
            "active_games": ["Wow.exe"],
            "governor": "performance",
            "tweaks": {"governor": true, "focus_mode": true, "scx_scheduler": "rusty"},
            "helper_available": true,
            "capabilities": {"cpu_model": "i7-10750H", "gpu_vendors": ["nvidia"],
                             "kernel_release": "6.9.0"},
        }));
        assert_eq!(lines.len(), 6);
        assert_eq!(lines[1], "active game  : Wow.exe");
        assert_eq!(lines[3], "active tweaks : governor, focus_mode, scx_rusty");
        assert_eq!(lines[5], "machine      : i7-10750H · nvidia · kernel 6.9.0");
    }

    #[test]
    fn an_empty_status_still_reads() {
        let lines = status(&json!({}));
        assert_eq!(lines[0], "master      : off");
        assert_eq!(lines[1], "active game  : —");
        assert_eq!(lines[3], "active tweaks : none");
        assert_eq!(lines[4], "helper       : limited mode");
    }

    #[test]
    fn the_tweak_order_is_the_tables_not_the_replys() {
        // A daemon serialising its tweaks in another order must not change
        // what the line reads, or two machines disagree for no reason.
        let lines = status(&json!({
            "tweaks": {"focus_mode": true, "tearing": true, "governor": true}
        }));
        assert_eq!(lines[3], "active tweaks : governor, tearing, focus_mode");
    }

    #[test]
    fn a_tweak_this_build_has_not_heard_of_is_not_printed() {
        let lines = status(&json!({"tweaks": {"warp_drive": true}}));
        assert_eq!(lines[3], "active tweaks : none");
    }

    #[test]
    fn a_session_without_a_full_fps_record_says_so_rather_than_failing() {
        let rows = vec![
            json!({"started": "2026-09-03T12:00", "game": "Wow", "fps_avg": 60.4,
                   "fps_1low": 41.6}),
            json!({"started": "2026-09-03T13:00", "game": "Wow", "fps_avg": 60.4}),
            json!({"started": "2026-09-03T14:00", "game": "Wow"}),
        ];
        let lines = sessions(&rows, 10);
        assert!(lines[0].ends_with("  avg 60  1% 42"));
        assert!(lines[1].ends_with("  (no fps log)"));
        assert!(lines[2].ends_with("  (no fps log)"));
    }

    #[test]
    fn frame_rates_round_half_to_even_like_python() {
        let rows = vec![json!({"started": "x", "game": "g", "fps_avg": 60.5,
                               "fps_1low": 59.5})];
        assert!(sessions(&rows, 10)[0].ends_with("  avg 60  1% 60"));
    }

    #[test]
    fn the_limit_takes_the_last_rows() {
        let rows: Vec<Value> = (0..5)
            .map(|i| json!({"started": format!("s{i}"), "game": "g"}))
            .collect();
        let lines = sessions(&rows, 2);
        assert_eq!(lines.len(), 2);
        assert!(lines[0].starts_with("s3"));
    }

    #[test]
    fn a_limit_of_zero_shows_everything_rather_than_nothing() {
        // `--limit 0` is accepted, negates to `0`, and `rows[0:]` is the lot.
        let rows: Vec<Value> = (0..5)
            .map(|i| json!({"started": format!("s{i}"), "game": "g"}))
            .collect();
        assert_eq!(sessions(&rows, 0).len(), 5);
    }

    #[test]
    fn a_negative_limit_drops_rows_off_the_front() {
        let rows: Vec<Value> = (0..5)
            .map(|i| json!({"started": format!("s{i}"), "game": "g"}))
            .collect();
        let lines = sessions(&rows, -2);
        assert_eq!(lines.len(), 3);
        assert!(lines[0].starts_with("s2"));
    }

    #[test]
    fn a_limit_past_either_end_stays_in_range() {
        let rows: Vec<Value> = (0..3)
            .map(|i| json!({"started": format!("s{i}"), "game": "g"}))
            .collect();
        assert_eq!(sessions(&rows, 99).len(), 3);
        assert_eq!(sessions(&rows, -99).len(), 0);
    }

    #[test]
    fn no_sessions_is_its_own_line() {
        assert_eq!(sessions(&[], 10), vec!["no sessions recorded yet"]);
    }

    #[test]
    fn a_preflight_status_outside_the_four_gets_a_question_mark() {
        let checks = vec![
            json!({"status": "ok", "title": "governor", "value": "performance"}),
            json!({"status": "elsewhere", "title": "t", "value": "v"}),
            json!({"title": "no status at all", "value": "v"}),
        ];
        let lines = preflight(&checks);
        assert!(lines[0].starts_with("✓ governor"));
        assert!(lines[1].starts_with("? t"));
        assert!(lines[2].starts_with("? no status at all"));
    }

    #[test]
    fn the_forced_boost_pseudo_profile_is_not_a_game() {
        let reply = json!({"profiles": [
            {"exe": "__forced__", "display_name": "forced", "enabled": true},
            {"exe": "Wow.exe", "display_name": "WoW", "enabled": true,
             "match_mode": "exact"},
        ]});
        let lines = games(&reply);
        assert_eq!(lines.len(), 1);
        assert!(lines[0].starts_with("● WoW"));
        assert!(lines[0].ends_with("(Wow.exe, exact)"));
    }

    #[test]
    fn nothing_applied_says_nothing_rather_than_leaving_the_line_blank() {
        assert_eq!(preflight_fixes(&json!({})), vec!["\napplied: nothing"]);
        assert_eq!(
            preflight_fixes(&json!({"applied": ["a"], "failed": ["b"]})),
            vec!["\napplied: a", "failed : b"]
        );
    }
}
