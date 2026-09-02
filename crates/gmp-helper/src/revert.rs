//! Putting the machine back the way it was found.
//!
//! Everything here is driven off the snapshot in state.json rather than off
//! anything this process remembers, so a helper that was restarted mid-session
//! still reverts correctly - and so a Python helper can revert what a Rust one
//! applied, which is the whole point of the two implementations sharing a
//! state format.

use crate::error::Result;
use crate::{fans, power, state, sys};

/// Restore the governor, EPP, power limits, AMD limits and fans, then drop the
/// snapshot.
///
/// Returns false if any single step failed, but always attempts all of them:
/// a machine left half-reverted because the first write failed is worse than
/// one where the failure is reported and the rest was put back.
pub async fn revert_all(roots: &sys::Roots) -> Result<bool> {
    let Some(snapshot) = state::Snapshot::load(&roots.state_file()) else {
        tracing::info!("revert_all: nothing to revert");
        return Ok(true);
    };
    let mut ok = true;

    // An EMPTY governor is skipped, not written. get_governor answers "" on a
    // machine with no cpufreq and that empty string is stored, so treating it
    // as a value would try to write nothing to every core.
    if let Some(governor) = snapshot.governor.as_deref().filter(|g| !g.is_empty()) {
        for path in sys::cpu_leaf_paths(&roots.cpu, "scaling_governor") {
            if let Err(err) = sys::write_value(&path, governor) {
                tracing::warn!(
                    "could not restore the governor on {}: {err}",
                    path.display()
                );
                ok = false;
                // The Python stops at the first failure here. If one core will
                // not take the governor the rest almost certainly will not
                // either, and the interesting signal is the first error.
                break;
            }
        }
    }

    // EPP continues past a failure, unlike the governor above. The asymmetry
    // is inherited from the Python helper deliberately: EPP is per-core and a
    // single core refusing it says nothing about the others.
    if let Some(epp) = snapshot.epp.as_deref().filter(|e| !e.is_empty()) {
        for path in sys::cpu_leaf_paths(&roots.cpu, "energy_performance_preference") {
            if let Err(err) = sys::write_value(&path, epp) {
                tracing::warn!("could not restore EPP on {}: {err}", path.display());
                ok = false;
            }
        }
    }

    if !power::restore_power_limits(roots, &snapshot) {
        ok = false;
    }

    // Gated on the recorded STAPM value, matching the Python: no AMD baseline
    // means ryzenadj was never used and there is nothing of ours to undo.
    if snapshot.ryzenadj_stapm_mw.is_some() && !power::reset_tdp(roots).await? {
        ok = false;
    }

    if fans::has_state(roots) && !fans::reset_fans(roots)? {
        ok = false;
    }

    // The snapshot goes last, and unconditionally. Keeping it after a partial
    // revert would make the NEXT apply skip recording a baseline, and the one
    // on disk no longer describes the machine.
    let _ = std::fs::remove_file(roots.state_file());
    tracing::info!("reverted (ok={ok})");
    Ok(ok)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn machine(tag: &str) -> sys::Roots {
        let base = std::env::temp_dir().join(format!("gmp-revert-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        for cpu in ["cpu0", "cpu1"] {
            let freq = base.join("cpu").join(cpu).join("cpufreq");
            std::fs::create_dir_all(&freq).unwrap();
            std::fs::write(freq.join("scaling_governor"), "performance\n").unwrap();
            std::fs::write(freq.join("energy_performance_preference"), "performance\n").unwrap();
        }
        let rapl = base.join("rapl");
        std::fs::create_dir_all(&rapl).unwrap();
        for idx in [0, 1] {
            std::fs::write(
                rapl.join(format!("constraint_{idx}_power_limit_uw")),
                "40000000",
            )
            .unwrap();
        }
        sys::Roots {
            cpu: base.join("cpu"),
            rapl,
            state_dir: base.join("run"),
        }
    }

    fn write_snapshot(roots: &sys::Roots, snapshot: &state::Snapshot) {
        state::save(roots, snapshot).unwrap();
    }

    fn governors(roots: &sys::Roots) -> Vec<String> {
        sys::cpu_leaf_paths(&roots.cpu, "scaling_governor")
            .iter()
            .map(|p| sys::read_trimmed(p).unwrap())
            .collect()
    }

    #[tokio::test]
    async fn nothing_recorded_is_success() {
        let roots = machine("empty");
        assert!(revert_all(&roots).await.unwrap());
        let _ = std::fs::remove_dir_all(roots.cpu.parent().unwrap());
    }

    #[tokio::test]
    async fn the_governor_epp_and_power_limits_all_go_back() {
        let roots = machine("full");
        write_snapshot(
            &roots,
            &state::Snapshot {
                governor: Some("powersave".into()),
                epp: Some("balance_power".into()),
                pl1_uw: Some(107_000_000),
                pl2_uw: Some(107_000_000),
                ..Default::default()
            },
        );
        assert!(revert_all(&roots).await.unwrap());
        assert_eq!(governors(&roots), ["powersave", "powersave"]);
        assert_eq!(
            sys::read_trimmed(&roots.rapl.join("constraint_0_power_limit_uw")).unwrap(),
            "107000000"
        );
        assert!(
            !roots.state_file().exists(),
            "the snapshot outlived the revert"
        );
        let _ = std::fs::remove_dir_all(roots.cpu.parent().unwrap());
    }

    #[tokio::test]
    async fn an_empty_governor_is_skipped_not_written() {
        // A machine with no cpufreq records "" - writing that to every core
        // would be nonsense.
        let roots = machine("emptygov");
        write_snapshot(
            &roots,
            &state::Snapshot {
                governor: Some(String::new()),
                ..Default::default()
            },
        );
        assert!(revert_all(&roots).await.unwrap());
        assert_eq!(governors(&roots), ["performance", "performance"]);
        let _ = std::fs::remove_dir_all(roots.cpu.parent().unwrap());
    }

    #[tokio::test]
    async fn the_snapshot_is_dropped_even_when_a_step_failed() {
        // Keeping it would make the next apply skip recording a baseline,
        // while the file no longer describes the machine.
        let roots = machine("partial");
        write_snapshot(
            &roots,
            &state::Snapshot {
                governor: Some("powersave".into()),
                pl1_uw: Some(1),
                pl2_uw: Some(1),
                ..Default::default()
            },
        );
        std::fs::remove_dir_all(&roots.rapl).unwrap();
        assert!(
            !revert_all(&roots).await.unwrap(),
            "a failed step must be reported"
        );
        assert_eq!(
            governors(&roots),
            ["powersave", "powersave"],
            "the rest still reverted"
        );
        assert!(!roots.state_file().exists());
        let _ = std::fs::remove_dir_all(roots.cpu.parent().unwrap());
    }

    #[tokio::test]
    async fn a_snapshot_written_by_the_python_helper_reverts() {
        // THE MIXED-INSTALL PATH: this is the fixture captured from the Python
        // helper, reverted by the Rust one.
        let roots = machine("python");
        std::fs::create_dir_all(&roots.state_dir).unwrap();
        std::fs::write(
            roots.state_file(),
            include_str!("../../../tests/fixtures/state.python.json"),
        )
        .unwrap();
        assert!(revert_all(&roots).await.unwrap());
        assert_eq!(governors(&roots), ["powersave", "powersave"]);
        assert_eq!(
            sys::read_trimmed(&roots.rapl.join("constraint_1_power_limit_uw")).unwrap(),
            "107000000"
        );
        let _ = std::fs::remove_dir_all(roots.cpu.parent().unwrap());
    }
}
