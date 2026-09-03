//! Reading a GPU snapshot and saying what a frame-rate dip probably was.
//!
//! A port of the pure half of `src/goblinmode/gpu.py`. Everything that talks
//! to nvidia-smi or sysfs - `available`, `deep_state`, `light_state`,
//! `nvidia_module_state`, `GpuMonitor` - stays in Python. What moves here is
//! the judgement: given a snapshot and some numbers, is this a hardware fault,
//! a scene that is simply heavy, or the game not drawing at all?
//!
//! The distinction matters more than it sounds. A dip that is not a fault must
//! not arm the post-game post-mortem, or the user is told something broke
//! every time they alt-tab.

use serde_json::{Map, Value};

use crate::round::one_dp;

/// A GPU snapshot: the heterogeneous dict nvidia-smi parsing produces.
pub type State = Map<String, Value>;

/// A numeric field, or None if absent, null or not a number.
fn num(state: &State, key: &str) -> Option<f64> {
    state.get(key).and_then(Value::as_f64)
}

/// A numeric field that is present AND non-zero.
///
/// Python spells this `if used and total`, where 0 is falsy. Translating that
/// as "is present" would make a zeroed field look like a real reading.
fn nonzero(state: &State, key: &str) -> Option<f64> {
    num(state, key).filter(|v| *v != 0.0)
}

/// Parse one nvidia-smi cell.
///
/// The sentinels are nvidia-smi's own way of saying a field does not apply to
/// this card. A value it does not recognise comes back as the raw string
/// rather than being dropped, so an unexpected format is visible in a bug
/// report instead of silently becoming null.
pub fn parse_cell(v: &str) -> Value {
    let v = v.trim();
    if matches!(v, "" | "[N/A]" | "N/A" | "[Not Supported]") {
        return Value::Null;
    }
    if let Ok(i) = v.parse::<i64>() {
        return Value::from(i);
    }
    if let Ok(f) = v.parse::<f64>() {
        return Value::from(f);
    }
    Value::from(v)
}

/// When FPS collapses but nothing is working hard, the frames are being
/// *withheld* rather than *starved*. Returns a plain-language note if so.
pub fn classify_dip(
    state: &State,
    cpu_load: Option<f64>,
    disk_read_mbps: Option<f64>,
) -> Option<String> {
    let util = num(state, "util_gpu")?;
    let gpu_idle = util < 15.0;
    let cpu_light = cpu_load.is_none_or(|c| c < 45.0);
    if !(gpu_idle && cpu_light) {
        return None;
    }
    if let Some(disk) = disk_read_mbps {
        if disk > 25.0 {
            return Some(format!(
                "CPU and GPU were near-idle while the disk read {disk:.0} MB/s \
                 - this is a loading screen / zone transition streaming assets, not a bottleneck"
            ));
        }
    }
    Some(
        "CPU and GPU were both near-idle - the frames were withheld, not starved. \
         Usually the game window losing focus (alt-tab -> WoW 'Max Background FPS' \
         cap), a loading screen, or a menu. Not a hardware problem"
            .to_owned(),
    )
}

/// Likely causes for a stall, most damning first.
///
/// `under_load` should reflect whether the GPU was actually busy. A
/// down-trained PCIe link or a low power state is EXPECTED on an idle GPU, so
/// reporting them then would be a false alarm every time.
pub fn assess(state: &State, under_load: bool) -> Vec<String> {
    if state.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();

    // Checked against the snapshot itself, so a stale or optimistic
    // `under_load` cannot produce "bandwidth-starved" lines while the GPU sleeps.
    let util = num(state, "util_gpu").unwrap_or(0.0);
    let busy = under_load && util >= 25.0;

    let used = nonzero(state, "vram_used_mb");
    let total = nonzero(state, "vram_total_mb");
    let free = num(state, "vram_free_mb");
    if let (Some(used), Some(total)) = (used, total) {
        let frac = used / total;
        let free_txt = free.map_or("null".to_owned(), fmt_num);
        if frac >= 0.94 || free.is_some_and(|f| f < 300.0) {
            out.push(format!(
                "VRAM near exhaustion ({}/{} MB, {free_txt} MB free) - the \
                 driver is likely spilling to system RAM over PCIe",
                fmt_num(used),
                fmt_num(total)
            ));
        } else if frac >= 0.88 {
            out.push(format!(
                "VRAM pressure high ({}/{} MB)",
                fmt_num(used),
                fmt_num(total)
            ));
        }
    }

    let rx = num(state, "pcie_rx_mbps");
    let pushed = rx.is_none_or(|r| r > 500.0);
    let rx_txt = rx.map_or("None".to_owned(), fmt_num);
    if let (Some(gen), Some(gen_max)) = (nonzero(state, "pcie_gen"), nonzero(state, "pcie_gen_max"))
    {
        if busy && gen < gen_max && pushed {
            out.push(format!(
                "PCIe link at Gen{} (card supports Gen{}) while carrying {rx_txt} MB/s - bandwidth-starved",
                fmt_num(gen),
                fmt_num(gen_max)
            ));
        }
    }
    if let (Some(w), Some(w_max)) = (
        nonzero(state, "pcie_width"),
        nonzero(state, "pcie_width_max"),
    ) {
        if busy && w < w_max && w <= 4.0 && pushed {
            out.push(format!(
                "PCIe link narrowed to x{} (of x{}) under load",
                fmt_num(w),
                fmt_num(w_max)
            ));
        }
    }

    if busy && util > 50.0 {
        if let Some(ps) = state.get("pstate").and_then(Value::as_str) {
            if matches!(ps, "P5" | "P8" | "P12" | "P15") {
                out.push(format!(
                    "GPU stuck in low power-state {ps} while {}% utilised",
                    fmt_num(util)
                ));
            }
        }
    }

    if let (Some(cg), Some(cgm)) = (
        nonzero(state, "clock_gfx_mhz"),
        nonzero(state, "clock_gfx_max_mhz"),
    ) {
        if busy && cg < cgm * 0.55 && util > 50.0 {
            out.push(format!(
                "GPU core clock collapsed to {}/{} MHz under {}% load",
                fmt_num(cg),
                fmt_num(cgm),
                fmt_num(util)
            ));
        }
    }

    // Only an integer counts: the field is a bitmask, and a float here would
    // mean nvidia-smi returned something unexpected.
    if let Some(er) = state.get("event_reasons").and_then(Value::as_i64) {
        if er != 0 {
            let mut hit = Vec::new();
            for (bit, label) in [
                (0x8, "HW slowdown"),
                (0x40, "HW thermal"),
                (0x80, "HW power-brake"),
            ] {
                if er & bit != 0 {
                    hit.push(label);
                }
            }
            if !hit.is_empty() {
                out.push(format!("nvidia clock-event: {}", hit.join(", ")));
            }
        }
    }
    out
}

/// Render a number the way Python's `str()` would inside an f-string: an
/// integral float prints without a trailing `.0`.
fn fmt_num(v: f64) -> String {
    if v.fract() == 0.0 && v.abs() < 1e15 {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}

/// Turn a fresh snapshot plus the dip numbers into an incident line.
///
/// Returns `(detail, is_real)`. `is_real` is false for a dip that is not a
/// hardware FAULT - frames withheld, or a scene simply GPU- or CPU-bound at
/// the current settings - so it does not arm the post-game post-mortem.
///
/// Mutates `state` with the at-dip context that the exporter and the
/// Diagnostics page read back out.
pub fn describe_dip(
    state: &mut State,
    fps: f64,
    baseline: f64,
    cpu_load: Option<f64>,
    disk_read: Option<f64>,
    cpu_core_max: Option<f64>,
) -> (String, bool) {
    let gpu_busy = num(state, "util_gpu").unwrap_or(0.0) >= 25.0 || cpu_load.unwrap_or(0.0) >= 60.0;
    let benign = classify_dip(state, cpu_load, disk_read);
    let causes = assess(state, gpu_busy);

    state.insert("likely_causes".into(), Value::from(causes.clone()));
    state.insert(
        "cpu_load_at_dip".into(),
        cpu_load.map_or(Value::Null, |v| Value::from(one_dp(v))),
    );
    state.insert(
        "cpu_core_max_at_dip".into(),
        cpu_core_max.map_or(Value::Null, |v| Value::from(one_dp(v))),
    );
    state.insert(
        "disk_read_mbps_at_dip".into(),
        disk_read.map_or(Value::Null, Value::from),
    );

    let util = num(state, "util_gpu");
    let at = format!("(baseline ~{baseline:.0})");

    if let Some(benign) = &benign {
        if causes.is_empty() {
            state.insert(
                "assessment".into(),
                Value::from("benign - not a hardware bottleneck"),
            );
            return (
                format!("Frame rate dipped to {fps:.0} FPS {at}. {benign}"),
                false,
            );
        }
    }
    if let Some(first) = causes.first() {
        return (
            format!("Frame rate collapsed to {fps:.0} FPS {at}. {first}"),
            true,
        );
    }

    let dropped = baseline > 0.0 && fps <= baseline * 0.75;
    if dropped {
        if let Some(util) = util {
            if util >= 92.0 {
                state.insert("assessment".into(), Value::from("GPU-bound scene"));
                return (
                    format!(
                        "Frame rate dropped to {fps:.0} FPS {at}. GPU pegged at {util:.0}% - \
                         this spot is heavier than your settings can sustain, not a fault. \
                         Lower a setting or cap the frame rate here."
                    ),
                    false,
                );
            }
            if util < 80.0 {
                if let Some(core) = cpu_core_max {
                    if core >= 95.0 {
                        state.insert("assessment".into(), Value::from("CPU-bound scene"));
                        return (
                            format!(
                                "Frame rate dropped to {fps:.0} FPS {at}. A CPU core was pegged at \
                                 {core:.0}% while the GPU had headroom ({util:.0}%) - a \
                                 single-threaded hotspot (busy city, raid), not a hardware fault."
                            ),
                            false,
                        );
                    }
                }
            }
        }
    }
    (
        format!(
            "Frame rate dropped to {fps:.0} FPS {at}. No single cause stood out - \
             the GPU snapshot is attached. A short drop like this is usually a zone \
             load, shader compilation or a background task."
        ),
        true,
    )
}

/// After the game exits: did the GPU actually let go?
///
/// Only VRAM is checked. An idle PCIe down-train is normal ASPM, not a fault,
/// and flagging it would report a problem after every session.
pub fn post_mortem(idle_state: &State) -> Option<(String, String)> {
    let used = num(idle_state, "vram_used_mb")?;
    if used > 900.0 {
        return Some((
            "vram_not_freed".to_owned(),
            format!(
                "{} MB of VRAM still allocated after the game exited - a \
                 driver-side leak; a reboot clears it",
                fmt_num(used)
            ),
        ));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn state(pairs: Value) -> State {
        pairs.as_object().cloned().unwrap_or_default()
    }

    // ---- translated from tests/test_gpu.py -------------------------------

    #[test]
    fn vram_exhaustion_flagged() {
        let s = state(json!({"util_gpu": 90, "vram_used_mb": 5900,
                             "vram_total_mb": 6000, "vram_free_mb": 100}));
        assert!(assess(&s, true)
            .iter()
            .any(|c| c.contains("VRAM near exhaustion")));
    }

    #[test]
    fn idle_gpu_never_flags_pcie_or_pstate() {
        // A down-trained link and a low power state are both EXPECTED on an
        // idle GPU. util gates it as well as the under_load flag, so a stale
        // caller cannot produce a false alarm either.
        let s = state(json!({"util_gpu": 2, "pcie_gen": 1, "pcie_gen_max": 4, "pstate": "P8"}));
        assert!(assess(&s, false).is_empty());
        assert!(assess(&s, true).is_empty());
    }

    #[test]
    fn empty_state_returns_empty() {
        assert!(assess(&State::new(), true).is_empty());
    }

    #[test]
    fn idle_cpu_and_gpu_is_withheld_not_starved() {
        let note = classify_dip(&state(json!({"util_gpu": 3})), Some(10.0), Some(2.0)).unwrap();
        assert!(note.contains("withheld"));
    }

    #[test]
    fn busy_gpu_returns_none() {
        assert!(classify_dip(&state(json!({"util_gpu": 80})), Some(70.0), Some(0.0)).is_none());
    }

    #[test]
    fn post_mortem_flags_unreleased_vram() {
        let v = post_mortem(&state(json!({"vram_used_mb": 1500}))).unwrap();
        assert_eq!(v.0, "vram_not_freed");
    }

    #[test]
    fn post_mortem_clean_exit_returns_none() {
        assert!(post_mortem(&state(json!({"vram_used_mb": 120}))).is_none());
    }

    // ---- the port's own hazards -------------------------------------------

    #[test]
    fn a_zeroed_field_is_not_a_reading() {
        // Python spells the guard `if used and total`, where 0 is falsy.
        // Treating it as "present" would divide by zero or report a link at
        // Gen0 as degraded.
        let s = state(
            json!({"util_gpu": 90, "vram_used_mb": 0, "vram_total_mb": 0,
                             "pcie_gen": 0, "pcie_gen_max": 4}),
        );
        assert!(assess(&s, true).is_empty());
    }

    #[test]
    fn describe_dip_records_the_context_it_was_given() {
        // The exporter and the Diagnostics page read these back out, so the
        // mutation is part of the contract rather than a side effect.
        let mut s = state(json!({"util_gpu": 99}));
        let (_detail, is_real) =
            describe_dip(&mut s, 30.0, 60.0, Some(51.15), Some(4.0), Some(99.5));
        assert!(!is_real, "a GPU-bound scene is not a fault");
        assert_eq!(s["cpu_load_at_dip"], json!(51.1)); // round(x, 1), half to even
        assert_eq!(s["cpu_core_max_at_dip"], json!(99.5));
        assert_eq!(s["assessment"], "GPU-bound scene");
        assert!(s["likely_causes"].as_array().unwrap().is_empty());
    }

    #[test]
    fn a_benign_dip_does_not_arm_the_post_mortem() {
        let mut s = state(json!({"util_gpu": 3}));
        let (detail, is_real) = describe_dip(&mut s, 0.0, 0.0, Some(5.0), None, None);
        assert!(!is_real);
        assert!(detail.contains("withheld"));
    }

    #[test]
    fn parse_cell_keeps_an_unrecognised_value_rather_than_dropping_it() {
        assert_eq!(parse_cell("42"), json!(42));
        assert_eq!(parse_cell("3.5"), json!(3.5));
        assert_eq!(parse_cell("[N/A]"), Value::Null);
        assert_eq!(parse_cell("  "), Value::Null);
        // visible in a bug report instead of silently becoming null
        assert_eq!(parse_cell("something new"), json!("something new"));
    }
}
