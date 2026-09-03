//! Per-session frame statistics and regression detection.
//!
//! A port of the pure half of `src/goblinmode/sessions.py`. `SessionTracker`
//! itself stays in Python for now: it owns a file, a monotonic clock and the
//! discovery of MangoHud logs by mtime, none of which belong in a crate whose
//! whole point is that it can be tested from fixture strings. What moves here
//! is the arithmetic - parsing a CSV, taking a percentile, and deciding
//! whether a session got worse.

use serde::{Deserialize, Serialize};

use crate::round::{half_even as round_half_even, one_dp as round1};

/// How many recent prior sessions form the comparison baseline.
pub const BASELINE_SESSIONS: usize = 6;
/// Need at least this many priors (with FPS stats) before flagging anything.
pub const BASELINE_MIN: usize = 3;
/// Fractional change from the baseline that counts as a regression.
pub const REGRESSION_FRAC: f64 = 0.10;
/// Ignore CSV files older than this (seconds) relative to session start.
pub const CSV_GRACE_BEFORE: f64 = 15.0;
/// Minimum FPS samples before the stats are considered meaningful.
pub const MIN_SAMPLES: usize = 30;

/// Nearest-rank percentile of a *sorted* slice; `q` in [0, 1].
pub fn percentile(values: &[f64], q: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let last = (values.len() - 1) as f64;
    let idx = round_half_even(q * last).clamp(0.0, last) as usize;
    values[idx]
}

/// The four series a MangoHud CSV can carry.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Series {
    pub fps: Vec<f64>,
    pub cpu_temp: Vec<f64>,
    pub gpu_temp: Vec<f64>,
    pub frametime_ms: Vec<f64>,
}

/// Parse one MangoHud CSV.
///
/// Takes the text rather than a path, so it can be tested from a fixture.
///
/// Two behaviours worth keeping straight, both inherited deliberately:
/// the header is whichever row first contains an `fps` column - MangoHud
/// writes two preamble lines before it - and the temperature and frametime
/// samples are only taken from rows whose FPS value was itself accepted, so
/// the four series can legitimately be different lengths.
pub fn parse_csv(text: &str) -> Series {
    let mut out = Series::default();
    let (mut fps_i, mut cpu_i, mut gpu_i, mut ft_i) = (None, None, None, None);

    for raw in text.lines() {
        let cells: Vec<&str> = raw.trim().split(',').collect();
        if fps_i.is_none() {
            let low: Vec<String> = cells.iter().map(|c| c.trim().to_lowercase()).collect();
            if let Some(i) = low.iter().position(|c| c == "fps") {
                fps_i = Some(i);
                cpu_i = low.iter().position(|c| c == "cpu_temp");
                gpu_i = low.iter().position(|c| c == "gpu_temp");
                ft_i = low.iter().position(|c| c == "frametime");
            }
            continue;
        }
        let fps_col = fps_i.unwrap();
        if cells.len() <= fps_col {
            continue;
        }
        let Ok(v) = cells[fps_col].parse::<f64>() else {
            continue;
        };
        if !(v > 0.0 && v < 1000.0) {
            continue;
        }
        out.fps.push(v);
        for (col, sink, lo, hi) in [
            (cpu_i, &mut out.cpu_temp, 0.0, 200.0),
            (gpu_i, &mut out.gpu_temp, 0.0, 200.0),
            (ft_i, &mut out.frametime_ms, 0.0, 2000.0),
        ] {
            let Some(col) = col else { continue };
            if cells.len() <= col {
                continue;
            }
            if let Ok(x) = cells[col].parse::<f64>() {
                if x > lo && x < hi {
                    sink.push(x);
                }
            }
        }
    }
    out
}

/// One prior session, as far as regression detection cares.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PriorSession {
    #[serde(default)]
    pub fps_1low: Option<f64>,
    #[serde(default)]
    pub fps_avg: Option<f64>,
}

/// A session that got measurably better or worse than the recent baseline.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Regression {
    /// "1% low" | "average FPS"
    pub metric: String,
    /// "regression" | "improvement"
    pub direction: String,
    /// Signed, relative to baseline. Negative means slower.
    pub change_pct: f64,
    pub baseline: f64,
    pub current: f64,
    pub sessions_compared: usize,
}

impl Regression {
    pub fn headline(&self, game: &str) -> String {
        let verb = if self.direction == "regression" {
            "dropped"
        } else {
            "gained"
        };
        format!(
            "{game}: {} {verb} {:.0}% vs your recent average ({:.0} vs {:.0} fps)",
            self.metric,
            self.change_pct.abs(),
            self.current,
            self.baseline
        )
    }
}

/// Compare this session's 1% low, then its average, against the baseline.
///
/// THE EARLY RETURN IS DELIBERATE. If the 1% low is within the threshold, this
/// reports nothing at all - it does not go on to consider average FPS. A
/// session whose 1% low held steady did not get worse in the way a player
/// notices, and reporting a change in the average after that would be noise.
/// Written out because it reads like a missing `continue`.
pub fn detect_regression(
    current_1low: Option<f64>,
    current_avg: Option<f64>,
    prior: &[PriorSession],
) -> Option<Regression> {
    for (metric, current, pick) in [
        ("1% low", current_1low, 0usize),
        ("average FPS", current_avg, 1usize),
    ] {
        let Some(current) = current else { continue };
        if current <= 0.0 {
            continue;
        }
        let from = prior.len().saturating_sub(BASELINE_SESSIONS);
        let mut history: Vec<f64> = prior[from..]
            .iter()
            .filter_map(|p| if pick == 0 { p.fps_1low } else { p.fps_avg })
            .filter(|v| *v > 0.0)
            .collect();
        if history.len() < BASELINE_MIN {
            continue;
        }
        history.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let baseline = history[history.len() / 2];
        if baseline <= 0.0 {
            continue;
        }
        let frac = (current - baseline) / baseline;
        if frac.abs() < REGRESSION_FRAC {
            return None; // stable on this metric - see the note above
        }
        return Some(Regression {
            metric: metric.to_owned(),
            direction: if frac < 0.0 {
                "regression"
            } else {
                "improvement"
            }
            .to_owned(),
            change_pct: round1(frac * 100.0),
            baseline: round1(baseline),
            current: round1(current),
            sessions_compared: history.len(),
        });
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEADER: &str = "os,cpu,gpu,ram,kernel,driver\nLinux,x,y,16,7.2,570\n";

    fn priors(low: f64, avg: f64, n: usize) -> Vec<PriorSession> {
        (0..n)
            .map(|_| PriorSession {
                fps_1low: Some(low),
                fps_avg: Some(avg),
            })
            .collect()
    }

    // ---- translated from tests/test_sessions.py --------------------------

    #[test]
    fn parse_csv_reads_fps_and_temps() {
        let rows: String = (0..10)
            .map(|_| "60.0,70.0,65.0,16.6\n".to_owned())
            .collect();
        let s = parse_csv(&format!("{HEADER}fps,cpu_temp,gpu_temp,frametime\n{rows}"));
        assert_eq!(s.fps.len(), 10);
        assert_eq!(s.cpu_temp, vec![70.0; 10]);
        assert_eq!(s.gpu_temp, vec![65.0; 10]);
    }

    #[test]
    fn parse_csv_ignores_garbage_and_out_of_range() {
        let s = parse_csv("os\nx\nfps,elapsed\n60,1\nNaN,2\n-3,3\n9999,4\n72,5\n");
        assert_eq!(s.fps, vec![60.0, 72.0]);
    }

    #[test]
    fn percentile_matches_the_python_nearest_rank() {
        let s: Vec<f64> = (1..=100).map(f64::from).collect();
        assert_eq!(percentile(&s, 0.0), 1.0);
        assert_eq!(percentile(&s, 1.0), 100.0);
        assert_eq!(percentile(&s, 0.5), 51.0);
    }

    #[test]
    fn flags_regression_below_baseline() {
        let reg = detect_regression(Some(60.0), Some(130.0), &priors(90.0, 140.0, 4)).unwrap();
        assert_eq!(reg.direction, "regression");
        assert!(reg.change_pct < 0.0);
    }

    #[test]
    fn flags_improvement_above_baseline() {
        let reg = detect_regression(Some(80.0), Some(130.0), &priors(60.0, 100.0, 4)).unwrap();
        assert_eq!(reg.direction, "improvement");
    }

    #[test]
    fn stable_is_not_flagged() {
        assert!(detect_regression(Some(88.0), Some(138.0), &priors(90.0, 140.0, 4)).is_none());
    }

    #[test]
    fn needs_minimum_history() {
        assert!(detect_regression(Some(40.0), Some(80.0), &priors(90.0, 140.0, 2)).is_none());
    }

    // ---- the two rounding traps ------------------------------------------

    #[test]
    fn the_median_of_six_rounds_half_to_even() {
        // round(0.5 * 5) is round(2.5): 2 in Python, 3 under f64::round. Six
        // samples at the median is not an exotic case - it is a short session.
        let s = vec![10.0, 20.0, 30.0, 40.0, 50.0, 60.0];
        assert_eq!(percentile(&s, 0.5), 30.0);
    }

    #[test]
    fn rounding_to_one_decimal_uses_the_exact_binary_value() {
        // 51.15 is really 51.1499999... so it rounds DOWN. Scaling by ten
        // first would push it up to 51.2, which is what the parity test
        // against Python caught.
        assert_eq!(round1(51.15), 51.1);
        assert_eq!(round1(66.75), 66.8);
        assert_eq!(round1(55.25), 55.2);
        assert_eq!(round1(48.05), 48.0);
    }

    #[test]
    fn a_stable_one_percent_low_suppresses_the_average_entirely() {
        // Reads like a missing `continue` and is not one.
        let prior: Vec<PriorSession> = (0..6)
            .map(|_| PriorSession {
                fps_1low: Some(100.0),
                fps_avg: Some(10.0),
            })
            .collect();
        assert!(detect_regression(Some(100.0), Some(1000.0), &prior).is_none());
    }

    #[test]
    fn an_empty_series_percentile_is_zero() {
        assert_eq!(percentile(&[], 0.5), 0.0);
    }

    #[test]
    fn the_headline_reads_as_a_sentence() {
        let reg = detect_regression(Some(50.0), None, &priors(60.0, 0.0, 6)).unwrap();
        assert_eq!(
            reg.headline("Wow.exe"),
            "Wow.exe: 1% low dropped 17% vs your recent average (50 vs 60 fps)"
        );
    }
}
