//! CPU frequency governor and energy-performance preference.

use std::collections::BTreeSet;
use std::path::Path;

use crate::error::{HelperError, Result};
use crate::{state, sys};

/// The governor the first CPU is running, or `""` if the machine has no
/// cpufreq at all.
///
/// The empty string is a real answer, not an error: a VM or a machine with
/// `intel_pstate=disable` and no driver has no governor to report, and the
/// Python helper returns `""` there rather than failing. A caller that got an
/// error instead would show "the helper is broken" on a working machine.
pub fn get_governor(cpu_base: &Path) -> std::io::Result<String> {
    match sys::cpu_leaf_paths(cpu_base, "scaling_governor").first() {
        Some(path) => sys::read_trimmed(path),
        None => Ok(String::new()),
    }
}

/// EPP values to accept when the kernel advertises no list of its own - the
/// standard intel_pstate / amd_pstate set.
pub const EPP_FALLBACK: &[&str] = &[
    "default",
    "performance",
    "balance_performance",
    "balance_power",
    "power",
];

/// What cpu0 says it will accept. An unreadable or absent list is an EMPTY
/// set, not an error, and the callers below decide what that means - they
/// differ, and deliberately so.
fn advertised(cpu_base: &Path, leaf: &str) -> BTreeSet<String> {
    let path = cpu_base.join("cpu0").join("cpufreq").join(leaf);
    sys::read_trimmed(&path)
        .map(|text| text.split_whitespace().map(str::to_owned).collect())
        .unwrap_or_default()
}

pub fn available_governors(cpu_base: &Path) -> BTreeSet<String> {
    advertised(cpu_base, "scaling_available_governors")
}

pub fn available_epps(cpu_base: &Path) -> BTreeSet<String> {
    advertised(cpu_base, "energy_performance_available_preferences")
}

/// Set the governor on every CPU.
///
/// Returns false if any core refused the write, true if they all took it.
/// A core that fails does not stop the others: a machine with one offline or
/// quirky core should still get the governor everywhere else.
pub fn set_governor(roots: &sys::Roots, governor: &str) -> Result<bool> {
    // VALIDATE BEFORE SNAPSHOTTING. The reverse order is the bug the
    // conformance suite found in the Python helper: a refused call wrote a
    // baseline, and since the snapshot early-returns once the file exists, the
    // next real change never recorded its own. A refusal must leave nothing.
    if !available_governors(&roots.cpu).contains(governor) {
        // The wording matters: tests/conformance/helper.py asserts the message
        // mentions "unsupported governor".
        return Err(HelperError::Failed(format!(
            "unsupported governor: '{governor}'"
        )));
    }
    snapshot(roots)?;

    let paths = sys::cpu_leaf_paths(&roots.cpu, "scaling_governor");
    // Note there is no `ok = !paths.is_empty()` here, unlike set_epp. It would
    // be unreachable: validation just required a non-empty governor list, and
    // that list is read from cpu0's cpufreq directory, so reaching this line
    // means at least cpu0 has one. The Python helper is shaped the same way.
    let mut ok = true;
    for path in &paths {
        if let Err(err) = sys::write_value(path, governor) {
            tracing::warn!("governor write failed for {}: {err}", path.display());
            ok = false;
        }
    }
    Ok(ok)
}

/// Set the energy/performance preference on every core.
///
/// All-must-succeed: true only if every EPP file accepted the value. A partial
/// write returns false so the caller is not told the machine is in a state it
/// is not.
pub fn set_epp(roots: &sys::Roots, epp: &str) -> Result<bool> {
    let advertised = available_epps(&roots.cpu);
    let accepted = if advertised.is_empty() {
        // A kernel that publishes no list still takes the standard values.
        EPP_FALLBACK.contains(&epp)
    } else {
        advertised.contains(epp)
    };
    if !accepted {
        // "unsupported epp" is what the conformance suite looks for, matched
        // case-insensitively.
        return Err(HelperError::Failed(format!("unsupported EPP: '{epp}'")));
    }
    snapshot(roots)?;

    let paths = sys::cpu_leaf_paths(&roots.cpu, "energy_performance_preference");
    // Unlike the governor, an empty list is reachable here: the fallback set
    // accepts a value on a machine that has no EPP files at all. Nothing was
    // written, so the answer is false rather than a vacuous true.
    let mut ok = !paths.is_empty();
    for path in &paths {
        if let Err(err) = sys::write_value(path, epp) {
            tracing::warn!("EPP write failed for {}: {err}", path.display());
            ok = false;
        }
    }
    Ok(ok)
}

/// A snapshot that cannot be written fails the whole operation, matching the
/// Python helper: changing the machine without recording what to change back
/// to is worse than refusing.
fn snapshot(roots: &sys::Roots) -> Result<()> {
    state::capture_if_absent(roots)
        .map_err(|err| HelperError::Failed(format!("could not record the baseline: {err}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-cpu-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn reads_the_first_cpus_governor() {
        let base = scratch("gov");
        for (cpu, gov) in [("cpu0", "powersave"), ("cpu1", "performance")] {
            std::fs::create_dir_all(base.join(cpu).join("cpufreq")).unwrap();
            std::fs::write(
                base.join(cpu).join("cpufreq").join("scaling_governor"),
                format!("{gov}\n"),
            )
            .unwrap();
        }
        assert_eq!(get_governor(&base).unwrap(), "powersave");
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn no_cpufreq_is_an_empty_string_not_an_error() {
        assert_eq!(get_governor(Path::new("/nonexistent/gmp")).unwrap(), "");
    }

    /// A fake machine: `cpus` cores, each with the given cpufreq leaves, and a
    /// state dir that starts empty.
    fn fake_machine(tag: &str, cpus: usize, governors: &str, epps: &str) -> sys::Roots {
        let base = scratch(tag);
        for n in 0..cpus {
            let freq = base.join(format!("cpu{n}")).join("cpufreq");
            std::fs::create_dir_all(&freq).unwrap();
            std::fs::write(freq.join("scaling_governor"), "powersave\n").unwrap();
            if !epps.is_empty() {
                std::fs::write(
                    freq.join("energy_performance_preference"),
                    "balance_power\n",
                )
                .unwrap();
            }
        }
        let cpu0 = base.join("cpu0").join("cpufreq");
        if !governors.is_empty() {
            std::fs::write(cpu0.join("scaling_available_governors"), governors).unwrap();
        }
        if !epps.is_empty() {
            std::fs::write(cpu0.join("energy_performance_available_preferences"), epps).unwrap();
        }
        sys::Roots {
            cpu: base.clone(),
            rapl: base.join("no-rapl"),
            state_dir: base.join("run"),
        }
    }

    fn read_all(roots: &sys::Roots, leaf: &str) -> Vec<String> {
        sys::cpu_leaf_paths(&roots.cpu, leaf)
            .iter()
            .map(|p| sys::read_trimmed(p).unwrap())
            .collect()
    }

    #[test]
    fn set_governor_writes_every_cpu() {
        let roots = fake_machine("setgov", 4, "performance powersave\n", "");
        assert!(set_governor(&roots, "performance").unwrap());
        assert_eq!(read_all(&roots, "scaling_governor"), ["performance"; 4]);
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }

    /// THE RULE THE CONFORMANCE SUITE ADDED: a refused call must leave NO
    /// state behind, not merely leave its target value alone. Validating
    /// after snapshotting is what made a rejected request poison RevertAll.
    #[test]
    fn a_rejected_governor_changes_nothing_and_records_no_baseline() {
        let roots = fake_machine("rejgov", 2, "performance powersave\n", "");
        let err = set_governor(&roots, "goblin-turbo").unwrap_err();
        let message = format!("{err:?}");
        assert!(
            message.to_lowercase().contains("unsupported governor"),
            "the conformance suite matches on this wording: {message}"
        );
        assert_eq!(read_all(&roots, "scaling_governor"), ["powersave"; 2]);
        assert!(
            !roots.state_file().exists(),
            "a refused call wrote a baseline - this is the R1 bug"
        );
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }

    #[test]
    fn an_accepted_governor_records_the_baseline_once() {
        let roots = fake_machine("snapgov", 2, "performance powersave\n", "");
        assert!(set_governor(&roots, "performance").unwrap());
        let first = std::fs::read_to_string(roots.state_file()).unwrap();
        assert!(
            first.contains("powersave"),
            "the baseline is the PREVIOUS value: {first}"
        );

        // A second change must not overwrite it: RevertAll has to restore what
        // the user started with, not what they had a moment ago.
        assert!(set_governor(&roots, "powersave").unwrap());
        assert_eq!(std::fs::read_to_string(roots.state_file()).unwrap(), first);
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }

    #[test]
    fn set_epp_falls_back_when_the_kernel_advertises_nothing() {
        // EPP files exist but no list is published - the standard set applies.
        let roots = fake_machine("eppfall", 2, "powersave\n", "");
        for n in 0..2 {
            std::fs::write(
                roots
                    .cpu
                    .join(format!("cpu{n}"))
                    .join("cpufreq")
                    .join("energy_performance_preference"),
                "balance_power\n",
            )
            .unwrap();
        }
        assert!(available_epps(&roots.cpu).is_empty());
        assert!(set_epp(&roots, "performance").unwrap());
        assert_eq!(
            read_all(&roots, "energy_performance_preference"),
            ["performance"; 2]
        );
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }

    #[test]
    fn set_epp_rejects_a_value_outside_the_advertised_list() {
        let roots = fake_machine("eppbad", 2, "powersave\n", "performance balance_power\n");
        let err = set_epp(&roots, "ludicrous").unwrap_err();
        assert!(format!("{err:?}")
            .to_lowercase()
            .contains("unsupported epp"));
        assert!(!roots.state_file().exists());
        // "power" is in the fallback set but NOT advertised here, so the
        // advertised list must win over the fallback.
        assert!(set_epp(&roots, "power").is_err());
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }

    #[test]
    fn set_epp_on_a_machine_with_no_epp_files_is_false_not_true() {
        // The fallback accepts the value, but nothing was written. Answering
        // true would tell the caller the box is in a state it is not.
        let roots = fake_machine("eppnone", 2, "powersave\n", "");
        assert!(!set_epp(&roots, "performance").unwrap());
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }

    #[test]
    fn a_core_that_refuses_the_write_makes_the_answer_false() {
        let roots = fake_machine("partial", 3, "performance powersave\n", "");
        let stubborn = roots
            .cpu
            .join("cpu1")
            .join("cpufreq")
            .join("scaling_governor");
        let mut perms = std::fs::metadata(&stubborn).unwrap().permissions();
        perms.set_readonly(true);
        std::fs::set_permissions(&stubborn, perms).unwrap();
        if std::fs::write(&stubborn, "x").is_ok() {
            // Running as root, where read-only means nothing. Nothing to prove.
            let _ = std::fs::remove_dir_all(&roots.cpu);
            return;
        }
        assert!(!set_governor(&roots, "performance").unwrap());
        // The other cores still took it - one bad core does not stop the rest.
        let got = sys::read_trimmed(
            &roots
                .cpu
                .join("cpu2")
                .join("cpufreq")
                .join("scaling_governor"),
        )
        .unwrap();
        assert_eq!(got, "performance");
        let _ = std::fs::remove_dir_all(&roots.cpu);
    }
}
