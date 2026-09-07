//! The three things the D-Bus API layer decides for itself.
//!
//! Most of it forwards - to the report builder, the incident log, the
//! preflight checks, the daemon's own state. These three are its own, and each
//! is a small choice that changes what somebody gets back from a method they
//! pressed a button for.

use serde_json::{Map, Value};

/// How much of a log the analyser reads.
///
/// The tail, not the file. A Proton log runs to megabytes and the interesting
/// part is always the end; reading it whole would make a button press take
/// seconds and tell nobody anything more.
pub const ANALYSE_TAIL_BYTES: u64 = 200_000;

/// Where to start reading a log of this size.
///
/// Saturating rather than signed: a file smaller than the window is read from
/// the beginning, not from a negative offset.
pub fn analyse_from(size: u64) -> u64 {
    size.saturating_sub(ANALYSE_TAIL_BYTES)
}

/// The Steam AppID to analyse a log against.
///
/// The first active game that has one. Analysis rules are keyed by AppID, so
/// with two games running this picks one arbitrarily - but deterministically,
/// in the order the observer reports them, and a game with no AppID never
/// takes the slot from one that has it.
pub fn appid_for_active(active: &[String], appid_of: &dyn Fn(&str) -> Option<String>) -> String {
    for exe in active {
        if let Some(appid) = appid_of(exe) {
            if !appid.is_empty() {
                return appid;
            }
        }
    }
    String::new()
}

/// Rebuild an incident from a row of the on-disk history.
///
/// The defaults differ per field and are not interchangeable. A kind is
/// `unknown` because every reader switches on it and an empty one would match
/// nothing; a detail is empty because it is prose and a placeholder would be
/// read as the incident's own words. `game_pid` has no default at all - a
/// missing pid is missing, and inventing a zero would name process zero.
pub fn incident_from_history(row: &Value) -> Value {
    let field = |key: &str| row.get(key).cloned();
    let mut out = Map::new();
    out.insert(
        "kind".into(),
        field("kind").unwrap_or_else(|| Value::String("unknown".into())),
    );
    out.insert(
        "detail".into(),
        field("detail").unwrap_or_else(|| Value::String(String::new())),
    );
    out.insert(
        "game".into(),
        field("game").unwrap_or_else(|| Value::String(String::new())),
    );
    out.insert("game_pid".into(), field("game_pid").unwrap_or(Value::Null));
    out.insert(
        "metrics_window".into(),
        field("metrics_window").unwrap_or_else(|| Value::Array(Vec::new())),
    );
    out.insert(
        "logs_tail".into(),
        field("logs_tail").unwrap_or_else(|| Value::Array(Vec::new())),
    );
    out.insert(
        "active_tweaks".into(),
        field("active_tweaks").unwrap_or_else(|| Value::Object(Map::new())),
    );
    Value::Object(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_log_smaller_than_the_window_is_read_whole() {
        assert_eq!(analyse_from(0), 0);
        assert_eq!(analyse_from(1_000), 0);
        assert_eq!(analyse_from(ANALYSE_TAIL_BYTES), 0);
    }

    #[test]
    fn a_bigger_log_is_read_from_its_tail() {
        assert_eq!(analyse_from(ANALYSE_TAIL_BYTES + 1), 1);
        assert_eq!(analyse_from(5_000_000), 5_000_000 - ANALYSE_TAIL_BYTES);
    }

    #[test]
    fn the_first_active_game_with_an_app_id_wins() {
        let ids = |exe: &str| match exe {
            "Wow.exe" => None,
            "rs2client" => Some("1343400".to_string()),
            _ => Some("999".to_string()),
        };
        assert_eq!(
            appid_for_active(&["Wow.exe".into(), "rs2client".into()], &ids),
            "1343400"
        );
        assert_eq!(
            appid_for_active(&["other".into(), "rs2client".into()], &ids),
            "999",
            "the observer's order decides it"
        );
    }

    #[test]
    fn a_game_with_an_empty_app_id_does_not_take_the_slot() {
        let ids = |exe: &str| match exe {
            "blank" => Some(String::new()),
            _ => Some("1343400".to_string()),
        };
        assert_eq!(
            appid_for_active(&["blank".into(), "rs2client".into()], &ids),
            "1343400"
        );
    }

    #[test]
    fn nothing_running_means_no_app_id() {
        assert_eq!(appid_for_active(&[], &|_| None), "");
        assert_eq!(appid_for_active(&["x".into()], &|_| None), "");
    }

    #[test]
    fn a_history_row_is_rebuilt_field_for_field() {
        let row = serde_json::json!({
            "kind": "gpu_fault", "detail": "device lost", "game": "Wow.exe",
            "game_pid": 4242, "metrics_window": [{"t": 1}],
            "logs_tail": ["a"], "active_tweaks": {"governor": "performance"},
            "ts": "ignored",
        });
        let out = incident_from_history(&row);
        assert_eq!(out["kind"], "gpu_fault");
        assert_eq!(out["game_pid"], 4242);
        assert_eq!(out["logs_tail"], serde_json::json!(["a"]));
        assert!(out.get("ts").is_none(), "only the fields it asks for");
    }

    #[test]
    fn each_missing_field_takes_its_own_default() {
        // Not interchangeable: a kind is switched on, a detail is prose, and
        // a missing pid is missing rather than process zero.
        let out = incident_from_history(&serde_json::json!({}));
        assert_eq!(out["kind"], "unknown");
        assert_eq!(out["detail"], "");
        assert_eq!(out["game"], "");
        assert_eq!(out["game_pid"], Value::Null);
        assert_eq!(out["metrics_window"], serde_json::json!([]));
        assert_eq!(out["logs_tail"], serde_json::json!([]));
        assert_eq!(out["active_tweaks"], serde_json::json!({}));
    }

    #[test]
    fn a_field_that_is_present_and_null_is_kept_as_it_is() {
        // `.get(key, default)` hands back the null it found; only an absent
        // key takes the default.
        let out = incident_from_history(&serde_json::json!({"kind": null}));
        assert_eq!(out["kind"], Value::Null);
    }
}
