//! Raising a process's scheduling priority.
//!
//! The riskiest operation in the helper, because it is the only one that acts
//! on a target the CALLER names. Everything else works on the machine; this
//! works on a pid, and a pid is not a stable identifier - the kernel recycles
//! them. The whole shape of this module is about closing the window between
//! "I checked who owns this pid" and "I changed that pid's priority".

use std::os::fd::{AsRawFd, OwnedFd};
use std::os::unix::fs::MetadataExt;

use rustix::process::{Pid, PidfdFlags};

use crate::error::{HelperError, Result};

/// Never let a caller push a process below this. Renice is a "make my game
/// smoother" feature, not a general priority interface, and -10 is already
/// well ahead of everything a desktop normally runs.
pub const NICE_FLOOR: i32 = -10;
const NICE_CEILING: i32 = 19;

/// Raise (or lower) a process's priority.
///
/// `caller_uid` is `Some(0)` ONLY for a genuine root caller. `None` means the
/// bus lookup failed, and it is treated as untrusted - never as root. That
/// distinction is the whole reason this takes an `Option` rather than a uid
/// with a sentinel: the Python helper had a bug here where an unresolvable
/// uid stood in for root, which handed this check to anyone who could make
/// the lookup fail.
pub fn renice(pid: u32, nice: i32, caller_uid: Option<u32>) -> Result<bool> {
    if pid <= 1 {
        // pid 1 is init and pid 0 is not a process. Neither is ever a game.
        return Err(HelperError::Failed(format!("no such process: {pid}")));
    }
    let nice = nice.clamp(NICE_FLOOR, NICE_CEILING);
    // Only an explicit root caller skips the ownership check. `None != Some(0)`
    // is what makes an unknown caller fail closed.
    let enforce_owner = caller_uid != Some(0);

    let Some(target) = Pid::from_raw(pid as i32) else {
        return Err(HelperError::Failed(format!("no such process: {pid}")));
    };

    // PIN THE PROCESS FIRST, then check ownership. Taken in this order the
    // pidfd refers to the task that existed at this instant, so if the pid is
    // recycled before the priority is set, the liveness check below sees the
    // ORIGINAL task gone and refuses - rather than happily renicing whatever
    // process inherited the number. A pre-5.3 kernel has no pidfd_open; there
    // the check is skipped, exactly as in the Python helper.
    let pidfd = rustix::process::pidfd_open(target, PidfdFlags::empty()).ok();

    let owner = std::fs::metadata(format!("/proc/{pid}"))
        .map_err(|_| HelperError::Failed(format!("no such process: {pid}")))?
        .uid();
    if enforce_owner && Some(owner) != caller_uid {
        return Err(HelperError::Failed(format!(
            "process {pid} is not owned by uid {}",
            caller_uid.map_or_else(|| "unknown".to_owned(), |u| u.to_string()),
        )));
    }

    if let Some(fd) = &pidfd {
        if pidfd_state(fd) == PidfdState::Exited {
            return Err(HelperError::Failed(format!("process {pid} went away")));
        }
    }

    rustix::process::setpriority_process(Some(target), nice)
        .map_err(|err| HelperError::Failed(format!("could not renice {pid}: {err}")))?;

    // Every thread, best effort. A thread that has exited between the listing
    // and the write is normal, not a failure, so nothing here is reported.
    if let Ok(entries) = std::fs::read_dir(format!("/proc/{pid}/task")) {
        for tid in entries.flatten() {
            let Ok(raw) = tid.file_name().to_string_lossy().parse::<i32>() else {
                continue;
            };
            if let Some(thread) = Pid::from_raw(raw) {
                let _ = rustix::process::setpriority_process(Some(thread), nice);
            }
        }
    }
    Ok(true)
}

#[derive(Debug, PartialEq, Eq)]
enum PidfdState {
    /// The pinned task is still there.
    Alive,
    /// The pinned task is gone - the pid may since have been reused.
    Exited,
    /// The kernel will not say. Treated like having no pidfd at all.
    Unknown,
}

/// Whether the task a pidfd pinned is still alive.
///
/// The Python helper uses `pidfd_send_signal(fd, 0)` - the null signal, which
/// checks for existence without delivering anything. rustix cannot express
/// that: its `pidfd_send_signal` takes a `Signal`, which is non-zero by
/// construction, and this crate forbids `unsafe` so the raw syscall is not an
/// option either. `/proc/self/fdinfo/<fd>` reports the pidfd's target as
/// `Pid:` and gives `-1` once that task has exited, which answers the same
/// question through a documented interface.
///
/// An unreadable fdinfo, or a kernel too old to report `Pid:`, is `Unknown`
/// and is treated exactly like not having a pidfd - the same position the
/// Python helper is in on a pre-5.3 kernel. It is NOT treated as "exited",
/// because that would make every renice fail on such a system.
fn pidfd_state(fd: &OwnedFd) -> PidfdState {
    let path = format!("/proc/self/fdinfo/{}", fd.as_raw_fd());
    let Ok(text) = std::fs::read_to_string(&path) else {
        tracing::warn!("could not read {path}; skipping the liveness check");
        return PidfdState::Unknown;
    };
    parse_pidfd_state(&text)
}

fn parse_pidfd_state(fdinfo: &str) -> PidfdState {
    for line in fdinfo.lines() {
        if let Some(value) = line.strip_prefix("Pid:") {
            return match value.trim() {
                "-1" => PidfdState::Exited,
                _ => PidfdState::Alive,
            };
        }
    }
    PidfdState::Unknown
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_and_pid_zero_are_refused() {
        for pid in [0, 1] {
            let err = renice(pid, -5, Some(0)).unwrap_err();
            assert!(format!("{err:?}").contains("no such process"));
        }
    }

    #[test]
    fn an_unknown_caller_is_not_root() {
        // THE BUG THIS GUARDS: a None uid standing in for root would hand the
        // ownership check to anyone who could make the bus lookup fail.
        // Renicing our own process with an unknown caller must be refused,
        // even though the same call with Some(our uid) is allowed.
        let me = std::process::id();
        let err = renice(me, 0, None).unwrap_err();
        let message = format!("{err:?}");
        assert!(message.contains("not owned by"), "{message}");
        assert!(message.contains("unknown"), "{message}");
    }

    #[test]
    fn a_process_owned_by_somebody_else_is_refused() {
        // pid 1 is refused earlier, so use a uid that is not ours against our
        // own process: the ownership comparison is what is under test.
        let me = std::process::id();
        let not_me = std::fs::metadata("/proc/self").unwrap().uid() + 1;
        let err = renice(me, 0, Some(not_me)).unwrap_err();
        assert!(format!("{err:?}").contains("not owned by"));
    }

    #[test]
    fn the_nice_value_is_clamped_not_rejected() {
        // A caller asking for -20 gets -10, not an error. The Python helper
        // clamps for the same reason: the GUI sends a slider value and a hard
        // refusal there would be a worse experience than a capped one.
        assert_eq!((-20i32).clamp(NICE_FLOOR, NICE_CEILING), NICE_FLOOR);
        assert_eq!(50i32.clamp(NICE_FLOOR, NICE_CEILING), NICE_CEILING);
        assert_eq!(0i32.clamp(NICE_FLOOR, NICE_CEILING), 0);
    }

    #[test]
    fn fdinfo_parsing_distinguishes_a_dead_target() {
        assert_eq!(
            parse_pidfd_state("pos:\t0\nPid:\t1234\n"),
            PidfdState::Alive
        );
        assert_eq!(parse_pidfd_state("pos:\t0\nPid:\t-1\n"), PidfdState::Exited);
        // A kernel that does not report Pid: must not be read as "exited", or
        // every renice on that machine fails.
        assert_eq!(
            parse_pidfd_state("pos:\t0\nflags:\t02\n"),
            PidfdState::Unknown
        );
    }

    #[test]
    fn a_pidfd_tracks_its_target_across_that_targets_death() {
        // The property the whole ordering exists for, against a real process.
        let mut child = std::process::Command::new("sleep")
            .arg("30")
            .spawn()
            .expect("sleep must be available");
        let pid = Pid::from_raw(child.id() as i32).unwrap();
        let fd = rustix::process::pidfd_open(pid, PidfdFlags::empty())
            .expect("pidfd_open must work on a 5.3+ kernel");
        assert_eq!(pidfd_state(&fd), PidfdState::Alive);

        child.kill().unwrap();
        child.wait().unwrap();
        // Reaped: the task is gone and the pid may now be handed to somebody
        // else. The pidfd still refers to the original and says so.
        assert_eq!(pidfd_state(&fd), PidfdState::Exited);
    }

    #[test]
    fn renicing_our_own_process_upward_works() {
        // Lowering priority needs no privilege, so this runs as any user and
        // exercises the whole path: pidfd, ownership, liveness, setpriority.
        let me = std::process::id();
        let uid = std::fs::metadata("/proc/self").unwrap().uid();
        let before = rustix::process::getpriority_process(None).unwrap();
        assert!(renice(me, before + 1, Some(uid)).unwrap());
        assert_eq!(
            rustix::process::getpriority_process(None).unwrap(),
            before + 1
        );
    }
}
