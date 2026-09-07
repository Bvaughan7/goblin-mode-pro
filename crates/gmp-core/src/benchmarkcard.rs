//! Comparing two benchmark runs.
//!
//! One row per metric, from the plain dicts a session history already holds.
//! `b` is the "after" side, so a row's `better` always points at whichever run
//! actually improved - which is not the same as whichever number is larger,
//! because for temperatures, frame time and stutter the improvement is
//! downwards.
//!
//! The report-card image stays in Python: it needs Cairo, and a picture is not
//! a thing two implementations can be diffed on.

use serde_json::{Map, Value};

use crate::round::{one_dp, two_dp};

/// Every metric worth comparing, in display order, with the label the user
/// reads. The order is the table's order, not an alphabetical one.
pub const METRICS: &[(&str, &str)] = &[
    ("fps_avg", "Average FPS"),
    ("fps_median", "Median FPS"),
    ("fps_1low", "1% low FPS"),
    ("fps_01low", "0.1% low FPS"),
    ("fps_p95", "95th %ile FPS"),
    ("fps_min", "Minimum FPS"),
    ("frametime_ms_avg", "Avg frame time (ms)"),
    ("frametime_stutter_pct", "Stutter (% of frames)"),
    ("cpu_temp_avg", "CPU temp avg (\u{b0}C)"),
    ("cpu_temp_max", "CPU temp peak (\u{b0}C)"),
    ("gpu_temp_avg", "GPU temp avg (\u{b0}C)"),
    ("gpu_temp_max", "GPU temp peak (\u{b0}C)"),
];

/// The metrics where a LOWER number is the improvement. Everything else is
/// higher-is-better.
const LOWER_IS_BETTER: &[&str] = &[
    "frametime_ms_avg",
    "frametime_stutter_pct",
    "cpu_temp_avg",
    "cpu_temp_max",
    "gpu_temp_avg",
    "gpu_temp_max",
];

/// One row per metric present in EITHER run.
///
/// A metric missing from both is left out; a metric present in one is a row
/// with a gap in it, because "this run did not measure that" is worth showing.
///
/// `delta_pct` is skipped when the "before" value is FALSY, not merely when it
/// is missing: a metric that was zero has no percentage to change by, and
/// dividing would be a division by zero rather than an infinite improvement.
/// It is `abs(a)` on the bottom so a negative baseline does not flip the sign
/// of the change.
///
/// That zero check cannot be caught by a test that goes through JSON, because
/// an infinity does not survive the trip - it serialises as null, which is
/// what skipping produces anyway. The guard is kept because the reasoning is
/// the point and the next caller may not be a serializer.
///
/// `better` is set only when the two differ. Equal runs have no winner, and
/// saying `a` because "not improved" would call a tie a regression.
pub fn diff_sessions(a: &Value, b: &Value) -> Vec<Value> {
    let mut rows = Vec::new();
    for (field, label) in METRICS {
        let va = number(a, field);
        let vb = number(b, field);
        if va.is_none() && vb.is_none() {
            continue;
        }
        let mut row = Map::new();
        row.insert("field".into(), Value::String((*field).to_string()));
        row.insert("label".into(), Value::String((*label).to_string()));
        row.insert("a".into(), value_or_null(va));
        row.insert("b".into(), value_or_null(vb));
        row.insert("delta".into(), Value::Null);
        row.insert("delta_pct".into(), Value::Null);
        row.insert("better".into(), Value::Null);

        if let (Some(va), Some(vb)) = (va, vb) {
            row["delta"] = json_number(two_dp(vb - va));
            if va != 0.0 {
                row["delta_pct"] = json_number(one_dp((vb - va) / va.abs() * 100.0));
            }
            let improved = if LOWER_IS_BETTER.contains(field) {
                vb < va
            } else {
                vb > va
            };
            if vb != va {
                row["better"] = Value::String(if improved { "b" } else { "a" }.to_string());
            }
        }
        rows.push(Value::Object(row));
    }
    rows
}

/// A session field as a number, or nothing. A field the session did not
/// measure is null; anything that is not a number is treated the same way.
fn number(session: &Value, field: &str) -> Option<f64> {
    session.get(field)?.as_f64()
}

fn value_or_null(value: Option<f64>) -> Value {
    match value {
        Some(v) => json_number(v),
        None => Value::Null,
    }
}

/// A float as JSON, keeping a whole number whole - `round` gives back a float
/// in Python too, so `2.0` stays `2.0` rather than becoming `2`.
fn json_number(value: f64) -> Value {
    serde_json::Number::from_f64(value).map_or(Value::Null, Value::Number)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session(pairs: &[(&str, f64)]) -> Value {
        let mut map = Map::new();
        for (key, value) in pairs {
            map.insert((*key).to_string(), json_number(*value));
        }
        Value::Object(map)
    }

    fn row<'a>(rows: &'a [Value], field: &str) -> &'a Value {
        rows.iter()
            .find(|r| r["field"] == field)
            .unwrap_or_else(|| panic!("no row for {field}"))
    }

    #[test]
    fn a_metric_neither_run_measured_is_not_a_row() {
        let rows = diff_sessions(&session(&[("fps_avg", 60.0)]), &session(&[]));
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["field"], "fps_avg");
    }

    #[test]
    fn a_metric_only_one_run_measured_is_a_row_with_a_gap() {
        let rows = diff_sessions(&session(&[("fps_avg", 60.0)]), &session(&[]));
        assert_eq!(rows[0]["b"], Value::Null);
        assert_eq!(rows[0]["delta"], Value::Null);
        assert_eq!(rows[0]["better"], Value::Null);
    }

    #[test]
    fn more_frames_is_better_and_cooler_is_better() {
        let rows = diff_sessions(
            &session(&[("fps_avg", 60.0), ("cpu_temp_max", 90.0)]),
            &session(&[("fps_avg", 90.0), ("cpu_temp_max", 80.0)]),
        );
        assert_eq!(row(&rows, "fps_avg")["better"], "b");
        assert_eq!(row(&rows, "cpu_temp_max")["better"], "b");
    }

    #[test]
    fn a_hotter_faster_run_wins_on_one_and_loses_on_the_other() {
        let rows = diff_sessions(
            &session(&[("fps_avg", 60.0), ("cpu_temp_max", 80.0)]),
            &session(&[("fps_avg", 90.0), ("cpu_temp_max", 90.0)]),
        );
        assert_eq!(row(&rows, "fps_avg")["better"], "b");
        assert_eq!(row(&rows, "cpu_temp_max")["better"], "a");
    }

    #[test]
    fn two_identical_runs_have_no_winner() {
        let rows = diff_sessions(
            &session(&[("fps_avg", 60.0)]),
            &session(&[("fps_avg", 60.0)]),
        );
        assert_eq!(rows[0]["better"], Value::Null);
        assert_eq!(rows[0]["delta"], json_number(0.0));
        assert_eq!(rows[0]["delta_pct"], json_number(0.0));
    }

    #[test]
    fn a_baseline_of_zero_has_no_percentage_to_change_by() {
        let rows = diff_sessions(
            &session(&[("fps_avg", 0.0)]),
            &session(&[("fps_avg", 60.0)]),
        );
        assert_eq!(rows[0]["delta"], json_number(60.0));
        assert_eq!(
            rows[0]["delta_pct"],
            Value::Null,
            "not an infinite improvement"
        );
        assert_eq!(rows[0]["better"], "b");
    }

    #[test]
    fn a_negative_baseline_does_not_flip_the_sign_of_the_change() {
        // `abs(a)` on the bottom. A run that went from -10 to -5 improved by
        // 50%, not by -50%.
        let rows = diff_sessions(
            &session(&[("cpu_temp_avg", -10.0)]),
            &session(&[("cpu_temp_avg", -5.0)]),
        );
        assert_eq!(rows[0]["delta_pct"], json_number(50.0));
    }

    #[test]
    fn the_rows_come_in_the_tables_order() {
        let all: Vec<(&str, f64)> = METRICS.iter().map(|(f, _)| (*f, 1.0)).collect();
        let rows = diff_sessions(&session(&all), &session(&all));
        let fields: Vec<&str> = rows.iter().map(|r| r["field"].as_str().unwrap()).collect();
        let expected: Vec<&str> = METRICS.iter().map(|(f, _)| *f).collect();
        assert_eq!(fields, expected);
    }

    #[test]
    fn no_label_carries_an_unformatted_escape() {
        // Every one of these goes straight into an f-string on both the CLI
        // and the GUI side. A printf escape is not an escape here, it is two
        // characters the user reads.
        for (_, label) in METRICS {
            assert!(!label.contains("%%"), "{label}");
        }
    }
}
