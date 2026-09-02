//! Preemptive fan spin-up.
//!
//! Most laptops and handhelds expose no writable pwm control at all - the
//! EC/BIOS owns the fan curve - so everything here is best effort and no-ops
//! cleanly on the majority of systems. Where a control IS exposed, hwmon's own
//! convention is used: `pwmN_enable=1` switches a channel to manual and `pwmN`
//! is a 0-255 duty cycle.
//!
//! Taking a channel off the EC curve outlives the caller, which is why
//! SpinUpFans has its own polkit action - and why ResetFans deliberately does
//! not. Handing control back to the embedded controller must always be
//! possible without a prompt, or a user with no polkit agent running is stuck
//! with whatever duty was last set.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{HelperError, Result};
use crate::sys;

/// SpinUpFans only ever spins fans UP. There is no legitimate reason to drive
/// a duty cycle low through it: doing so switches a channel out of automatic
/// control and REDUCES cooling, which is a hardware-damage vector rather than
/// a feature. Anything below this is refused.
pub const MIN_FAN_PERCENT: u32 = 40;

/// What a channel looked like before it was touched.
///
/// Same compatibility rules as the state snapshot, and for the same reason:
/// either helper may find a file the other wrote. Unknown keys are carried
/// through untouched so that rolling back from a newer version cannot silently
/// drop a field it recorded - and for fans, a dropped field is a channel that
/// does not get handed back to the EC.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct FanChannel {
    /// The mode: 1 = manual, usually 2 = automatic.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    enable: Option<String>,
    /// The duty cycle, 0-255.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pwm: Option<String>,
    /// Anything this version does not know about, preserved on rewrite.
    #[serde(flatten)]
    unknown: BTreeMap<String, serde_json::Value>,
}

/// Whether a previous spin-up is still recorded. RevertAll only calls
/// reset_fans when there is something to reset.
pub(crate) fn has_state(roots: &sys::Roots) -> bool {
    fan_state_file(roots).exists()
}

fn fan_state_file(roots: &sys::Roots) -> PathBuf {
    roots.state_dir.join("fans.json")
}

/// Every hwmon `pwmN` that looks controllable - one with the standard adjacent
/// `pwmN_enable` mode switch. A pwm with no enable file cannot be taken off
/// the EC curve and put back, so it is not a target.
fn pwm_controls(hwmon_base: &Path) -> Vec<PathBuf> {
    let Ok(hwmons) = std::fs::read_dir(hwmon_base) else {
        return Vec::new();
    };
    let mut roots: Vec<PathBuf> = hwmons
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("hwmon"))
        })
        .collect();
    roots.sort();

    let mut out = Vec::new();
    for hwmon in roots {
        let Ok(entries) = std::fs::read_dir(&hwmon) else {
            continue;
        };
        let mut pwms: Vec<PathBuf> = entries
            .flatten()
            .map(|e| e.path())
            .filter(|p| {
                let name = p
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned();
                is_pwm_name(&name) && hwmon.join(format!("{name}_enable")).exists()
            })
            .collect();
        pwms.sort();
        out.extend(pwms);
    }
    out
}

/// `pwm` followed by digits and nothing else - so `pwm1` matches but
/// `pwm1_enable` and `pwm1_auto_point1_temp` do not.
fn is_pwm_name(name: &str) -> bool {
    match name.strip_prefix("pwm") {
        Some(rest) => !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()),
        None => false,
    }
}

fn enable_path(pwm: &Path) -> PathBuf {
    let name = pwm
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned();
    pwm.with_file_name(format!("{name}_enable"))
}

/// Percent to an hwmon 0-255 duty.
///
/// Rounds half to EVEN, which is what Python's `round()` does and what the
/// Python helper therefore writes. It matters at exactly one setting in the
/// permitted range: 70% is 178.5, which rounds to 178 here and would be 179
/// under the more obvious "add a half and truncate". A one-step duty
/// difference is physically nothing, but two implementations of the same
/// interface disagreeing about what 70% means is the sort of thing that later
/// gets found the hard way.
fn duty_for(percent: u32) -> u32 {
    let total = percent * 255;
    let (quotient, remainder) = (total / 100, total % 100);
    match remainder.cmp(&50) {
        std::cmp::Ordering::Greater => quotient + 1,
        std::cmp::Ordering::Less => quotient,
        std::cmp::Ordering::Equal => quotient + (quotient % 2),
    }
}

fn load_fan_state(roots: &sys::Roots) -> Option<BTreeMap<String, FanChannel>> {
    let text = std::fs::read_to_string(fan_state_file(roots)).ok()?;
    serde_json::from_str(&text).ok()
}

/// Switch every writable pwm to manual at `percent` duty.
///
/// Returns false - NOT an error - wherever the EC exposes no writable pwm
/// control, which is most systems. A machine whose fans cannot be driven is
/// working normally, not broken.
pub fn spin_up_fans(roots: &sys::Roots, hwmon_base: &Path, percent: u32) -> Result<bool> {
    if percent < MIN_FAN_PERCENT {
        return Err(HelperError::Failed(format!(
            "fan duty {percent}% is below the {MIN_FAN_PERCENT}% floor - \
             SpinUpFans only increases cooling, it never drives fans down"
        )));
    }
    let percent = percent.min(100);
    let pwms = pwm_controls(hwmon_base);
    if pwms.is_empty() {
        return Ok(false);
    }
    let duty = duty_for(percent);

    // Snapshot only if nothing is recorded yet: a second spin-up during the
    // same session must not overwrite the EC's original settings with this
    // helper's own.
    if !fan_state_file(roots).exists() {
        let mut snapshot: BTreeMap<String, FanChannel> = BTreeMap::new();
        for pwm in &pwms {
            let (Ok(enable), Ok(current)) =
                (sys::read_trimmed(&enable_path(pwm)), sys::read_trimmed(pwm))
            else {
                // A channel that cannot be read cannot be restored, so it is
                // left out rather than recorded with a guess.
                continue;
            };
            snapshot.insert(
                pwm.to_string_lossy().into_owned(),
                FanChannel {
                    enable: Some(enable),
                    pwm: Some(current),
                    unknown: BTreeMap::new(),
                },
            );
        }
        if let Err(err) = save_fan_state(roots, &snapshot) {
            tracing::warn!("could not snapshot fan state: {err}");
        }
    }

    let mut ok = false;
    for pwm in &pwms {
        // Manual mode FIRST, then the duty: writing a duty to a channel still
        // under EC control is silently ignored on most firmware.
        match sys::write_value(&enable_path(pwm), "1")
            .and_then(|()| sys::write_value(pwm, &duty.to_string()))
        {
            Ok(()) => ok = true,
            Err(err) => tracing::warn!("fan spin-up write failed for {}: {err}", pwm.display()),
        }
    }
    if ok {
        tracing::info!("fans set to {percent}% duty on {} control(s)", pwms.len());
    }
    Ok(ok)
}

fn save_fan_state(roots: &sys::Roots, data: &BTreeMap<String, FanChannel>) -> std::io::Result<()> {
    std::fs::create_dir_all(&roots.state_dir)?;
    let json = serde_json::to_string_pretty(data)
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    std::fs::write(fan_state_file(roots), json)
}

/// Hand every channel this session touched back to the EC.
pub fn reset_fans(roots: &sys::Roots) -> Result<bool> {
    let file = fan_state_file(roots);
    if !file.exists() {
        return Ok(true);
    }
    let Some(snapshot) = load_fan_state(roots) else {
        // Unreadable or corrupt. There is nothing to restore from, and leaving
        // the file would make every future reset try again forever.
        let _ = std::fs::remove_file(&file);
        return Ok(true);
    };

    let mut ok = true;
    for (path, saved) in &snapshot {
        let pwm = Path::new(path);
        // Duty first, then the mode. Handing a channel back to the EC before
        // setting the duty leaves it briefly at this helper's value under
        // automatic control, and the order is what the Python helper uses.
        let result = saved
            .pwm
            .as_ref()
            .map_or(Ok(()), |value| sys::write_value(pwm, value))
            .and_then(|()| {
                saved
                    .enable
                    .as_ref()
                    .map_or(Ok(()), |value| sys::write_value(&enable_path(pwm), value))
            });
        if let Err(err) = result {
            tracing::warn!("fan reset failed for {}: {err}", pwm.display());
            ok = false;
        }
    }
    let _ = std::fs::remove_file(&file);
    tracing::info!("fan control reset (ok={ok})");
    Ok(ok)
}

/// Hand the fans back if a previous instance died holding them.
///
/// Called at startup BEFORE any request is accepted. A fan left under manual
/// control with no daemon watching it is the one state this helper must never
/// sit in. Note that the governor, EPP and RAPL are deliberately NOT reverted
/// here: they persist in hardware regardless, state.json still holds the
/// pre-game baseline, and RevertAll on the next game exit restores them -
/// whereas reverting here would kill a boost mid-game after a transient
/// restart.
pub fn recover_after_restart(roots: &sys::Roots) {
    if fan_state_file(roots).exists() {
        tracing::warn!("stale fan state from a previous instance - restoring EC control");
        if let Err(err) = reset_fans(roots) {
            tracing::error!("could not restore EC fan control: {err:?}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake(tag: &str, channels: &[(&str, &str, &str)]) -> (sys::Roots, PathBuf) {
        let base = std::env::temp_dir().join(format!("gmp-fans-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let hwmon_base = base.join("hwmon");
        for (name, enable, pwm) in channels {
            let (dir, leaf) = name.split_once('/').unwrap();
            let d = hwmon_base.join(dir);
            std::fs::create_dir_all(&d).unwrap();
            std::fs::write(d.join(leaf), format!("{pwm}\n")).unwrap();
            std::fs::write(d.join(format!("{leaf}_enable")), format!("{enable}\n")).unwrap();
        }
        std::fs::create_dir_all(&hwmon_base).unwrap();
        let roots = sys::Roots {
            cpu: base.join("no-cpu"),
            rapl: base.join("no-rapl"),
            state_dir: base.join("run"),
        };
        (roots, hwmon_base)
    }

    #[test]
    fn seventy_percent_matches_the_python_helper() {
        // 178.5 -> 178, not 179. The one value in the permitted range where
        // round-half-to-even and round-half-up disagree.
        assert_eq!(duty_for(70), 178);
        assert_eq!(duty_for(40), 102);
        assert_eq!(duty_for(50), 128);
        assert_eq!(duty_for(90), 230);
        assert_eq!(duty_for(100), 255);
    }

    #[test]
    fn a_duty_below_the_floor_is_refused() {
        // Driving fans DOWN through this method would take a channel off the
        // EC curve and reduce cooling.
        let (roots, hwmon) = fake("floor", &[("hwmon0/pwm1", "2", "80")]);
        let err = spin_up_fans(&roots, &hwmon, 20).unwrap_err();
        assert!(format!("{err:?}").contains("below the 40% floor"));
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1")).unwrap(),
            "80"
        );
        assert!(
            !fan_state_file(&roots).exists(),
            "a refused call recorded state"
        );
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn a_machine_with_no_writable_pwm_answers_false_not_an_error() {
        // Most systems. Working normally, not broken.
        let (roots, hwmon) = fake("nopwm", &[]);
        assert!(!spin_up_fans(&roots, &hwmon, 80).unwrap());
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn a_pwm_without_an_enable_file_is_not_a_target() {
        let (roots, hwmon) = fake("noenable", &[]);
        std::fs::create_dir_all(hwmon.join("hwmon0")).unwrap();
        std::fs::write(hwmon.join("hwmon0").join("pwm1"), "80\n").unwrap();
        assert!(pwm_controls(&hwmon).is_empty());
        assert!(!spin_up_fans(&roots, &hwmon, 80).unwrap());
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn adjacent_hwmon_files_are_not_mistaken_for_controls() {
        assert!(is_pwm_name("pwm1"));
        assert!(is_pwm_name("pwm12"));
        assert!(!is_pwm_name("pwm1_enable"));
        assert!(!is_pwm_name("pwm1_auto_point1_temp"));
        assert!(!is_pwm_name("pwm"));
        assert!(!is_pwm_name("temp1_input"));
    }

    #[test]
    fn spin_up_then_reset_hands_the_channel_back_exactly_as_found() {
        let (roots, hwmon) = fake(
            "roundtrip",
            &[("hwmon0/pwm1", "2", "80"), ("hwmon1/pwm1", "2", "90")],
        );
        assert!(spin_up_fans(&roots, &hwmon, 80).unwrap());
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1")).unwrap(),
            "204"
        );
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1_enable")).unwrap(),
            "1"
        );

        assert!(reset_fans(&roots).unwrap());
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1")).unwrap(),
            "80"
        );
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1_enable")).unwrap(),
            "2"
        );
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon1").join("pwm1")).unwrap(),
            "90"
        );
        assert!(
            !fan_state_file(&roots).exists(),
            "the state file outlived the reset"
        );
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn a_second_spin_up_does_not_overwrite_the_original_snapshot() {
        let (roots, hwmon) = fake("twice", &[("hwmon0/pwm1", "2", "80")]);
        spin_up_fans(&roots, &hwmon, 60).unwrap();
        spin_up_fans(&roots, &hwmon, 100).unwrap();
        reset_fans(&roots).unwrap();
        // Back to the EC's value, not to this helper's first setting.
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1")).unwrap(),
            "80"
        );
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn reset_with_nothing_recorded_is_success() {
        let (roots, hwmon) = fake("noreset", &[("hwmon0/pwm1", "2", "80")]);
        assert!(reset_fans(&roots).unwrap());
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn a_corrupt_fan_state_file_is_discarded_not_retried_forever() {
        let (roots, hwmon) = fake("corrupt", &[("hwmon0/pwm1", "2", "80")]);
        std::fs::create_dir_all(&roots.state_dir).unwrap();
        std::fs::write(fan_state_file(&roots), "{not json").unwrap();
        assert!(reset_fans(&roots).unwrap());
        assert!(!fan_state_file(&roots).exists());
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    #[test]
    fn a_stale_state_file_hands_the_fans_back_at_startup() {
        // The one state this helper must never sit in: a fan under manual
        // control with nothing watching it.
        let (roots, hwmon) = fake("stale", &[("hwmon0/pwm1", "2", "80")]);
        spin_up_fans(&roots, &hwmon, 100).unwrap();
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1")).unwrap(),
            "255"
        );

        // A new process starts and finds the previous one's state.
        recover_after_restart(&roots);
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1")).unwrap(),
            "80"
        );
        assert_eq!(
            sys::read_trimmed(&hwmon.join("hwmon0").join("pwm1_enable")).unwrap(),
            "2"
        );
        assert!(!fan_state_file(&roots).exists());
        let _ = std::fs::remove_dir_all(hwmon.parent().unwrap());
    }

    /// Real captured fan state, one file from each implementation.
    const PYTHON_FANS: &str = include_str!("../../../tests/fixtures/fans.python.json");
    const RUST_FANS: &str = include_str!("../../../tests/fixtures/fans.rust.json");

    fn parse(text: &str) -> BTreeMap<String, FanChannel> {
        serde_json::from_str(text).expect("a captured fan state must load")
    }

    #[test]
    fn both_implementations_record_fan_state_the_same_way() {
        assert_eq!(parse(PYTHON_FANS), parse(RUST_FANS));
        let channels = parse(PYTHON_FANS);
        assert_eq!(channels.len(), 2);
        let first = &channels["/sys/class/hwmon/hwmon0/pwm1"];
        // enable=2 is the EC's automatic mode - what a reset must restore.
        assert_eq!(first.enable.as_deref(), Some("2"));
        assert_eq!(first.pwm.as_deref(), Some("80"));
    }

    #[test]
    fn a_python_written_fan_state_survives_a_rust_rewrite() {
        let original = parse(PYTHON_FANS);
        let text = serde_json::to_string_pretty(&original).unwrap();
        assert_eq!(parse(&text), original);
    }

    #[test]
    fn an_unknown_channel_field_is_not_dropped_on_rewrite() {
        // A dropped field here is a channel that does not get handed back to
        // the EC, which is the one state this helper must never leave behind.
        let mut value: serde_json::Value = serde_json::from_str(PYTHON_FANS).unwrap();
        value["/sys/class/hwmon/hwmon0/pwm1"]["future_mode"] = serde_json::json!("3");
        let text = serde_json::to_string(&value).unwrap();
        let channels = parse(&text);
        let rewritten = serde_json::to_string(&channels).unwrap();
        assert!(rewritten.contains("future_mode"), "{rewritten}");
        assert!(rewritten.contains("\"3\""), "{rewritten}");
    }
}
