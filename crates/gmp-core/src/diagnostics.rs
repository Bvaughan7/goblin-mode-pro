//! Throttle assessment: which conditions count as an incident, and when to
//! say so again.
//!
//! A port of the pure slice of `src/goblinmode/diagnostics.py`. Reading hwmon,
//! RAPL and nvidia-smi stays in Python; what moves is everything downstream of
//! a `Sample`, which is where the judgement lives.
//!
//! The whole module exists to answer one question well: this machine is
//! throttling, is that worth interrupting someone mid-raid about? Almost every
//! constant here is a "no" - an isolated counter tick, a dGPU at its power cap,
//! a chronic condition already reported - and the tests below are mostly about
//! NOT firing.

use serde::{Deserialize, Serialize};

use crate::round::one_dp;

/// Re-raise the same kind of incident at most this often.
pub const REMIND_SECONDS: f64 = 180.0;

/// Some conditions are chronic on a thermally marginal laptop. Remind far less
/// often for those, so a warm three-hour raid is not a stream of popups.
pub fn remind_seconds_for(kind: &str) -> f64 {
    match kind {
        "thermal_throttle" => 900.0,
        _ => REMIND_SECONDS,
    }
}

/// An episode is only over after this long with no recurrence. Without the
/// grace window a single throttle-free sample ends the episode and the very
/// next counter tick reads as a fresh onset - which is notification spam.
pub const EPISODE_GRACE_SECONDS: f64 = 90.0;

/// CPU package thermal throttling counts as an issue only once the counter has
/// ticked in this many samples across the trailing window. Comet Lake and
/// Tiger Lake laptops nick the counter under any turbo load; an isolated tick
/// costs no measurable frame rate. This is the "throttling but performance was
/// fine" case, and it should stay silent.
pub const THROTTLE_WINDOW_SECONDS: f64 = 20.0;
pub const THROTTLE_MIN_HITS: usize = 5;

/// NVML clock event-reason bits worth alerting on, in the order they are
/// reported. `SwPowerCap` (0x4) is deliberately absent: a laptop dGPU is
/// power-capped under any real load, and that is normal.
///
/// Ordered, and joined in this order, because it is user-visible text.
pub const GPU_BAD_BITS: &[(i128, &str)] = &[
    (0x8, "GPU HW slowdown"),
    (0x20, "GPU SW thermal slowdown"),
    (0x40, "GPU HW thermal slowdown"),
    (0x80, "GPU HW power-brake slowdown"),
];

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Sample {
    pub t: f64,
    #[serde(default)]
    pub cpu_temp: Option<f64>,
    #[serde(default)]
    pub cpu_load: f64,
    #[serde(default)]
    pub pkg_power_w: Option<f64>,
    #[serde(default)]
    pub pl1_w: Option<f64>,
    #[serde(default)]
    pub gpu_temp: Option<f64>,
    #[serde(default)]
    pub gpu_throttle_reasons: String,
    #[serde(default)]
    pub cpu_throttled: bool,
}

/// `int(raw, 16)` / `int(raw)`, with Python's rules rather than Rust's.
///
/// The difference is not academic: nvidia-smi's reason field has been seen
/// blank, decimal, `0x`-prefixed in either case, and absent. Python's `int`
/// also accepts a leading sign and single underscores between digits, and
/// Rust's `from_str_radix` accepts neither, so this spells the rule out. Any
/// failure is 0 - an unreadable reason field must never invent a throttle.
pub fn parse_gpu_reasons(raw: &str) -> i128 {
    let raw = raw.trim();
    if raw.is_empty() {
        return 0;
    }
    let lower = raw.to_ascii_lowercase();
    let (body, radix) = if lower.starts_with("0x") {
        (&raw[2..], 16)
    } else {
        (raw, 10)
    };
    py_int(body, radix).unwrap_or(0)
}

/// Digits in `radix`, optionally signed, with single underscores permitted
/// between digits - the grammar Python's `int()` accepts.
fn py_int(body: &str, radix: u32) -> Option<i128> {
    let (negative, digits) = match body.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, body.strip_prefix('+').unwrap_or(body)),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') {
        return None;
    }
    if digits.contains("__") {
        return None;
    }
    let mut value: i128 = 0;
    for ch in digits.chars() {
        if ch == '_' {
            continue;
        }
        let digit = ch.to_digit(radix)?;
        value = value
            .checked_mul(i128::from(radix))?
            .checked_add(digit.into())?;
    }
    Some(if negative { -value } else { value })
}

/// Python's `if x:` on an `Optional[float]`, which 0.0 does not satisfy.
///
/// It decides whether a temperature is appended to the message, so a probe
/// reading exactly 0 produces no "(0°C)" suffix in either implementation.
fn truthy(value: Option<f64>) -> Option<f64> {
    value.filter(|v| *v != 0.0)
}

/// The rolling state the assessment needs between samples.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Engine {
    /// `(t, throttled)` inside the trailing window.
    #[serde(default)]
    pub throttle_hits: Vec<(f64, bool)>,
    /// kind -> when it was last reported.
    #[serde(default)]
    pub incident_seen: Vec<(String, f64)>,
    /// kind -> when it was last observed.
    #[serde(default)]
    pub issue_last_seen: Vec<(String, f64)>,
}

fn get(pairs: &[(String, f64)], key: &str) -> Option<f64> {
    pairs.iter().find(|(k, _)| k == key).map(|(_, v)| *v)
}

fn set(pairs: &mut Vec<(String, f64)>, key: &str, value: f64) {
    match pairs.iter_mut().find(|(k, _)| k == key) {
        Some(slot) => slot.1 = value,
        None => pairs.push((key.to_owned(), value)),
    }
}

fn remove(pairs: &mut Vec<(String, f64)>, key: &str) {
    pairs.retain(|(k, _)| k != key);
}

impl Engine {
    /// The conditions currently true, as `(kind, detail)` in report order.
    ///
    /// Mutates the throttle window, exactly as the Python does - the window is
    /// advanced by asking the question, not on a timer.
    pub fn current_issues(&mut self, s: &Sample) -> Vec<(String, String)> {
        let mut issues = Vec::new();

        self.throttle_hits.push((s.t, s.cpu_throttled));
        let cutoff = s.t - THROTTLE_WINDOW_SECONDS;
        while self.throttle_hits.first().is_some_and(|(t, _)| *t < cutoff) {
            self.throttle_hits.remove(0);
        }
        let hits = self.throttle_hits.iter().filter(|(_, hit)| *hit).count();
        if hits >= THROTTLE_MIN_HITS {
            let hot = truthy(s.cpu_temp).map_or(String::new(), |t| format!(" ({t:.0}°C)"));
            issues.push((
                "thermal_throttle".to_string(),
                format!(
                    "CPU package thermal throttling{hot} — {hits} throttle events in the last {}s",
                    THROTTLE_WINDOW_SECONDS as i64
                ),
            ));
        }

        if let (Some(power), Some(pl1)) = (s.pkg_power_w, s.pl1_w) {
            // Not `>= pl1`: RAPL reports the average over the interval, which
            // sits a hair under the cap even while pinned to it.
            if s.cpu_load > 60.0 && power >= pl1 * 0.98 {
                issues.push((
                    "power_limit".to_string(),
                    format!("CPU package power pinned at PL1 ({pl1:.0} W) under load"),
                ));
            }
        }

        let bits = parse_gpu_reasons(&s.gpu_throttle_reasons);
        let bad: Vec<&str> = GPU_BAD_BITS
            .iter()
            .filter(|(bit, _)| bits & bit != 0)
            .map(|(_, label)| *label)
            .collect();
        if !bad.is_empty() {
            let hot = truthy(s.gpu_temp).map_or(String::new(), |t| format!(" ({t:.0}°C)"));
            issues.push(("gpu_throttle".to_string(), bad.join(", ") + &hot));
        }
        issues
    }

    /// One `(kind, detail)` per *episode* of a condition, or nothing.
    ///
    /// Fires on onset, then at most every [`remind_seconds_for`] while it
    /// persists. An episode ends only after [`EPISODE_GRACE_SECONDS`] with no
    /// recurrence, so a momentary gap in a chronic condition does not read as
    /// a fresh onset and re-notify.
    ///
    /// Returns at most one issue per call even when several are true, matching
    /// the Python. The rest are not lost - they are still true on the next
    /// sample a second later.
    pub fn assess(&mut self, s: &Sample) -> Option<(String, String)> {
        let issues = self.current_issues(s);
        let now = s.t;

        for (kind, _) in &issues {
            set(&mut self.issue_last_seen, kind, now);
        }

        let expired: Vec<String> = self
            .incident_seen
            .iter()
            .map(|(kind, _)| kind.clone())
            .filter(|kind| {
                now - get(&self.issue_last_seen, kind).unwrap_or(0.0) >= EPISODE_GRACE_SECONDS
            })
            .collect();
        for kind in expired {
            remove(&mut self.incident_seen, &kind);
            remove(&mut self.issue_last_seen, &kind);
        }

        for (kind, detail) in issues {
            let remind = remind_seconds_for(&kind);
            let due = match get(&self.incident_seen, &kind) {
                None => true,
                Some(last) => now - last >= remind,
            };
            if due {
                set(&mut self.incident_seen, &kind, now);
                return Some((kind, detail));
            }
        }
        None
    }
}

/// MB/s between two counter readings, or `None` if the pair says nothing.
///
/// `None` covers a first reading, a clock that did not advance, and a counter
/// that went backwards - the last being a wrap or a device that vanished and
/// came back. Reporting a negative or astronomical rate would look like a
/// disk stall in the incident log.
pub fn rate_mbps(prev: Option<(f64, i64)>, now: f64, value: i64) -> Option<f64> {
    let (prev_t, prev_value) = prev?;
    if now <= prev_t {
        return None;
    }
    let delta = value.checked_sub(prev_value)?;
    if delta < 0 {
        return None;
    }
    Some(one_dp(delta as f64 / (now - prev_t) / 1_000_000.0))
}

/// Watts between two RAPL energy readings, or `None`.
///
/// Same shape as [`rate_mbps`] but NOT the same function: RAPL's `dt <= 0`
/// test is written as an equality-tolerant compare after the subtraction,
/// and the counter wrap is a real event on long sessions rather than a
/// defensive check.
pub fn package_power(prev: Option<(f64, i64)>, now: f64, energy_uj: i64) -> Option<f64> {
    let (prev_t, prev_energy) = prev?;
    let dt = now - prev_t;
    let de = energy_uj.checked_sub(prev_energy)?;
    if dt <= 0.0 || de < 0 {
        return None;
    }
    Some(one_dp(de as f64 / dt / 1_000_000.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(t: f64) -> Sample {
        Sample {
            t,
            ..Default::default()
        }
    }

    fn throttling(t: f64) -> Sample {
        Sample {
            t,
            cpu_throttled: true,
            cpu_temp: Some(96.0),
            ..Default::default()
        }
    }

    #[test]
    fn an_isolated_throttle_tick_is_not_an_incident() {
        // The whole point of the window. A Tiger Lake laptop nicks this
        // counter under any turbo load and the frame rate never moves.
        let mut engine = Engine::default();
        assert_eq!(engine.assess(&throttling(1.0)), None);
        for t in 2..=4 {
            assert_eq!(engine.assess(&throttling(f64::from(t))), None);
        }
    }

    #[test]
    fn sustained_throttling_is() {
        let mut engine = Engine::default();
        let mut fired = None;
        for t in 1..=5 {
            fired = engine.assess(&throttling(f64::from(t)));
        }
        let (kind, detail) = fired.expect("five hits inside the window");
        assert_eq!(kind, "thermal_throttle");
        assert!(detail.contains("(96°C)"), "{detail}");
        assert!(detail.contains("5 throttle events"), "{detail}");
    }

    #[test]
    fn hits_that_fall_outside_the_window_stop_counting() {
        let mut engine = Engine::default();
        for t in 1..=4 {
            engine.assess(&throttling(f64::from(t)));
        }
        // 25s later the four earlier hits are outside the 20s window, so this
        // is hit number one again, not number five.
        assert_eq!(engine.assess(&throttling(29.0)), None);
    }

    #[test]
    fn a_zero_temperature_is_left_out_of_the_message() {
        // Python's `if s.cpu_temp:` - a probe reading exactly 0 is treated as
        // no reading, not as 0°C.
        let mut engine = Engine::default();
        let mut last = None;
        for t in 1..=5 {
            let mut s = throttling(f64::from(t));
            s.cpu_temp = Some(0.0);
            last = engine.assess(&s);
        }
        let (_, detail) = last.expect("fired");
        assert!(!detail.contains("°C"), "{detail}");
    }

    #[test]
    fn a_chronic_condition_reminds_rarely_not_every_three_minutes() {
        assert_eq!(remind_seconds_for("thermal_throttle"), 900.0);
        assert_eq!(remind_seconds_for("power_limit"), REMIND_SECONDS);
        assert_eq!(remind_seconds_for("gpu_throttle"), REMIND_SECONDS);
    }

    #[test]
    fn a_gap_shorter_than_the_grace_window_is_the_same_episode() {
        // Without this, one throttle-free sample ends the episode and the next
        // tick reads as a fresh onset.
        let mut engine = Engine::default();
        for t in 1..=5 {
            engine.assess(&throttling(f64::from(t)));
        }
        engine.assess(&sample(6.0)); // quiet
        for t in 7..=11 {
            assert_eq!(
                engine.assess(&throttling(f64::from(t))),
                None,
                "re-notified inside the same episode"
            );
        }
    }

    #[test]
    fn a_gap_longer_than_the_grace_window_is_a_new_episode() {
        let mut engine = Engine::default();
        for t in 1..=5 {
            engine.assess(&throttling(f64::from(t)));
        }
        // Long enough for the episode to actually end, which is later than
        // it looks: the trailing window keeps the condition TRUE for 20s
        // after the last tick, so the 90s grace period only starts counting
        // from t=25, not from t=5.
        for t in 6..=130 {
            engine.assess(&sample(f64::from(t)));
        }
        let mut fired = None;
        for t in 101..=105 {
            fired = engine.assess(&throttling(f64::from(t)));
        }
        assert!(fired.is_some(), "a new episode should be reported");
    }

    #[test]
    fn power_at_the_limit_needs_load_behind_it() {
        // An idle machine sitting at its cap is not throttling anything.
        let mut engine = Engine::default();
        let at_limit = |load: f64| Sample {
            t: 1.0,
            cpu_load: load,
            pkg_power_w: Some(45.0),
            pl1_w: Some(45.0),
            ..Default::default()
        };
        assert_eq!(
            engine.current_issues(&at_limit(60.0)).len(),
            0,
            "60 is not >60"
        );
        assert_eq!(engine.current_issues(&at_limit(60.1)).len(), 1);
    }

    #[test]
    fn power_just_under_the_cap_still_counts() {
        // RAPL averages over the interval, so a pinned package reads a hair
        // under its own limit. 0.98 is the tolerance.
        let mut engine = Engine::default();
        let at = |w: f64| Sample {
            t: 1.0,
            cpu_load: 90.0,
            pkg_power_w: Some(w),
            pl1_w: Some(45.0),
            ..Default::default()
        };
        assert_eq!(engine.current_issues(&at(44.1)).len(), 1, "45*0.98 = 44.1");
        assert_eq!(engine.current_issues(&at(44.0)).len(), 0);
    }

    #[test]
    fn a_power_capped_dgpu_is_not_an_incident() {
        // SwPowerCap (0x4) is absent from the table on purpose: a laptop dGPU
        // is power-capped under any real load.
        let mut engine = Engine::default();
        let s = Sample {
            t: 1.0,
            gpu_throttle_reasons: "0x4".into(),
            ..Default::default()
        };
        assert!(engine.current_issues(&s).is_empty());
    }

    #[test]
    fn gpu_reasons_are_joined_in_table_order() {
        let mut engine = Engine::default();
        let s = Sample {
            t: 1.0,
            gpu_throttle_reasons: "0xC8".into(), // 0x8 | 0x40 | 0x80
            gpu_temp: Some(88.0),
            ..Default::default()
        };
        let issues = engine.current_issues(&s);
        assert_eq!(
            issues[0].1,
            "GPU HW slowdown, GPU HW thermal slowdown, GPU HW power-brake slowdown (88°C)"
        );
    }

    #[test]
    fn an_unreadable_reason_field_never_invents_a_throttle() {
        for raw in [
            "",
            "  ",
            "N/A",
            "[N/A]",
            "Not Supported",
            "0x",
            "0xZZ",
            "--",
            "-",
        ] {
            assert_eq!(parse_gpu_reasons(raw), 0, "{raw:?}");
        }
    }

    #[test]
    fn reason_fields_parse_the_way_python_parses_them() {
        assert_eq!(parse_gpu_reasons("0x8"), 8);
        assert_eq!(
            parse_gpu_reasons("0X8"),
            8,
            "nvidia-smi has used both cases"
        );
        assert_eq!(parse_gpu_reasons("8"), 8);
        assert_eq!(parse_gpu_reasons(" 0x8 "), 8);
        assert_eq!(parse_gpu_reasons("0x1_0"), 16, "int() allows the separator");
        assert_eq!(parse_gpu_reasons("1_0"), 10);
        assert_eq!(parse_gpu_reasons("+8"), 8);
        assert_eq!(parse_gpu_reasons("-8"), -8);
        assert_eq!(parse_gpu_reasons("-0x8"), 0, "int('-0x8') is a ValueError");
        assert_eq!(parse_gpu_reasons("0x1__0"), 0, "doubled separator is not");
        assert_eq!(parse_gpu_reasons("0x8f"), 0x8f);
    }

    #[test]
    fn only_one_issue_is_reported_per_sample() {
        // Several conditions can be true at once; the rest are still true a
        // second later. This keeps a single sample from firing three popups.
        let mut engine = Engine::default();
        let mut last = None;
        for t in 1..=5 {
            last = engine.assess(&Sample {
                t: f64::from(t),
                cpu_throttled: true,
                cpu_load: 99.0,
                pkg_power_w: Some(45.0),
                pl1_w: Some(45.0),
                gpu_throttle_reasons: "0x8".into(),
                ..Default::default()
            });
        }
        assert_eq!(last.expect("fired").0, "thermal_throttle");
    }

    #[test]
    fn a_counter_that_goes_backwards_reports_nothing() {
        // A wrap, or a device that vanished and came back. Reporting the
        // negative rate would look like a disk stall in the incident log.
        assert_eq!(rate_mbps(Some((1.0, 500)), 2.0, 100), None);
        assert_eq!(package_power(Some((1.0, 500)), 2.0, 100), None);
    }

    #[test]
    fn a_clock_that_did_not_advance_reports_nothing() {
        assert_eq!(rate_mbps(Some((2.0, 100)), 2.0, 500), None);
        assert_eq!(package_power(Some((2.0, 100)), 2.0, 500), None);
        assert_eq!(rate_mbps(Some((3.0, 100)), 2.0, 500), None);
    }

    #[test]
    fn the_first_reading_reports_nothing() {
        assert_eq!(rate_mbps(None, 1.0, 100), None);
        assert_eq!(package_power(None, 1.0, 100), None);
    }

    #[test]
    fn rates_round_to_one_place_the_way_python_does() {
        // Half-to-even, via formatting - see round.rs. 12.5 -> 12.4 here is
        // not a typo: 2_500_000 / 2 / 1e6 is 1.25, and .1f of 1.25 is 1.2.
        assert_eq!(rate_mbps(Some((0.0, 0)), 2.0, 2_500_000), Some(1.2));
        assert_eq!(rate_mbps(Some((0.0, 0)), 2.0, 7_500_000), Some(3.8));
    }
}
