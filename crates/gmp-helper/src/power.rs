//! Intel RAPL power limits and AMD ryzenadj TDP control.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

use crate::error::{HelperError, Result};
use crate::{state, sys};

fn rapl_constraint(rapl_base: &Path, idx: u8, leaf: &str) -> PathBuf {
    rapl_base.join(format!("constraint_{idx}_{leaf}"))
}

/// PL1 and PL2, in microwatts.
///
/// Fails rather than reporting zeros when the machine has no RAPL: PL1 = 0 is
/// a value a caller could act on, and "this machine cannot report power
/// limits" is not the same statement as "this machine's limit is nothing".
/// The Python helper propagates the `OSError` here for the same reason.
pub fn get_power_limits(rapl_base: &Path) -> std::io::Result<(u64, u64)> {
    let read = |idx: u8| -> std::io::Result<u64> {
        let path = rapl_constraint(rapl_base, idx, "power_limit_uw");
        sys::read_trimmed(&path)?.parse::<u64>().map_err(|err| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("{} is not a number: {err}", path.display()),
            )
        })
    };
    Ok((read(0)?, read(1)?))
}

/// `ryzenadj`, resolved once at startup.
///
/// Resolved once rather than per call because the Python helper binds
/// `RYZENADJ` at import: a machine that installs ryzenadj while the helper is
/// running keeps reporting no TDP control until the service restarts, on both
/// implementations. Matching that matters more than being cleverer, because
/// `HasTDPControl` is what the GUI greys a control on.
pub fn ryzenadj() -> Option<&'static Path> {
    static RYZENADJ: OnceLock<Option<PathBuf>> = OnceLock::new();
    RYZENADJ.get_or_init(|| sys::which("ryzenadj")).as_deref()
}

pub fn has_tdp_control() -> bool {
    ryzenadj().is_some()
}

/// Absolute upper bound for a RAPL write, used when the firmware maximum
/// cannot be read. No real CPU accepts anywhere near this.
const RAPL_CEILING_UW: u64 = 1_000_000_000;

/// Absolute floor for a RAPL PL1/PL2 write.
///
/// SetPowerLimits is a "raise the cap" feature. Driving PL1 down to a few
/// watts is a silent local denial of service - the machine crawls and nothing
/// errors - so anything below this is refused. 6 W sits under the lowest real
/// preset (an Intel handheld on battery is ~8 W) while still blocking the
/// "pin it to 4 W" case. A genuinely lower limit has to be set out of band.
const RAPL_FLOOR_UW: u64 = 6_000_000;

const TDP_MIN_W: u32 = 4;
const TDP_MAX_W: u32 = 120;

/// How long ryzenadj gets before it is abandoned.
const RYZENADJ_TIMEOUT: Duration = Duration::from_secs(8);

/// The limits ryzenadj reports, as (row label, write flag).
const RYZENADJ_LIMITS: &[(&str, &str)] = &[
    ("STAPM LIMIT", "stapm-limit"),
    ("PPT LIMIT FAST", "fast-limit"),
    ("PPT LIMIT SLOW", "slow-limit"),
];

/// Raise the RAPL power limits.
///
/// A value of 0 means "leave this constraint alone", NOT "set it to zero" -
/// which is why zero is not treated as a floor violation.
pub fn set_power_limits(roots: &sys::Roots, pl1_uw: u64, pl2_uw: u64) -> Result<bool> {
    let requested = [(0u8, pl1_uw), (1u8, pl2_uw)];

    // VALIDATE EVERYTHING BEFORE SNAPSHOTTING. This method is where that rule
    // came from: it used to snapshot first and validate inside the write loop,
    // so a below-floor request was correctly refused and still left a
    // root-owned state.json behind, which stopped the next real apply from
    // recording its baseline. See tests/conformance/helper.py.
    for (idx, value) in requested {
        if value > 0 && value < RAPL_FLOOR_UW {
            return Err(HelperError::Failed(format!(
                "RAPL constraint {idx} request {value} µW is below the \
                 {RAPL_FLOOR_UW} µW floor - this method only raises the limit"
            )));
        }
    }
    snapshot(roots)?;

    let mut ok = true;
    for (idx, value) in requested {
        if value == 0 {
            continue;
        }
        if let Err(err) = write_constraint(&roots.rapl, idx, value) {
            tracing::warn!("RAPL write failed for constraint {idx}: {err}");
            ok = false;
        }
    }
    Ok(ok)
}

/// Clamp to the firmware maximum and write.
///
/// An unreadable `max_power_uw` skips the write entirely rather than writing
/// the unclamped value: the cap is the only thing standing between a request
/// and a limit the silicon will not honour, and guessing is worse than not
/// applying. On this project's own test machine the firmware max is 45 W while
/// PL1 reads 107 W, so the clamp is not theoretical - using this feature there
/// LOWERS PL1, which selftest reports rather than treating as a failure.
fn write_constraint(rapl_base: &Path, idx: u8, value: u64) -> std::io::Result<()> {
    let mut value = value.min(RAPL_CEILING_UW);
    let cap: u64 = sys::read_trimmed(&rapl_constraint(rapl_base, idx, "max_power_uw"))?
        .parse()
        .unwrap_or(0);
    if cap > 0 {
        value = value.min(cap);
    }
    sys::write_value(
        &rapl_constraint(rapl_base, idx, "power_limit_uw"),
        &value.to_string(),
    )
}

/// Restore PL1/PL2 from the snapshot, leaving the snapshot in place.
///
/// The state file is deliberately NOT deleted: the governor and EPP it also
/// records may still be applied for another running game, and dropping the
/// file would lose the baseline for those.
pub fn reset_power_limits(roots: &sys::Roots) -> Result<bool> {
    let Some(snapshot) = state::Snapshot::load(&roots.state_file()) else {
        // Nothing was ever recorded, so there is nothing to put back. That is
        // success, not failure - the machine is already as it was.
        return Ok(true);
    };
    let (Some(pl1), Some(pl2)) = (snapshot.pl1_uw, snapshot.pl2_uw) else {
        return Ok(true);
    };
    let mut ok = true;
    for (idx, value) in [(0u8, pl1), (1u8, pl2)] {
        if let Err(err) = sys::write_value(
            &rapl_constraint(&roots.rapl, idx, "power_limit_uw"),
            &value.to_string(),
        ) {
            tracing::warn!("could not restore constraint {idx}: {err}");
            ok = false;
        }
    }
    tracing::info!("power limits reset to their recorded values (ok={ok})");
    Ok(ok)
}

/// Run ryzenadj and return its stdout. A non-zero exit is a failure.
async fn run_ryzenadj(binary: &Path, args: &[String]) -> std::io::Result<String> {
    let run = tokio::process::Command::new(binary)
        .args(args)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    let output = tokio::time::timeout(RYZENADJ_TIMEOUT, run)
        .await
        .map_err(|_| std::io::Error::new(std::io::ErrorKind::TimedOut, "ryzenadj timed out"))??;
    if !output.status.success() {
        return Err(std::io::Error::other(format!(
            "ryzenadj exited {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Read one `ryzenadj --info` row's value, in mW.
///
/// Rows look like `| STAPM LIMIT | 25.000 | stapm-limit |`. The value column is
/// watts on current ryzenadj but has been milliwatts before, so the MAGNITUDE
/// decides: a real limit is never 1000 W and never 25 mW.
fn parse_ryzenadj_row(info: &str, label: &str) -> Option<i64> {
    for line in info.lines() {
        if !line.to_uppercase().contains(label) {
            continue;
        }
        // Skip the first cell - that is the row's name, not its value.
        let cells = line.split('|').map(str::trim).filter(|c| !c.is_empty());
        for cell in cells.skip(1) {
            let Ok(value) = cell.parse::<f64>() else {
                continue;
            };
            if !cell.chars().all(|c| c.is_ascii_digit() || c == '.') {
                continue;
            }
            if value <= 0.0 {
                return None;
            }
            return Some(if value > 1000.0 {
                value as i64
            } else {
                (value * 1000.0) as i64
            });
        }
    }
    None
}

/// Every power limit ryzenadj reports, in mW, keyed by its write flag.
async fn ryzenadj_limits_mw(binary: &Path) -> BTreeMap<String, i64> {
    let Ok(info) = run_ryzenadj(binary, &["--info".to_owned()]).await else {
        return BTreeMap::new();
    };
    let mut out = BTreeMap::new();
    for (label, flag) in RYZENADJ_LIMITS {
        if let Some(value) = parse_ryzenadj_row(&info, label) {
            out.insert((*flag).to_owned(), value);
        }
    }
    out
}

/// Record the AMD limits, on top of the general baseline.
async fn snapshot_tdp(roots: &sys::Roots, binary: &Path) -> Result<()> {
    // The full governor/EPP/RAPL baseline FIRST: capture_if_absent early-returns
    // once the file exists, so adding our own key before it runs would mean the
    // governor's original value is never recorded and RevertAll cannot restore it.
    snapshot(roots)?;
    let mut snap = state::Snapshot::load(&roots.state_file()).unwrap_or_default();
    if snap.ryzenadj_limits_mw.is_some() {
        return Ok(());
    }
    let limits = ryzenadj_limits_mw(binary).await;
    if limits.is_empty() {
        return Ok(());
    }
    // Kept for a helper upgraded under a running daemon: an older snapshot
    // recorded only STAPM and ResetTDP still falls back to it.
    snap.ryzenadj_stapm_mw = limits.get("stapm-limit").copied();
    snap.ryzenadj_limits_mw = Some(limits);
    state::save(roots, &snap)
        .map_err(|err| HelperError::Failed(format!("could not record the AMD baseline: {err}")))
}

/// Which limits ResetTDP should write back.
///
/// Every recorded limit goes back to ITS OWN original value. Restoring them
/// all to STAPM would clamp the burst limit down to the sustained one and
/// quietly cost headroom the machine shipped with - that was a real bug
/// (f33c437), where a 25/30 W box came back as 25/25 until reboot.
fn limits_to_restore(snapshot: &state::Snapshot) -> BTreeMap<String, i64> {
    if let Some(limits) = &snapshot.ryzenadj_limits_mw {
        if !limits.is_empty() {
            return limits.clone();
        }
    }
    // A snapshot written by an older helper recorded only STAPM.
    match snapshot.ryzenadj_stapm_mw {
        Some(stapm) => BTreeMap::from([("stapm-limit".to_owned(), stapm)]),
        None => BTreeMap::new(),
    }
}

/// Set the AMD sustained TDP, with a little short-burst headroom.
pub async fn set_tdp(roots: &sys::Roots, watts: u32) -> Result<bool> {
    let Some(binary) = ryzenadj() else {
        tracing::warn!("SetTDP: ryzenadj is not installed");
        return Ok(false);
    };
    let watts = watts.clamp(TDP_MIN_W, TDP_MAX_W);
    snapshot_tdp(roots, binary).await?;

    let mw = watts * 1000;
    let fast = TDP_MAX_W.min(watts + 8) * 1000;
    let args = vec![
        format!("--stapm-limit={mw}"),
        format!("--slow-limit={mw}"),
        format!("--fast-limit={fast}"),
    ];
    match run_ryzenadj(binary, &args).await {
        Ok(_) => {
            tracing::info!("AMD TDP set to {watts} W (fast {} W)", fast / 1000);
            Ok(true)
        }
        Err(err) => {
            tracing::warn!("ryzenadj SetTDP({watts}W) failed: {err}");
            Ok(false)
        }
    }
}

/// Put the AMD limits back the way they were.
pub async fn reset_tdp(roots: &sys::Roots) -> Result<bool> {
    let Some(binary) = ryzenadj() else {
        return Ok(true);
    };
    let snap = state::Snapshot::load(&roots.state_file()).unwrap_or_default();
    let limits = limits_to_restore(&snap);
    if limits.is_empty() {
        tracing::info!("ResetTDP: no snapshot; leaving current limits (cleared on reboot)");
        return Ok(true);
    }
    // Restore every limit to ITS OWN original value. Restoring them all to
    // STAPM would clamp the burst limit down to the sustained one and quietly
    // cost headroom the machine shipped with - that was a real bug, f33c437.
    let args: Vec<String> = limits
        .iter()
        .map(|(flag, value)| format!("--{flag}={value}"))
        .collect();
    match run_ryzenadj(binary, &args).await {
        Ok(_) => {
            tracing::info!("AMD TDP restored: {limits:?}");
            Ok(true)
        }
        Err(err) => {
            tracing::warn!("ryzenadj ResetTDP failed: {err}");
            Ok(false)
        }
    }
}

fn snapshot(roots: &sys::Roots) -> Result<()> {
    state::capture_if_absent(roots)
        .map_err(|err| HelperError::Failed(format!("could not record the baseline: {err}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-power-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn reads_both_constraints() {
        let base = scratch("ok");
        std::fs::write(base.join("constraint_0_power_limit_uw"), "107000000\n").unwrap();
        std::fs::write(base.join("constraint_1_power_limit_uw"), "107000000\n").unwrap();
        assert_eq!(get_power_limits(&base).unwrap(), (107_000_000, 107_000_000));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn no_rapl_is_an_error_not_a_zero() {
        // A zero would be a power limit a caller could act on. "Cannot report"
        // has to stay distinguishable from "is nothing".
        assert!(get_power_limits(Path::new("/nonexistent/gmp")).is_err());
    }

    #[test]
    fn a_non_numeric_constraint_is_an_error() {
        let base = scratch("junk");
        std::fs::write(base.join("constraint_0_power_limit_uw"), "banana\n").unwrap();
        std::fs::write(base.join("constraint_1_power_limit_uw"), "1\n").unwrap();
        assert!(get_power_limits(&base).is_err());
        let _ = std::fs::remove_dir_all(&base);
    }

    fn machine(tag: &str, pl: Option<(u64, u64)>, max_uw: Option<u64>) -> sys::Roots {
        let base = scratch(tag);
        let rapl = base.join("rapl");
        std::fs::create_dir_all(&rapl).unwrap();
        if let Some((pl1, pl2)) = pl {
            std::fs::write(rapl.join("constraint_0_power_limit_uw"), pl1.to_string()).unwrap();
            std::fs::write(rapl.join("constraint_1_power_limit_uw"), pl2.to_string()).unwrap();
        }
        if let Some(max) = max_uw {
            for idx in [0, 1] {
                std::fs::write(
                    rapl.join(format!("constraint_{idx}_max_power_uw")),
                    max.to_string(),
                )
                .unwrap();
            }
        }
        sys::Roots {
            cpu: base.join("no-cpu"),
            rapl,
            state_dir: base.join("run"),
        }
    }

    fn pl_now(roots: &sys::Roots) -> (u64, u64) {
        let read = |idx: u8| {
            sys::read_trimmed(&rapl_constraint(&roots.rapl, idx, "power_limit_uw"))
                .unwrap()
                .parse()
                .unwrap()
        };
        (read(0), read(1))
    }

    /// THE BUG THIS METHOD IS FAMOUS FOR. A refused request must leave no
    /// state file behind, or the next real apply never records its baseline
    /// and RevertAll restores what was true at the moment of the REFUSAL.
    #[test]
    fn a_below_floor_request_is_refused_and_records_nothing() {
        let roots = machine("floor", Some((107_000_000, 107_000_000)), Some(45_000_000));
        let err = set_power_limits(&roots, 4_000_000, 107_000_000).unwrap_err();
        assert!(format!("{err:?}").contains("below the"));
        assert_eq!(
            pl_now(&roots),
            (107_000_000, 107_000_000),
            "the value changed"
        );
        assert!(
            !roots.state_file().exists(),
            "a refused call wrote a baseline"
        );
        let _ = std::fs::remove_dir_all(roots.rapl.parent().unwrap());
    }

    #[test]
    fn zero_means_leave_this_constraint_alone() {
        // Not a floor violation, and not a write either.
        let roots = machine("zero", Some((107_000_000, 107_000_000)), Some(45_000_000));
        assert!(set_power_limits(&roots, 0, 20_000_000).unwrap());
        assert_eq!(pl_now(&roots), (107_000_000, 20_000_000));
        let _ = std::fs::remove_dir_all(roots.rapl.parent().unwrap());
    }

    #[test]
    fn a_request_above_the_firmware_maximum_is_clamped_not_refused() {
        // On this project's own test machine the firmware max is 45 W while
        // PL1 reads 107 W, so asking for more legitimately LOWERS the limit.
        let roots = machine("clamp", Some((107_000_000, 107_000_000)), Some(45_000_000));
        assert!(set_power_limits(&roots, 90_000_000, 90_000_000).unwrap());
        assert_eq!(pl_now(&roots), (45_000_000, 45_000_000));
        let _ = std::fs::remove_dir_all(roots.rapl.parent().unwrap());
    }

    #[test]
    fn an_unreadable_firmware_maximum_skips_the_write() {
        // Guessing past a missing cap could ask the silicon for a limit it
        // will not honour, so nothing is written and the answer is false.
        let roots = machine("nocap", Some((107_000_000, 107_000_000)), None);
        assert!(!set_power_limits(&roots, 20_000_000, 20_000_000).unwrap());
        assert_eq!(pl_now(&roots), (107_000_000, 107_000_000));
        let _ = std::fs::remove_dir_all(roots.rapl.parent().unwrap());
    }

    #[test]
    fn reset_with_no_snapshot_is_success_not_failure() {
        // Nothing was recorded, so the machine is already as it was.
        let roots = machine("noreset", Some((1, 1)), None);
        assert!(reset_power_limits(&roots).unwrap());
        let _ = std::fs::remove_dir_all(roots.rapl.parent().unwrap());
    }

    #[test]
    fn reset_restores_the_recorded_limits_and_keeps_the_snapshot() {
        let roots = machine("reset", Some((107_000_000, 107_000_000)), Some(45_000_000));
        assert!(set_power_limits(&roots, 40_000_000, 40_000_000).unwrap());
        assert_eq!(pl_now(&roots), (40_000_000, 40_000_000));

        assert!(reset_power_limits(&roots).unwrap());
        assert_eq!(pl_now(&roots), (107_000_000, 107_000_000));
        // The file stays: the governor and EPP it also records may still be
        // applied for another running game.
        assert!(roots.state_file().exists());
        let _ = std::fs::remove_dir_all(roots.rapl.parent().unwrap());
    }

    #[test]
    fn ryzenadj_rows_are_read_in_milliwatts_whichever_unit_they_use() {
        let watts = "| STAPM LIMIT | 25.000 | stapm-limit |";
        let milliwatts = "| STAPM LIMIT | 25000 | stapm-limit |";
        assert_eq!(parse_ryzenadj_row(watts, "STAPM LIMIT"), Some(25_000));
        assert_eq!(parse_ryzenadj_row(milliwatts, "STAPM LIMIT"), Some(25_000));
    }

    #[test]
    fn a_zero_or_missing_ryzenadj_row_is_no_value() {
        assert_eq!(
            parse_ryzenadj_row("| STAPM LIMIT | 0.000 | stapm-limit |", "STAPM LIMIT"),
            None
        );
        assert_eq!(
            parse_ryzenadj_row("| PPT LIMIT FAST | 30.000 |", "STAPM LIMIT"),
            None
        );
        assert_eq!(parse_ryzenadj_row("", "STAPM LIMIT"), None);
    }

    #[test]
    fn the_row_name_is_never_mistaken_for_the_value() {
        // The flag column sits after the value and is not a number, but the
        // row label can contain digits on some builds.
        let row = "| PPT LIMIT FAST | 30.000 | fast-limit |";
        assert_eq!(parse_ryzenadj_row(row, "PPT LIMIT FAST"), Some(30_000));
    }

    /// f33c437: every limit goes back to its OWN value. Restoring them all to
    /// STAPM silently costs the burst headroom the machine shipped with.
    #[test]
    fn reset_restores_each_amd_limit_to_its_own_value() {
        let snap = state::Snapshot {
            ryzenadj_limits_mw: Some(BTreeMap::from([
                ("stapm-limit".to_owned(), 25_000),
                ("fast-limit".to_owned(), 33_000),
                ("slow-limit".to_owned(), 25_000),
            ])),
            ryzenadj_stapm_mw: Some(25_000),
            ..Default::default()
        };
        let limits = limits_to_restore(&snap);
        assert_eq!(limits.get("fast-limit"), Some(&33_000));
        assert_eq!(limits.get("stapm-limit"), Some(&25_000));
        assert_ne!(limits.get("fast-limit"), limits.get("stapm-limit"));
    }

    #[test]
    fn an_older_snapshot_with_only_stapm_still_restores() {
        // A helper upgraded under a running daemon left this shape behind.
        let snap = state::Snapshot {
            ryzenadj_stapm_mw: Some(25_000),
            ..Default::default()
        };
        assert_eq!(
            limits_to_restore(&snap),
            BTreeMap::from([("stapm-limit".to_owned(), 25_000)])
        );
    }

    #[test]
    fn nothing_recorded_means_nothing_to_restore() {
        assert!(limits_to_restore(&state::Snapshot::default()).is_empty());
    }
}
