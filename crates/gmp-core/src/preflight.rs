//! Pre-flight: what is wrong with this machine for gaming, and what to do.
//!
//! A port of the pure slice of `src/goblinmode/preflight.py`. The probes stay
//! in Python - each check reads /proc/sys, a kernel version, an rlimit or the
//! presence of a binary - but two things move here and both matter.
//!
//! The METADATA is what preflight promises the rest of the system: which
//! sysctl a failing check proposes, at what value. That crosses into the
//! privileged helper, which decides whether to write it. A key preflight
//! offers that the helper's allowlist does not carry is a fix that silently
//! never applies while the user is told their machine was tuned.
//!
//! The AGGREGATION is the part with rules rather than readings: a check may
//! not report worse than the severity it declares, and only failing checks
//! propose a fix.

/// Statuses, spelled as the Python spells them - they end up in JSON that the
/// GUI and the bug report both read.
pub const OK: &str = "ok";
pub const WARN: &str = "warn";
pub const FAIL: &str = "fail";
pub const INFO: &str = "info";
pub const UNKNOWN: &str = "unknown";

/// Where the generated drop-in is written.
pub const SYSCTL_DROPIN: &str = "/etc/sysctl.d/99-goblin-mode-pro.conf";

/// One check's fixed description. The probe that fills in a result is the
/// caller's business.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Check {
    pub id: &'static str,
    pub title: &'static str,
    pub why: &'static str,
    /// `(key, desired)` - a runtime fix the helper can apply and a drop-in line.
    pub sysctl: Option<(&'static str, &'static str)>,
    /// A persistent boot-parameter fix, where no runtime one exists.
    pub kernel_param: Option<&'static str>,
    /// Free text for a remedy that cannot be automated.
    pub fix_hint: &'static str,
    /// The worst this check is allowed to report.
    pub severity: &'static str,
}

pub const CHECKS: &[Check] = &[
    Check {
        id: r"max_map_count",
        title: r"vm.max_map_count",
        why: r"UE5 / Star Citizen crash guard",
        sysctl: Some((r"vm.max_map_count", r"2147483642")),
        kernel_param: None,
        fix_hint: r"",
        severity: r"fail",
    },
    Check {
        id: r"nofile",
        title: r"Open-file limit (esync)",
        why: r"Wine esync handle ceiling",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Raise DefaultLimitNOFILE in /etc/systemd/system.conf.d/ and hard nofile in /etc/security/limits.d/ to 524288.",
        severity: r"warn",
    },
    Check {
        id: r"split_lock",
        title: r"Split-lock mitigation",
        why: r"heavy-stutter source in some titles",
        sysctl: Some((r"kernel.split_lock_mitigate", r"0")),
        kernel_param: Some(r"split_lock_detect=off"),
        fix_hint: r"",
        severity: r"warn",
    },
    Check {
        id: r"nvidia_modeset",
        title: r"nvidia-drm modeset",
        why: r"Wayland + explicit sync",
        sysctl: None,
        kernel_param: Some(r"nvidia-drm.modeset=1"),
        fix_hint: r"Also add 'options nvidia_drm modeset=1 fbdev=1' to /etc/modprobe.d/.",
        severity: r"warn",
    },
    Check {
        id: r"thp",
        title: r"Transparent hugepages",
        why: r"allocation-stall stutter",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"echo madvise > /sys/kernel/mm/transparent_hugepage/enabled (persist via a systemd tmpfiles rule or kernel arg).",
        severity: r"warn",
    },
    Check {
        id: r"compaction",
        title: r"vm.compaction_proactiveness",
        why: r"frame hitches from memory compaction",
        sysctl: Some((r"vm.compaction_proactiveness", r"0")),
        kernel_param: None,
        fix_hint: r"",
        severity: r"warn",
    },
    Check {
        id: r"swappiness",
        title: r"vm.swappiness",
        why: r"paging out game memory",
        sysctl: Some((r"vm.swappiness", r"10")),
        kernel_param: None,
        fix_hint: r"",
        severity: r"info",
    },
    Check {
        id: r"fsync",
        title: r"Kernel fsync support",
        why: r"WINEFSYNC vs esync fallback",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Update to a kernel >= 5.16 (CachyOS ships current).",
        severity: r"warn",
    },
    Check {
        id: r"gamemode",
        title: r"feralinteractive gamemode",
        why: r"per-game tuning launchers expect",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Install the 'gamemode' package.",
        severity: r"warn",
    },
    Check {
        id: r"ananicy",
        title: r"ananicy-cpp niceness conflict",
        why: r"three tools fighting over process priority",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Leave renice off for games (automatic for new profiles), stop ananicy-cpp, or turn off 'Wrap with GameMode' per game.",
        severity: r"warn",
    },
    Check {
        id: r"mangohud",
        title: r"MangoHud",
        why: r"overlay + frame-rate watchdog",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Install the 'mangohud' package.",
        severity: r"warn",
    },
    Check {
        id: r"vulkan_icd",
        title: r"Vulkan driver (ICD)",
        why: r"no ICD = no game",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Install the vulkan driver for your GPU.",
        severity: r"warn",
    },
    Check {
        id: r"userns",
        title: r"User namespaces",
        why: r"Steam Runtime container + anti-cheat",
        sysctl: Some((r"user.max_user_namespaces", r"28633")),
        kernel_param: None,
        fix_hint: r"If this stays 0 after the fix, your kernel disables userns entirely (kernel.unprivileged_userns_clone / a build option).",
        severity: r"fail",
    },
    Check {
        id: r"userns_clone",
        title: r"Unprivileged userns clone (Debian/Ubuntu)",
        why: r"Steam Runtime container + anti-cheat",
        sysctl: Some((r"kernel.unprivileged_userns_clone", r"1")),
        kernel_param: None,
        fix_hint: r"Debian/Ubuntu only - mainline kernels don't have this knob.",
        severity: r"fail",
    },
    Check {
        id: r"anticheat",
        title: r"Anti-cheat (EAC / BattlEye)",
        why: r"how anti-cheat games run on Linux",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Nothing to install - set the game to Proton Experimental if it won't launch.",
        severity: r"info",
    },
    Check {
        id: r"swap",
        title: r"Swap / zram",
        why: r"OOM protection for RAM spikes",
        sysctl: None,
        kernel_param: None,
        fix_hint: r"Enable zram (e.g. the 'zram-generator' package).",
        severity: r"info",
    },
];

/// What a probe found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckResult {
    pub status: String,
    pub value: String,
    pub detail: String,
}

impl CheckResult {
    pub fn new(status: &str, value: impl Into<String>, detail: impl Into<String>) -> Self {
        Self {
            status: status.to_owned(),
            value: value.into(),
            detail: detail.into(),
        }
    }

    /// A result with nothing to say beyond its status.
    pub fn bare(status: &str) -> Self {
        Self::new(status, "", "")
    }
}

/// Cap a result at the severity its check declared.
///
/// A check that describes itself as advisory cannot shout FAIL, however bad
/// the reading. This is what keeps `vm.swappiness` - a defensible choice on a
/// machine with little RAM - from looking like a broken system.
pub fn apply_severity(status: &str, severity: &str) -> String {
    if status == FAIL && severity == WARN {
        return WARN.to_owned();
    }
    if status == WARN && severity == INFO {
        return INFO.to_owned();
    }
    status.to_owned()
}

/// One finished row, as the GUI and the bug report see it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub id: String,
    pub status: String,
    pub sysctl: Option<(String, String)>,
}

/// Count each status. Unknown statuses are counted too rather than dropped, so
/// a typo shows up as a number nobody expected instead of vanishing.
pub fn summary(rows: &[Row]) -> std::collections::BTreeMap<String, usize> {
    let mut out = std::collections::BTreeMap::new();
    for status in [OK, WARN, FAIL, INFO, UNKNOWN] {
        out.insert(status.to_owned(), 0);
    }
    for row in rows {
        *out.entry(row.status.clone()).or_insert(0) += 1;
    }
    out
}

/// The sysctls a run wants applied: those on failing checks that have one.
///
/// An OK check proposes nothing even though it has a sysctl - the value is
/// already right, and writing it anyway would record a change that was not one.
pub fn pending_sysctls(rows: &[Row]) -> Vec<(String, String)> {
    rows.iter()
        .filter(|r| r.status == WARN || r.status == FAIL)
        .filter_map(|r| r.sysctl.clone())
        .collect()
}

/// The `/etc/sysctl.d` snippet that fixes every sysctl-fixable failing check.
pub fn sysctl_dropin_text(rows: &[Row]) -> String {
    let mut out = String::from(
        "# Installed by Goblin Mode Pro - pre-flight fixes

",
    );
    for (key, value) in pending_sysctls(rows) {
        out.push_str(&format!(
            "{key} = {value}
"
        ));
    }
    out
}

// The sentences each decision reports, verbatim from Python.
const MAX_MAP: &str = r"Unreal Engine 4/5 titles, Star Citizen and others crash on launch or mid-session without a high value. Recommended: 2147483642.";
const NOFILE: &str = r"Wine/Proton esync opens many file descriptors; a low hard limit causes 'esync: up to N handles' failures and crashes.";
const SPLIT_NA: &str = r"This kernel doesn't expose the knob.";
const SPLIT_ON: &str = r"Split-lock mitigation stalls threads that do unaligned atomics - a known heavy-stutter source in RDR2, Elden Ring and a few others.";
const COMPACT: &str = r"Proactive memory compaction (default 20) can introduce frame hitches; gaming kernels set it to 0.";
const SWAP: &str =
    r"High swappiness can page out game memory under pressure; 10 is a common gaming value.";
const FSYNC_OK: &str = r"futex_waitv present - WINEFSYNC works.";
const FSYNC_OLD: &str = r"Kernel < 5.16 has no futex_waitv; Proton fsync falls back to esync.";

// ---------------------------------------------------------------------------
// The decisions. Each takes the reading its Python counterpart went and got.
//
// Every sentence below is generated from the Python module rather than
// retyped: they are what the user reads, and an earlier attempt to write them
// by hand lost its line continuations and shipped ten spaces mid-sentence.
// ---------------------------------------------------------------------------

/// UE5, Star Citizen and others crash without a high value.
///
/// An absent reading is treated as ZERO and therefore as a failure, not as
/// UNKNOWN: a kernel that does not expose the knob cannot satisfy the titles
/// this check exists for.
pub fn max_map_count(value: Option<i64>) -> CheckResult {
    let v = value.unwrap_or(0);
    if v >= 1_048_576 {
        return CheckResult::new(OK, v.to_string(), "");
    }
    CheckResult::new(FAIL, v.to_string(), MAX_MAP)
}

/// Wine esync opens a lot of file descriptors.
///
/// Unreadable is UNKNOWN here, unlike max_map_count: a limit that could not be
/// measured is not evidence of a low one, and reporting FAIL would send the
/// user after a problem that may not exist.
pub fn nofile(hard: Option<i64>) -> CheckResult {
    let Some(hard) = hard else {
        return CheckResult::bare(UNKNOWN);
    };
    if hard >= 524_288 {
        return CheckResult::new(OK, hard.to_string(), "");
    }
    CheckResult::new(WARN, hard.to_string(), NOFILE)
}

/// Split-lock mitigation stalls threads doing unaligned atomics.
///
/// A kernel without the knob is INFO, not a problem to fix - most kernels do
/// not expose it.
pub fn split_lock(value: Option<&str>) -> CheckResult {
    let Some(v) = value else {
        return CheckResult::new(INFO, "n/a", SPLIT_NA);
    };
    if v == "0" {
        return CheckResult::new(OK, "off", "");
    }
    CheckResult::new(WARN, "on", SPLIT_ON)
}

/// Proactive compaction can introduce frame hitches.
pub fn compaction(value: Option<i64>) -> CheckResult {
    let Some(v) = value else {
        return CheckResult::bare(UNKNOWN);
    };
    if v <= 5 {
        return CheckResult::new(OK, v.to_string(), "");
    }
    CheckResult::new(WARN, v.to_string(), COMPACT)
}

/// High swappiness can page out game memory.
///
/// Reported as INFO above the threshold rather than WARN: on a machine with
/// little RAM a high value is a defensible choice, not a fault.
pub fn swappiness(value: Option<i64>) -> CheckResult {
    let Some(v) = value else {
        return CheckResult::bare(UNKNOWN);
    };
    if v <= 20 {
        return CheckResult::new(OK, v.to_string(), "");
    }
    CheckResult::new(INFO, v.to_string(), SWAP)
}

/// Proton's fsync needs `futex_waitv`, which arrived in 5.16.
pub fn fsync(major: u32, minor: u32) -> CheckResult {
    let version = format!("{major}.{minor}");
    if (major, minor) >= (5, 16) {
        return CheckResult::new(OK, version, FSYNC_OK);
    }
    CheckResult::new(WARN, version, FSYNC_OLD)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(status: &str, sysctl: Option<(&str, &str)>) -> Row {
        Row {
            id: "x".into(),
            status: status.to_owned(),
            sysctl: sysctl.map(|(k, v)| (k.to_owned(), v.to_owned())),
        }
    }

    #[test]
    fn thresholds_are_pinned_from_both_sides() {
        // Each number here was chosen against a named failure mode, and the
        // sentence beside it says which. A threshold that drifts turns a check
        // into decoration that always passes.
        assert_eq!(max_map_count(Some(1_048_576)).status, OK);
        assert_eq!(max_map_count(Some(1_048_575)).status, FAIL);
        assert_eq!(nofile(Some(524_288)).status, OK);
        assert_eq!(nofile(Some(524_287)).status, WARN);
        assert_eq!(compaction(Some(5)).status, OK);
        assert_eq!(compaction(Some(6)).status, WARN);
        assert_eq!(swappiness(Some(20)).status, OK);
        assert_eq!(swappiness(Some(21)).status, INFO);
        assert_eq!(fsync(5, 16).status, OK);
        assert_eq!(fsync(5, 15).status, WARN);
    }

    #[test]
    fn an_absent_reading_means_different_things_to_different_checks() {
        // max_map_count: absent is a failure, because a kernel that cannot do
        // it cannot run the titles the check exists for.
        assert_eq!(max_map_count(None).status, FAIL);
        // nofile: absent is UNKNOWN, because a limit that could not be
        // measured is not evidence of a low one.
        assert_eq!(nofile(None).status, UNKNOWN);
        // split_lock: absent is INFO - most kernels do not expose the knob.
        assert_eq!(split_lock(None).status, INFO);
        assert_eq!(split_lock(None).value, "n/a");
    }

    #[test]
    fn the_severity_cap_only_ever_lowers() {
        assert_eq!(apply_severity(FAIL, WARN), WARN);
        assert_eq!(apply_severity(WARN, INFO), INFO);
        assert_eq!(apply_severity(OK, FAIL), OK, "an OK is never promoted");
        assert_eq!(apply_severity(FAIL, FAIL), FAIL);
        assert_eq!(apply_severity(UNKNOWN, WARN), UNKNOWN);
    }

    #[test]
    fn only_failing_checks_propose_a_fix() {
        // An OK check has a sysctl too. Writing it anyway would record a
        // change that was not one.
        let rows = [
            row(OK, Some(("vm.swappiness", "10"))),
            row(FAIL, Some(("vm.max_map_count", "2147483642"))),
            row(WARN, Some(("vm.compaction_proactiveness", "0"))),
            row(FAIL, None),
            row(INFO, Some(("vm.swappiness", "10"))),
        ];
        assert_eq!(
            pending_sysctls(&rows),
            vec![
                ("vm.max_map_count".to_owned(), "2147483642".to_owned()),
                ("vm.compaction_proactiveness".to_owned(), "0".to_owned()),
            ]
        );
        let text = sysctl_dropin_text(&rows);
        assert!(text.contains("vm.max_map_count = 2147483642"));
        assert!(!text.contains("vm.swappiness"));
        assert!(text.ends_with('\n'));
    }

    #[test]
    fn the_table_is_internally_consistent() {
        for check in CHECKS {
            assert!(
                matches!(check.severity, OK | WARN | FAIL | INFO | UNKNOWN),
                "{} has severity {}",
                check.id,
                check.severity
            );
            // A check with nothing to offer - no sysctl, no boot parameter and
            // no hint - can report a problem the user cannot act on.
            assert!(
                check.sysctl.is_some()
                    || check.kernel_param.is_some()
                    || !check.fix_hint.is_empty(),
                "{} reports problems with no remedy",
                check.id
            );
        }
        let mut ids: Vec<&str> = CHECKS.iter().map(|c| c.id).collect();
        ids.sort_unstable();
        let before = ids.len();
        ids.dedup();
        assert_eq!(ids.len(), before, "two checks share an id");
    }
}
