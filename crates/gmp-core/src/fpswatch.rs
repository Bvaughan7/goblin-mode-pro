//! Watching a MangoHud log for frame-rate dips.
//!
//! A port of the pure slice of `src/goblinmode/fpswatch.py`. Finding the log,
//! following it and handling rotation stay in Python; everything from a CSV
//! line onward moves, which is nearly the whole module.
//!
//! Two things here are subtler than they look.
//!
//! The `elapsed` column has no unit in MangoHud's output, and the candidates
//! are a factor of 1000 apart - which are also plausible row cadences. Getting
//! it wrong scales the virtual clock by 1000x in either direction, and every
//! window measured off that clock goes with it, including the dip duration
//! that decides whether anything is reported at all. See [`infer_divisor`].
//!
//! And the baseline is frozen for the length of a dip, so a long stall cannot
//! drag a rolling median down and then "recover" against its own degraded
//! numbers.

use serde::{Deserialize, Serialize};

use crate::round::one_dp;

pub const REMIND_SECONDS: f64 = 120.0;
const RECENT_S: f64 = 3.0;
const BASELINE_S: f64 = 30.0;
/// A dip has to last this long before it is a dip and not a load screen.
const MIN_DIP_DURATION_S: f64 = 4.0;
/// Recovery is measured against the frozen baseline, not the floor.
const RECOVERY_FRAC: f64 = 0.85;
/// Leaving a dip needs more than entering it, or a rate sitting on the floor
/// flaps between the two states once a second.
const EXIT_HYSTERESIS: f64 = 1.15;
/// Below this, nothing is being drawn - a menu, an alt-tab, a load screen.
/// Not a dip, and the baseline must not relearn from it.
const NOT_RENDERING_FPS: f64 = 5.0;
/// A dip this long never bounced back. Treat it as the new normal and relearn
/// rather than holding a stale baseline for the rest of the session.
const MAX_DIP_S: f64 = 120.0;

/// Seconds per unit, best-first: s, ms, us, ns.
const UNIT_DIVISORS: [f64; 4] = [1.0, 1e3, 1e6, 1e9];
/// A row cadence cannot be much shorter than one frame.
const SUB_FRAME_SLACK: f64 = 0.5;
const UNIT_SAMPLE_N: usize = 8;
const UNIT_MIN_DELTAS: usize = 3;
const UNIT_MAX_PENDING: usize = 64;
/// 6000 rows at the 200 ms cadence is ~20 minutes of virtual time, comfortably
/// longer than the longest window read off it.
const HIST_MAX: usize = 6000;

const UNIT_BY_SUFFIX: &[(&str, f64)] = &[
    ("ns", 1e9),
    ("us", 1e6),
    ("\u{b5}s", 1e6),
    ("ms", 1e3),
    ("sec", 1.0),
    ("s", 1.0),
];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FpsEvent {
    pub kind: String,
    pub fps: f64,
    pub baseline: f64,
    /// Seconds. 0.0 on a "dip", which is what the Python dataclass defaults
    /// it to - the field is always present rather than optional.
    #[serde(default)]
    pub duration_s: f64,
}

/// The unit off an `elapsed` column label, if it carries one.
///
/// MangoHud writes a bare `elapsed` today, so this normally returns `None` and
/// the unit is inferred from the data instead. It exists for the day that
/// changes, and because a label is worth more than a heuristic when present.
pub fn divisor_from_label(label: &str) -> Option<f64> {
    let mut tail: String = label.trim().to_lowercase();
    tail = tail
        .trim_matches(|c| c == ')' || c == ']')
        .replace(['(', '['], "_");
    for sep in ['-', ' ', '/'] {
        tail = tail.replace(sep, "_");
    }
    let token = tail.rsplit('_').next().unwrap_or(&tail);
    UNIT_BY_SUFFIX
        .iter()
        .find(|(suffix, _)| *suffix == token)
        .map(|(_, div)| *div)
}

/// `sorted(values)[len // 2]` - the UPPER median for an even count.
///
/// Deliberately not the statistical median: averaging the middle pair would
/// invent a frame rate that was never observed, and the caller uses this to
/// pick a real cadence.
fn median(values: &[f64]) -> f64 {
    let mut ordered: Vec<f64> = values.to_vec();
    ordered.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    ordered[ordered.len() / 2]
}

/// Pick the unit for this log's `elapsed` column, once, from its data.
///
/// The unit is decided once per log off the median delta, never per row: a
/// per-row magnitude test misreads any delta that is not the steady cadence.
/// A 30 s stall in a ms log looks like a us cadence and under-advances the
/// clock 1000x; a 1 ms frame in a ns log looks like a us stall and
/// over-advances it by the same factor.
///
/// The median alone is still ambiguous, because the units are a factor of 1000
/// apart and so are plausible cadences - 1e6 is a 1 ms cadence in ns and a 1 s
/// cadence in us, and nothing about the number says which. The frame rate
/// breaks the tie: MangoHud emits a row every `log_interval` or every frame,
/// so the cadence can never be much shorter than one frame. A unit implying
/// sub-frame spacing is impossible, and of what remains the truth is the
/// reading closest to the frame time.
pub fn infer_divisor(samples: &[(f64, f64)]) -> f64 {
    let usable: Vec<(f64, f64)> = samples
        .iter()
        .copied()
        .filter(|(d, f)| *d > 0.0 && *f > 0.0)
        .collect();
    if usable.is_empty() {
        // MangoHud's own unit, as a last resort.
        return 1e9;
    }
    let deltas: Vec<f64> = usable.iter().map(|(d, _)| *d).collect();
    let rates: Vec<f64> = usable.iter().map(|(_, f)| *f).collect();
    let med = median(&deltas);
    let frame_s = 1.0 / median(&rates);
    let floor = frame_s * SUB_FRAME_SLACK;

    let plausible: Vec<f64> = UNIT_DIVISORS
        .iter()
        .copied()
        .filter(|d| med / d >= floor)
        .collect();
    let candidates = if plausible.is_empty() {
        UNIT_DIVISORS.to_vec()
    } else {
        plausible
    };
    // `min` keeps the FIRST of equal keys, and UNIT_DIVISORS is ordered
    // coarsest-first, so a tie resolves to the larger unit exactly as it does
    // in Python.
    let mut best = candidates[0];
    let mut best_key = ((med / best) / frame_s).ln().abs();
    for &d in &candidates[1..] {
        let key = ((med / d) / frame_s).ln().abs();
        if key < best_key {
            best = d;
            best_key = key;
        }
    }
    best
}

/// `float(cell)` with Python's rules rather than Rust's.
///
/// Cells are not stripped before conversion in the Python, and `float()`
/// tolerates surrounding whitespace and single underscores between digits
/// where Rust's parser tolerates neither. A MangoHud log written with spaces
/// after its commas would otherwise parse on one implementation and not the
/// other.
pub fn py_float(cell: &str) -> Option<f64> {
    let text = cell.trim();
    if text.is_empty() {
        return None;
    }
    if text.contains('_') {
        // Underscores are legal only between digits.
        let bytes = text.as_bytes();
        for (i, b) in bytes.iter().enumerate() {
            if *b != b'_' {
                continue;
            }
            let before = i.checked_sub(1).map(|j| bytes[j]);
            let after = bytes.get(i + 1).copied();
            if !before.is_some_and(|c| c.is_ascii_digit())
                || !after.is_some_and(|c| c.is_ascii_digit())
            {
                return None;
            }
        }
        return text.replace('_', "").parse().ok();
    }
    text.parse().ok()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum State {
    Healthy,
    Dipping,
}

/// The watcher, minus the file it reads.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Watcher {
    pub dip_floor: f64,
    pub dip_ratio: f64,
    fps_col: Option<usize>,
    elapsed_col: Option<usize>,
    last_elapsed: Option<f64>,
    unit_div: Option<f64>,
    unit_deltas: Vec<(f64, f64)>,
    pending: Vec<(f64, f64)>,
    vclock: f64,
    hist: std::collections::VecDeque<(f64, f64)>,
    state: State,
    dip_started: f64,
    frozen_baseline: f64,
    dip_announced: bool,
    last_emit: f64,
}

impl Default for Watcher {
    fn default() -> Self {
        Self::new(22.0, 0.5)
    }
}

impl Watcher {
    pub fn new(dip_floor: f64, dip_ratio: f64) -> Self {
        Self {
            dip_floor,
            dip_ratio,
            fps_col: None,
            elapsed_col: None,
            last_elapsed: None,
            unit_div: None,
            unit_deltas: Vec::new(),
            pending: Vec::new(),
            vclock: 0.0,
            hist: std::collections::VecDeque::new(),
            state: State::Healthy,
            dip_started: 0.0,
            frozen_baseline: 0.0,
            dip_announced: false,
            last_emit: -1e9,
        }
    }

    fn push(&mut self, t: f64, fps: f64) {
        if self.hist.len() == HIST_MAX {
            self.hist.pop_front();
        }
        self.hist.push_back((t, fps));
    }

    /// One CSV line: the header if we have not seen one, otherwise a row.
    pub fn ingest(&mut self, line: &str) {
        let cells: Vec<&str> = line.split(',').collect();
        if self.fps_col.is_none() {
            let low: Vec<String> = cells.iter().map(|c| c.trim().to_lowercase()).collect();
            let Some(index) = low.iter().position(|c| c == "fps") else {
                return;
            };
            self.fps_col = Some(index);
            self.elapsed_col = low.iter().position(|c| {
                c.split('_')
                    .next()
                    .unwrap_or(c)
                    .split('(')
                    .next()
                    .unwrap_or(c)
                    .trim()
                    == "elapsed"
            });
            if let Some(col) = self.elapsed_col {
                self.unit_div = divisor_from_label(&low[col]);
            }
            return;
        }
        let fps_col = self.fps_col.unwrap_or(0);
        if cells.len() <= fps_col {
            return;
        }
        let Some(fps) = py_float(cells[fps_col]) else {
            return;
        };
        // NaN fails both comparisons, exactly as it does in Python.
        if !(fps > 0.0 && fps < 1000.0) {
            return;
        }

        let elapsed = self
            .elapsed_col
            .filter(|col| cells.len() > *col)
            .and_then(|col| py_float(cells[col]));
        let Some(et) = elapsed else {
            // No elapsed column, or a cell that would not convert. Either way
            // the clock advances at the nominal cadence rather than stalling.
            self.vclock += 0.2;
            let t = self.vclock;
            self.push(t, fps);
            return;
        };

        let mut delta = 0.0;
        if self.last_elapsed.is_some_and(|last| et > last) {
            delta = et - self.last_elapsed.unwrap_or(0.0);
        }
        self.last_elapsed = Some(et);

        let Some(div) = self.unit_div else {
            // Hold the row back until the unit is known - the divisor applies
            // to every delta including this log's first ones.
            self.pending.push((delta, fps));
            if delta > 0.0 {
                self.unit_deltas.push((delta, fps));
            }
            if self.unit_deltas.len() >= UNIT_SAMPLE_N || self.pending.len() >= UNIT_MAX_PENDING {
                self.settle_unit();
            }
            return;
        };
        self.vclock += delta / div;
        let t = self.vclock;
        self.push(t, fps);
    }

    /// Commit to a unit and replay the rows held back while deciding.
    pub fn settle_unit(&mut self) {
        if self.unit_div.is_none() {
            self.unit_div = Some(infer_divisor(&self.unit_deltas));
        }
        let div = self.unit_div.unwrap_or(1e9);
        for (delta, fps) in std::mem::take(&mut self.pending) {
            self.vclock += delta / div;
            let t = self.vclock;
            self.push(t, fps);
        }
        self.unit_deltas.clear();
    }

    /// The unit this log settled on, once it has.
    pub fn unit_div(&self) -> Option<f64> {
        self.unit_div
    }

    /// What `poll` does after reading a chunk: settle if enough deltas are in,
    /// then judge.
    pub fn poll_tail(&mut self) -> Option<FpsEvent> {
        if self.unit_div.is_none() && self.unit_deltas.len() >= UNIT_MIN_DELTAS {
            self.settle_unit();
        }
        self.evaluate()
    }

    fn window(&self, seconds: f64) -> Vec<f64> {
        let cut = self.vclock - seconds;
        self.hist
            .iter()
            .filter(|(t, _)| *t >= cut)
            .map(|(_, f)| *f)
            .collect()
    }

    pub fn current_fps(&self) -> Option<f64> {
        let w = self.window(RECENT_S);
        (!w.is_empty()).then(|| one_dp(w.iter().sum::<f64>() / w.len() as f64))
    }

    /// `{}` when there is nothing to report, which the caller distinguishes
    /// from a reading of zero.
    pub fn stats(&self) -> Option<serde_json::Value> {
        let w = self.window(60.0);
        if w.is_empty() {
            return None;
        }
        let mut sorted = w.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let min = sorted.first().copied().unwrap_or(0.0);
        Some(serde_json::json!({
            "fps_avg": one_dp(w.iter().sum::<f64>() / w.len() as f64),
            "fps_min": one_dp(min),
            "fps_1low": one_dp(sorted[sorted.len() / 100]),
            "in_dip": self.state == State::Dipping,
        }))
    }

    /// Low enough to be a dip: under the absolute floor, or well under a
    /// baseline that is itself worth comparing against.
    fn is_low(&self, fps: f64, baseline: f64) -> bool {
        if fps <= self.dip_floor {
            return true;
        }
        baseline > 5.0 && fps <= baseline * self.dip_ratio
    }

    /// Virtual seconds of unbroken low samples at the tail of history.
    fn low_run_seconds(&self, baseline: f64) -> f64 {
        let mut run = 0.0;
        let mut last_t: Option<f64> = None;
        for (t, f) in self.hist.iter().rev() {
            if !self.is_low(*f, baseline) {
                break;
            }
            if let Some(last) = last_t {
                run += last - t;
            }
            last_t = Some(*t);
        }
        run
    }

    pub fn evaluate(&mut self) -> Option<FpsEvent> {
        let recent = self.window(RECENT_S);
        let base = self.window(BASELINE_S);
        if recent.len() < 3 || base.len() < 12 {
            return None;
        }
        let fps = recent.iter().sum::<f64>() / recent.len() as f64;
        let now = self.vclock;

        let prev_baseline = self.frozen_baseline;
        // Nothing is being drawn - a menu, an alt-tab, a load screen. Not a
        // dip, and the baseline must not relearn from it.
        let idle_window = fps <= NOT_RENDERING_FPS && prev_baseline > 30.0;

        // The baseline is relearned only while healthy and actually rendering.
        // It is frozen for the length of an episode so the episode cannot drag
        // a rolling median down and fake its own recovery.
        if self.state == State::Healthy && !idle_window {
            self.frozen_baseline = median(&base);
        }
        let baseline = self.frozen_baseline;
        let low = self.is_low(fps, baseline) && !idle_window;

        if self.state != State::Dipping {
            let run = if low {
                self.low_run_seconds(baseline)
            } else {
                0.0
            };
            if run < MIN_DIP_DURATION_S {
                self.state = State::Healthy;
                return None;
            }
            self.state = State::Dipping;
            self.dip_started = now - run;
            if now - self.last_emit >= REMIND_SECONDS {
                self.last_emit = now;
                self.dip_announced = true;
                return Some(FpsEvent {
                    kind: "dip".into(),
                    fps: one_dp(fps),
                    baseline: one_dp(baseline),
                    duration_s: 0.0,
                });
            }
            self.dip_announced = false;
            return None;
        }

        let recovered = fps >= (self.dip_floor * EXIT_HYSTERESIS).max(baseline * RECOVERY_FRAC);
        if recovered {
            let duration = now - self.dip_started;
            self.state = State::Healthy;
            if self.dip_announced {
                self.dip_announced = false;
                return Some(FpsEvent {
                    kind: "recovered".into(),
                    fps: one_dp(fps),
                    baseline: one_dp(baseline),
                    duration_s: one_dp(duration),
                });
            }
            return None;
        }
        if now - self.dip_started >= MAX_DIP_S {
            // Never bounced back. Treat it as the new normal and relearn.
            self.state = State::Healthy;
            self.dip_announced = false;
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_labelled_unit_beats_the_heuristic() {
        assert_eq!(divisor_from_label("elapsed_ns"), Some(1e9));
        assert_eq!(divisor_from_label("elapsed (ms)"), Some(1e3));
        assert_eq!(divisor_from_label("Elapsed [us]"), Some(1e6));
        assert_eq!(divisor_from_label("elapsed-sec"), Some(1.0));
        assert_eq!(divisor_from_label("elapsed/s"), Some(1.0));
        assert_eq!(divisor_from_label("elapsed_\u{b5}s"), Some(1e6));
        // MangoHud's actual header today.
        assert_eq!(divisor_from_label("elapsed"), None);
        assert_eq!(divisor_from_label("fps"), None);
    }

    #[test]
    fn the_median_is_the_upper_one() {
        // Averaging the middle pair would invent a cadence never observed.
        assert_eq!(median(&[1.0, 2.0, 3.0, 4.0]), 3.0);
        assert_eq!(median(&[1.0, 2.0, 3.0]), 2.0);
        assert_eq!(median(&[5.0]), 5.0);
    }

    #[test]
    fn the_unit_comes_from_the_frame_rate_not_the_magnitude() {
        // 1e6 is a 1 ms cadence in ns and a 1 s cadence in us. At 60 fps the
        // 1 s reading implies 60 frames between rows; the ns reading is the
        // one that matches.
        let samples: Vec<(f64, f64)> = (0..8).map(|_| (16_666_666.0, 60.0)).collect();
        assert_eq!(infer_divisor(&samples), 1e9);
    }

    #[test]
    fn a_sub_frame_cadence_is_impossible_and_is_ruled_out() {
        // A row cannot arrive much faster than a frame, so the units that
        // would imply it are not candidates at all.
        let samples: Vec<(f64, f64)> = (0..8).map(|_| (200.0, 60.0)).collect();
        assert_eq!(infer_divisor(&samples), 1e3, "200 ms at 60 fps");
    }

    #[test]
    fn no_usable_samples_falls_back_to_mangohuds_own_unit() {
        assert_eq!(infer_divisor(&[]), 1e9);
        assert_eq!(infer_divisor(&[(0.0, 60.0), (-1.0, 60.0)]), 1e9);
        assert_eq!(infer_divisor(&[(100.0, 0.0)]), 1e9, "zero fps is unusable");
    }

    #[test]
    fn cells_parse_the_way_python_parses_them() {
        assert_eq!(py_float(" 60.0 "), Some(60.0), "spaces after commas");
        assert_eq!(py_float("1_000.5"), Some(1000.5));
        assert_eq!(py_float("+60"), Some(60.0));
        assert_eq!(py_float("6e1"), Some(60.0));
        assert_eq!(py_float(".5"), Some(0.5));
        assert_eq!(py_float("_60"), None);
        assert_eq!(py_float("60_"), None);
        assert_eq!(py_float("6__0"), None);
        assert_eq!(py_float(""), None);
        assert_eq!(py_float("n/a"), None);
        assert!(py_float("inf").is_some_and(f64::is_infinite));
        assert!(py_float("nan").is_some_and(f64::is_nan));
    }

    /// A log at a steady rate, with an explicit unit so no inference happens.
    fn steady_log(fps: f64, rows: usize) -> Vec<String> {
        let mut lines = vec!["fps,elapsed_ms".to_string()];
        for i in 0..rows {
            lines.push(format!("{fps},{}", i * 200));
        }
        lines
    }

    fn feed(watcher: &mut Watcher, lines: &[String]) -> Vec<FpsEvent> {
        let mut events = Vec::new();
        for line in lines {
            watcher.ingest(line);
            if let Some(event) = watcher.poll_tail() {
                events.push(event);
            }
        }
        events
    }

    #[test]
    fn a_steady_rate_reports_nothing() {
        let mut w = Watcher::default();
        assert!(feed(&mut w, &steady_log(60.0, 100)).is_empty());
    }

    #[test]
    fn nan_and_infinity_never_enter_the_history() {
        let mut w = Watcher::default();
        let mut lines = vec!["fps,elapsed_ms".to_string()];
        for i in 0..20 {
            lines.push(format!("nan,{}", i * 200));
            lines.push(format!("inf,{}", i * 200 + 100));
        }
        feed(&mut w, &lines);
        assert_eq!(w.current_fps(), None, "nothing should have been recorded");
    }

    #[test]
    fn a_rate_out_of_range_is_dropped() {
        // 0 and 1000 are both excluded - a log line reading either is a
        // MangoHud artefact, not a frame rate.
        let mut w = Watcher::default();
        let lines: Vec<String> = ["fps,elapsed_ms", "0,0", "1000,200", "-5,400"]
            .iter()
            .map(|s| (*s).to_string())
            .collect();
        feed(&mut w, &lines);
        assert_eq!(w.current_fps(), None);
    }

    #[test]
    fn a_sustained_drop_is_reported_and_its_recovery_too() {
        let mut w = Watcher::default();
        let mut lines = steady_log(60.0, 200);
        let base = 200 * 200;
        // 15 fps for 10 virtual seconds - under the 22 floor, past the 4s
        // minimum.
        for i in 0..50 {
            lines.push(format!("15,{}", base + i * 200));
        }
        let events = feed(&mut w, &lines);
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0].kind, "dip");
        assert_eq!(events[0].baseline, 60.0);

        let after = base + 50 * 200;
        let recovery: Vec<String> = (0..50).map(|i| format!("60,{}", after + i * 200)).collect();
        let events = feed(&mut w, &recovery);
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0].kind, "recovered");
        assert!(events[0].duration_s > 0.0);
    }

    #[test]
    fn a_brief_stutter_is_not_a_dip() {
        // Shorter than MIN_DIP_DURATION_S. This is the load-screen case, and
        // reporting it would make the feature useless.
        let mut w = Watcher::default();
        let mut lines = steady_log(60.0, 200);
        let base = 200 * 200;
        for i in 0..10 {
            lines.push(format!("15,{}", base + i * 200));
        }
        assert!(feed(&mut w, &lines).is_empty());
    }

    #[test]
    fn an_alt_tab_is_not_a_dip() {
        // Below NOT_RENDERING_FPS with a real baseline behind it: nothing is
        // being drawn, so there is nothing to complain about.
        let mut w = Watcher::default();
        let mut lines = steady_log(60.0, 200);
        let base = 200 * 200;
        for i in 0..100 {
            lines.push(format!("2,{}", base + i * 200));
        }
        assert!(feed(&mut w, &lines).is_empty());
    }

    #[test]
    fn the_baseline_does_not_relearn_during_a_dip() {
        // The whole reason it is frozen: otherwise a long dip drags the
        // rolling median down to meet it and "recovers" against a degraded
        // number.
        let mut w = Watcher::default();
        let mut lines = steady_log(60.0, 200);
        let base = 200 * 200;
        for i in 0..100 {
            lines.push(format!("15,{}", base + i * 200));
        }
        feed(&mut w, &lines);
        assert_eq!(w.frozen_baseline, 60.0);
    }

    #[test]
    fn history_is_capped() {
        let mut w = Watcher::default();
        feed(&mut w, &steady_log(60.0, HIST_MAX + 500));
        assert_eq!(w.hist.len(), HIST_MAX);
    }

    #[test]
    fn a_log_with_no_elapsed_column_uses_the_nominal_cadence() {
        let mut w = Watcher::new(22.0, 0.5);
        let mut lines = vec!["fps,frametime".to_string()];
        for _ in 0..20 {
            lines.push("60,16.6".to_string());
        }
        feed(&mut w, &lines);
        // Not assert_eq: 0.2 added twenty times is 4.000000000000001 in
        // both languages, and the parity corpus compares the drift itself.
        assert!((w.vclock - 4.0).abs() < 1e-9, "{}", w.vclock);
    }
}
