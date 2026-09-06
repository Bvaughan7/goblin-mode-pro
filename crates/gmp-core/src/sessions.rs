//! Per-session frame statistics and regression detection.
//!
//! A port of the pure half of `src/goblinmode/sessions.py`. `SessionTracker`
//! itself stays in Python for now: it owns a file, a monotonic clock and the
//! discovery of MangoHud logs by mtime, none of which belong in a crate whose
//! whole point is that it can be tested from fixture strings. What moves here
//! is the arithmetic - parsing a CSV, taking a percentile, and deciding
//! whether a session got worse.

use serde::{Deserialize, Serialize};

use crate::round::{half_even as round_half_even, one_dp as round1, py_sum, two_dp as round2};

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
        // Trimmed because Python's float() accepts surrounding whitespace and
        // Rust's parse() does not - " 60 " is a reading there and an error
        // here. The same trap caught the cpu-list parser in capabilities.
        let Ok(v) = cells[fps_col].trim().parse::<f64>() else {
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
            if let Ok(x) = cells[col].trim().parse::<f64>() {
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

/// A session that has started but not yet ended.
///
/// The clock readings the tracker takes at `start` stay with the caller: this
/// carries only the wall-clock stamp that ends up in the record, because the
/// elapsed time is measured against a MONOTONIC reading that never appears in
/// the summary and has no business in a crate that cannot read a clock.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct OpenSession {
    pub exe: String,
    pub game: String,
    pub tweaks: Vec<String>,
    pub started_wall: String,
}

impl OpenSession {
    /// `SessionTracker.start`'s one rule: a game with no display name is
    /// recorded under its executable. Python spells that `game or exe`, so it
    /// is an EMPTY name that falls back, not a missing one.
    pub fn new(exe: &str, game: &str, tweaks: &[String], started_wall: &str) -> Self {
        Self {
            exe: exe.to_owned(),
            game: if game.is_empty() { exe } else { game }.to_owned(),
            tweaks: tweaks.to_vec(),
            started_wall: started_wall.to_owned(),
        }
    }
}

/// One finished session, as it is written to `sessions.jsonl`.
///
/// Field order is the Python dataclass's, because `asdict` preserves it and
/// the file is read back by builds on both sides of the cutover.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SessionSummary {
    pub exe: String,
    pub game: String,
    pub started: String,
    pub ended: String,
    pub duration_s: f64,
    pub fps_avg: Option<f64>,
    pub fps_median: Option<f64>,
    pub fps_1low: Option<f64>,
    pub fps_min: Option<f64>,
    pub samples: usize,
    pub cpu_temp_avg: Option<f64>,
    pub gpu_temp_avg: Option<f64>,
    pub kernel: String,
    pub tweaks: Vec<String>,

    // populated only for benchmark runs
    pub benchmark: bool,
    /// 0.1% low.
    pub fps_01low: Option<f64>,
    pub fps_p95: Option<f64>,
    pub frametime_ms_avg: Option<f64>,
    /// Percentage of frames longer than twice the median frame time.
    pub frametime_stutter_pct: Option<f64>,
    pub cpu_temp_max: Option<f64>,
    pub gpu_temp_max: Option<f64>,
}

/// The shortest session worth recording, in seconds.
///
/// A benchmark is held to half of it: a benchmark run is deliberate and
/// usually short, where an ordinary session under a minute is almost always a
/// game that failed to start.
pub const MIN_DURATION_S: f64 = 60.0;
/// The same, for a run that was armed as a benchmark.
pub const MIN_BENCHMARK_DURATION_S: f64 = 30.0;

/// Summarise a finished session, or decide it was not worth recording.
///
/// `elapsed_s` is the monotonic time since the session started and
/// `ended_wall` the wall-clock stamp for the record; both are the caller's to
/// read. `series` is every sample from every MangoHud log in the window,
/// concatenated in the order the logs were read - which log a sample came from
/// makes no difference to any figure here, but the ORDER the samples arrive in
/// does, and that is the subtlest thing in this function. See `mean_in_order`.
///
/// Returns `None` when the session was too short, which is the only way it
/// declines: a session with no frames at all is still recorded, because how
/// long you played and what was applied while you did are worth keeping even
/// when the overlay logged nothing.
pub fn summarise(
    open: &OpenSession,
    series: &Series,
    elapsed_s: f64,
    kernel: &str,
    ended_wall: &str,
    benchmark: bool,
) -> Option<SessionSummary> {
    // Python clamps a backwards clock to zero here, and nothing can tell:
    // any negative elapsed time is below the floor either way, so the clamp
    // only ever feeds a value that is about to be rejected. Kept because it
    // is what the other side does.
    let duration = elapsed_s.max(0.0);
    let floor = if benchmark {
        MIN_BENCHMARK_DURATION_S
    } else {
        MIN_DURATION_S
    };
    // The UNROUNDED duration is what is compared, so 59.99 s is too short even
    // though the record would have said 60.0.
    if duration < floor {
        return None;
    }

    let mut out = SessionSummary {
        exe: open.exe.clone(),
        game: open.game.clone(),
        started: open.started_wall.clone(),
        ended: ended_wall.to_owned(),
        duration_s: round1(duration),
        kernel: kernel.to_owned(),
        tweaks: open.tweaks.clone(),
        benchmark,
        ..SessionSummary::default()
    };

    if series.fps.len() >= MIN_SAMPLES {
        let sorted = sorted(&series.fps);
        out.samples = sorted.len();
        // Summed over the SORTED copy, where the temperatures below sum the
        // order they were logged in - which is what Python does, and which
        // NOTHING can observe. `py_sum` compensates, and over 257,000
        // constructed sets whose mean sits on a rounding boundary no order of
        // the same samples differed from another by a single bit. Faithful
        // rather than load-bearing; do not "simplify" it into one order and
        // expect a test to complain.
        out.fps_avg = Some(round1(mean(&sorted)));
        out.fps_median = Some(round1(percentile(&sorted, 0.5)));
        out.fps_1low = Some(round1(percentile(&sorted, 0.01)));
        out.fps_min = Some(round1(sorted[0]));
        if benchmark {
            out.fps_01low = Some(round1(percentile(&sorted, 0.001)));
            out.fps_p95 = Some(round1(percentile(&sorted, 0.95)));
        }
    }

    // No sample minimum on the temperatures, and none on purpose: one reading
    // is a fair answer to "how hot did it get", where one frame is not a fair
    // answer to "how did it run".
    for (samples, avg, max) in [
        (
            &series.cpu_temp,
            &mut out.cpu_temp_avg,
            &mut out.cpu_temp_max,
        ),
        (
            &series.gpu_temp,
            &mut out.gpu_temp_avg,
            &mut out.gpu_temp_max,
        ),
    ] {
        if samples.is_empty() {
            continue;
        }
        *avg = Some(round1(mean(samples)));
        if benchmark {
            *max = Some(round1(largest(samples)));
        }
    }

    if benchmark && series.frametime_ms.len() >= MIN_SAMPLES {
        // The frame times carry their own minimum, counted over THEMSELVES: a
        // log can hold 40 frame rates and 29 frame times, because a row whose
        // frametime cell is out of range still contributes its frame rate.
        let sorted = sorted(&series.frametime_ms);
        let median = percentile(&sorted, 0.5);
        // Python writes `or 1.0` here, guarding a median of zero it cannot
        // reach: a frame time is only kept when it is strictly positive, so a
        // percentile of a non-empty series is positive too. Kept as it stands
        // rather than reproduced, because a divisor of 1.0 ms would be a
        // silent and arbitrary answer if it ever DID become reachable.
        // Counted over the logged order, though a count over the sorted copy
        // would be the same number - it is the same multiset. Written this way
        // because it is the samples being counted, not the ranking.
        let stutters = series
            .frametime_ms
            .iter()
            .filter(|x| **x > 2.0 * median)
            .count();
        out.frametime_ms_avg = Some(round2(mean(&series.frametime_ms)));
        out.frametime_stutter_pct = Some(round2(
            100.0 * stutters as f64 / series.frametime_ms.len() as f64,
        ));
    }

    Some(out)
}

/// `sorted(values)` - ascending, and a copy, because the caller's order is
/// itself significant to the arithmetic above.
fn sorted(values: &[f64]) -> Vec<f64> {
    let mut out = values.to_vec();
    out.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// `sum(values) / len(values)`.
///
/// Through `round::py_sum`, and not `iter().sum()`, because CPython's `sum`
/// compensates and the fold does not - see the note there. The two differ by
/// an ulp on samples like these, and `round(x, 1)` does not always hide it.
fn mean(values: &[f64]) -> f64 {
    py_sum(values) / values.len() as f64
}

/// `max(values)`.
///
/// Written to keep the first of equal values, as Python's `max` does, though
/// for floats that is a distinction without a difference: two values that
/// compare equal are the same number, so `>` and `>=` return the same answer
/// and no test can tell them apart.
fn largest(values: &[f64]) -> f64 {
    let mut best = values[0];
    for v in &values[1..] {
        if *v > best {
            best = *v;
        }
    }
    best
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

    // ---- summarise -------------------------------------------------------
    //
    // The parity corpus in tests/test_sessions_parity.py is what proves these
    // agree with Python, and it is far larger than this. These hold the shape
    // for a build that has no Python to diff against.

    fn open_session() -> OpenSession {
        OpenSession::new("game.exe", "A Game", &["governor".to_owned()], "STARTED")
    }

    fn frames(n: usize) -> Series {
        Series {
            fps: (0..n).map(|i| 60.0 + (i % 7) as f64).collect(),
            cpu_temp: vec![70.0; n],
            gpu_temp: vec![65.0; n],
            frametime_ms: vec![8.0; n],
        }
    }

    #[test]
    fn a_session_under_a_minute_is_not_recorded() {
        let s = &frames(60);
        assert!(summarise(&open_session(), s, 59.9, "k", "ENDED", false).is_none());
        assert!(summarise(&open_session(), s, 60.0, "k", "ENDED", false).is_some());
    }

    #[test]
    fn a_benchmark_is_recorded_from_thirty_seconds() {
        let s = &frames(60);
        assert!(summarise(&open_session(), s, 29.9, "k", "ENDED", true).is_none());
        assert!(summarise(&open_session(), s, 30.0, "k", "ENDED", true).is_some());
    }

    #[test]
    fn too_few_frames_leave_the_rate_fields_empty_but_still_record() {
        let out = summarise(&open_session(), &frames(29), 600.0, "k", "ENDED", false).unwrap();
        assert_eq!(out.samples, 0);
        assert_eq!(out.fps_avg, None);
        assert_eq!(out.duration_s, 600.0);
        assert_eq!(out.game, "A Game");
    }

    #[test]
    fn an_ordinary_session_carries_no_benchmark_figures() {
        let out = summarise(&open_session(), &frames(40), 600.0, "k", "ENDED", false).unwrap();
        assert_eq!(out.samples, 40);
        assert_eq!(out.fps_01low, None);
        assert_eq!(out.fps_p95, None);
        assert_eq!(out.cpu_temp_max, None);
        assert_eq!(out.frametime_stutter_pct, None);
        assert_eq!(out.cpu_temp_avg, Some(70.0));
    }

    #[test]
    fn a_benchmark_carries_them_all() {
        let out = summarise(&open_session(), &frames(40), 600.0, "k", "ENDED", true).unwrap();
        assert!(out.benchmark);
        assert!(out.fps_01low.is_some() && out.fps_p95.is_some());
        assert_eq!(out.cpu_temp_max, Some(70.0));
        assert_eq!(out.frametime_stutter_pct, Some(0.0));
    }

    #[test]
    fn a_frame_of_exactly_twice_the_median_is_not_a_stutter() {
        let mut s = frames(32);
        for (i, ft) in s.frametime_ms.iter_mut().enumerate() {
            *ft = if i < 24 { 8.0 } else { 16.0 };
        }
        let out = summarise(&open_session(), &s, 600.0, "k", "ENDED", true).unwrap();
        assert_eq!(out.frametime_stutter_pct, Some(0.0));
        s.frametime_ms[31] = 16.001;
        let out = summarise(&open_session(), &s, 600.0, "k", "ENDED", true).unwrap();
        // 1 frame in 32 is 3.125%, which is exactly representable and so an
        // exact tie at two places: it goes to even, not up.
        assert_eq!(out.frametime_stutter_pct, Some(3.12));
    }

    #[test]
    fn a_nameless_game_is_recorded_under_its_executable() {
        let open = OpenSession::new("game.exe", "", &[], "STARTED");
        assert_eq!(open.game, "game.exe");
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
