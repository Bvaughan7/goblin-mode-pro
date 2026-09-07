//! The Prometheus textfile collector output.
//!
//! One `.prom` document per status snapshot, in the format node_exporter's
//! textfile collector reads. Text, not data: the file is parsed by something
//! that is not this project, so the bytes are the contract - the HELP and TYPE
//! lines, the order, and the way a float is spelled.
//!
//! A metric with nothing to report is left OUT rather than exported as zero.
//! Prometheus reads an absent series as a gap and a zero as a measurement, and
//! a GPU that could not be read is a gap.

use serde_json::Value;

use crate::round::py_g;

/// How this file spells a number.
///
/// Exposed because it is the only reason two implementations of this document
/// could disagree while both looking right, so it is worth being able to ask
/// about on its own. `round` itself stays crate-private: it is CPython's float
/// behaviour, not an API.
pub fn format_value(value: f64) -> String {
    py_g(value)
}

/// Every metric, in the order the file writes them, with its type and help.
///
/// The order is the order the Python builds its dictionary in, and Python
/// dictionaries keep insertion order - so this is not a cosmetic choice that
/// could be sorted.
const METRICS: &[(&str, &str, &str)] = &[
    (
        "goblin_mode_pro_master_enabled",
        "gauge",
        "Whether optimizations are enabled (1) or off (0).",
    ),
    (
        "goblin_mode_pro_boosting",
        "gauge",
        "Whether a game is currently boosted (1) or idle (0).",
    ),
    (
        "goblin_mode_pro_forced_boost",
        "gauge",
        "Whether performance mode was forced on manually.",
    ),
    (
        "goblin_mode_pro_helper_available",
        "gauge",
        "Whether the privileged helper is reachable.",
    ),
    (
        "goblin_mode_pro_limited_mode",
        "gauge",
        "Whether GMP is running in limited mode (helper down).",
    ),
    (
        "goblin_mode_pro_active_games",
        "gauge",
        "Number of games currently detected as running.",
    ),
    (
        "goblin_mode_pro_health_score",
        "gauge",
        "Pre-flight system readiness score, 0-10.",
    ),
    (
        "goblin_mode_pro_cpu_temp_celsius",
        "gauge",
        "CPU package temperature.",
    ),
    (
        "goblin_mode_pro_cpu_load_percent",
        "gauge",
        "Aggregate CPU load.",
    ),
    (
        "goblin_mode_pro_package_power_watts",
        "gauge",
        "CPU package power draw.",
    ),
    (
        "goblin_mode_pro_gpu_load_percent",
        "gauge",
        "GPU utilisation.",
    ),
    (
        "goblin_mode_pro_gpu_temp_celsius",
        "gauge",
        "GPU temperature.",
    ),
    (
        "goblin_mode_pro_fps_avg",
        "gauge",
        "Average FPS over the last 60s window (watchdog-enabled games).",
    ),
    (
        "goblin_mode_pro_fps_min",
        "gauge",
        "Minimum FPS over the last 60s window.",
    ),
    (
        "goblin_mode_pro_fps_1pct_low",
        "gauge",
        "1% low FPS over the last 60s window.",
    ),
];

/// A field as a number, or nothing.
///
/// A boolean is NOT a number here even though Python would happily make one:
/// `True` becoming `1` in a temperature series would read as a measurement.
/// A string that spells a number IS one, which is Python's `float()` being
/// permissive and is reproduced rather than tightened - a status field that
/// arrives as `"42"` means 42 to one implementation and must mean it to both.
pub fn num(value: &Value) -> Option<f64> {
    match value {
        // The catch-all below would answer the same for a boolean. It is
        // named here anyway, because "a boolean is not a number" is the rule
        // this function exists for and a reader should not have to infer it
        // from the absence of an arm.
        Value::Bool(_) | Value::Null => None,
        Value::Number(n) => n.as_f64(),
        Value::String(text) => python_float(text),
        _ => None,
    }
}

/// `float(str)` with Python's rules: surrounding whitespace is allowed, and
/// so are underscores between digits.
fn python_float(text: &str) -> Option<f64> {
    let trimmed = text.trim_matches(|c: char| c.is_whitespace());
    if trimmed.is_empty() {
        return None;
    }
    // Python allows `1_000.5` and refuses `_1`, `1_`, `1__0` and `1_.0`.
    let cleaned = if trimmed.contains('_') {
        let bytes: Vec<char> = trimmed.chars().collect();
        let ok = bytes.iter().enumerate().all(|(i, c)| {
            *c != '_'
                || (i > 0
                    && i + 1 < bytes.len()
                    && bytes[i - 1].is_ascii_digit()
                    && bytes[i + 1].is_ascii_digit())
        });
        if !ok {
            return None;
        }
        trimmed.replace('_', "")
    } else {
        trimmed.to_string()
    };
    // Rust accepts the same shapes Python does here, plus nothing extra that
    // a status field could plausibly hold.
    cleaned.parse::<f64>().ok()
}

/// Truthiness and length the way Python reads a field that should be a list.
fn count(value: &Value) -> usize {
    match value {
        Value::Array(rows) => rows.len(),
        Value::Object(map) => map.len(),
        // `len()` of a string is its code points, and the Python does not
        // check the type before asking.
        Value::String(text) => text.chars().count(),
        _ => 0,
    }
}

/// The whole `.prom` document for one status snapshot.
pub fn render(status: &Value) -> String {
    let field = |key: &str| status.get(key).unwrap_or(&Value::Null).clone();
    let games = count(&field("active_games"));
    let flag = |key: &str, default: bool| {
        let value = field(key);
        if value.is_null() {
            default
        } else {
            crate::config::truthy(&value)
        }
    };
    let forced = flag("forced_boost", false);

    let sample = field("latest_sample");
    let fps = field("fps");
    let sub = |parent: &Value, key: &str| num(parent.get(key).unwrap_or(&Value::Null));

    let values: Vec<Option<f64>> = vec![
        Some(f64::from(u8::from(flag("master_enabled", true)))),
        Some(f64::from(u8::from(games > 0 || forced))),
        Some(f64::from(u8::from(forced))),
        Some(f64::from(u8::from(flag("helper_available", false)))),
        Some(f64::from(u8::from(flag("limited_mode", false)))),
        Some(games as f64),
        sub(&field("health"), "score"),
        sub(&sample, "cpu_temp"),
        sub(&sample, "cpu_load"),
        sub(&sample, "pkg_power_w"),
        sub(&sample, "gpu_load"),
        sub(&sample, "gpu_temp"),
        sub(&fps, "fps_avg"),
        sub(&fps, "fps_min"),
        sub(&fps, "fps_1low"),
    ];

    let blocks: Vec<String> = METRICS
        .iter()
        .zip(values)
        .filter_map(|((name, kind, help), value)| {
            value.map(|v| {
                format!(
                    "# HELP {name} {help}\n# TYPE {name} {kind}\n{name} {}",
                    py_g(v)
                )
            })
        })
        .collect();
    format!("{}\n", blocks.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_metric_with_nothing_to_report_is_left_out_entirely() {
        // Not exported as zero: Prometheus reads an absent series as a gap
        // and a zero as a measurement, and an unread GPU is a gap.
        let text = render(&serde_json::json!({}));
        assert!(!text.contains("goblin_mode_pro_gpu_temp_celsius"));
        assert!(!text.contains("goblin_mode_pro_health_score"));
    }

    #[test]
    fn the_six_that_are_always_known_are_always_written() {
        let text = render(&serde_json::json!({}));
        for name in [
            "goblin_mode_pro_master_enabled",
            "goblin_mode_pro_boosting",
            "goblin_mode_pro_forced_boost",
            "goblin_mode_pro_helper_available",
            "goblin_mode_pro_limited_mode",
            "goblin_mode_pro_active_games",
        ] {
            assert!(text.contains(&format!("{name} ")), "{name} missing");
        }
    }

    #[test]
    fn the_master_switch_defaults_to_on_and_the_rest_to_off() {
        // An older daemon may not send every key. Reading a missing master
        // switch as "off" would report the tool disabled on every such
        // snapshot.
        let text = render(&serde_json::json!({}));
        assert!(text.contains("goblin_mode_pro_master_enabled 1"));
        assert!(text.contains("goblin_mode_pro_helper_available 0"));
    }

    #[test]
    fn boosting_is_a_game_running_or_the_switch_held_down() {
        let idle = render(&serde_json::json!({"active_games": []}));
        assert!(idle.contains("goblin_mode_pro_boosting 0"));
        let playing = render(&serde_json::json!({"active_games": ["Wow.exe"]}));
        assert!(playing.contains("goblin_mode_pro_boosting 1"));
        let forced = render(&serde_json::json!({"forced_boost": true}));
        assert!(forced.contains("goblin_mode_pro_boosting 1"));
    }

    #[test]
    fn every_block_carries_its_help_and_type() {
        let text = render(&serde_json::json!({}));
        let expected = concat!(
            "# HELP goblin_mode_pro_active_games ",
            "Number of games currently detected as running.\n",
            "# TYPE goblin_mode_pro_active_games gauge\n",
            "goblin_mode_pro_active_games 0"
        );
        assert!(text.contains(expected), "{text}");
    }

    #[test]
    fn the_document_ends_with_exactly_one_newline() {
        let text = render(&serde_json::json!({}));
        assert!(text.ends_with('\n'));
        assert!(!text.ends_with("\n\n"));
    }

    #[test]
    fn a_boolean_is_not_a_measurement() {
        // `float(True)` is 1.0 and would read as a temperature.
        let text = render(&serde_json::json!({"latest_sample": {"cpu_temp": true}}));
        assert!(!text.contains("goblin_mode_pro_cpu_temp_celsius"));
    }

    #[test]
    fn a_number_spelled_as_a_string_is_still_a_number() {
        let text = render(&serde_json::json!({"latest_sample": {"cpu_temp": "42.5"}}));
        assert!(text.contains("goblin_mode_pro_cpu_temp_celsius 42.5"));
    }

    #[test]
    fn a_field_that_is_not_a_number_at_all_is_a_gap() {
        for junk in [
            serde_json::json!("hot"),
            serde_json::json!([1]),
            serde_json::json!({"a": 1}),
            serde_json::Value::Null,
        ] {
            let text = render(&serde_json::json!({"latest_sample": {"cpu_temp": junk}}));
            assert!(!text.contains("goblin_mode_pro_cpu_temp_celsius"), "{text}");
        }
    }
}
