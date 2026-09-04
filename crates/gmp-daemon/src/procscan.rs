//! Reading the process table the way psutil reads it.
//!
//! [`gmp_core::observer`] decides which process is the game; this produces the
//! list it decides over. The two are separate because the deciding is
//! diffable against the Python from fixtures and this is not - it can only be
//! checked against a real `/proc`.
//!
//! The one thing here that is not obvious is `name`, and getting it wrong is
//! not a cosmetic problem.
//!
//! `/proc/<pid>/comm` is truncated to 15 characters by the kernel. psutil does
//! NOT hand that straight back: when the truncated name is 15 characters and
//! the basename of `cmdline[0]` starts with it, psutil returns the longer
//! basename instead, because it is more explicative. Every comparison the
//! observer makes against `name` therefore sees the FULL name on the Python
//! side.
//!
//! Three entries on the observer's Wine/Steam blocklist are longer than 15
//! characters - `gameoverlayui.exe`, `steamwebhelper.exe` and
//! `wine64-preloader` - so a scanner that returned the raw `comm` would never
//! match them. They would stop being filtered, and since the observer picks
//! the FATTEST matching process, `steamwebhelper.exe` would beat the game it
//! is drawing an overlay on and take the renice.

use gmp_core::observer::Process;

/// The kernel's `comm` length. A name this long is a name that may have been
/// cut, which is the only case psutil tries to extend.
const COMM_LIMIT: usize = 15;

/// Every process on the machine, as the observer wants to see them.
///
/// A process that disappears mid-scan is skipped rather than reported as an
/// error: the table is a snapshot of something that is moving, and psutil's
/// `process_iter` does the same.
pub fn scan() -> Vec<Process> {
    scan_root(std::path::Path::new("/proc"))
}

/// `scan`, against a `/proc` that need not be this machine's - which is what
/// makes any of this testable.
pub fn scan_root(root: &std::path::Path) -> Vec<Process> {
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for entry in entries.flatten() {
        let Ok(pid) = entry.file_name().to_string_lossy().parse::<i64>() else {
            continue; // /proc holds plenty that is not a process
        };
        if let Some(process) = read_process(&entry.path(), pid) {
            out.push(process);
        }
    }
    // Ascending pid, so a scan is stable between polls and a tie in resident
    // size resolves the same way twice running.
    out.sort_by_key(|process| process.pid);
    out
}

fn read_process(dir: &std::path::Path, pid: i64) -> Option<Process> {
    // No comm means no process any more, which is the ordinary case.
    let comm = std::fs::read_to_string(dir.join("comm")).ok()?;
    let comm = comm.trim_end_matches('\n').to_string();
    let cmdline = read_cmdline(dir);
    Some(Process {
        pid,
        name: extend_name(comm, &cmdline),
        // `/proc/<pid>/exe` is a symlink that a process owned by another user
        // will not let this daemon read. Unreadable is empty, not an error:
        // psutil raises AccessDenied there and the observer catches it.
        exe: std::fs::read_link(dir.join("exe"))
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default(),
        rss: read_rss(dir),
        cmdline,
    })
}

/// psutil's rule, and the reason this module exists.
///
/// Only a name at the truncation limit is a candidate - a shorter one was not
/// cut, so there is nothing to restore - and the extension has to actually
/// start with what the kernel reported, or it belongs to something else.
fn extend_name(comm: String, cmdline: &[String]) -> String {
    if comm.len() < COMM_LIMIT {
        return comm;
    }
    let Some(first) = cmdline.first() else {
        return comm;
    };
    let extended = first.rsplit('/').next().unwrap_or(first);
    if extended.starts_with(&comm) {
        extended.to_string()
    } else {
        comm
    }
}

/// The command line, as psutil parses it - which is not simply "split on NUL".
///
/// `man proc` says arguments are NUL-separated with a trailing NUL, and most
/// processes comply. Ones that rewrite their title with `setproctitle` do not,
/// and psutil carries two rules for them that the observer's matching depends
/// on, because `cmdline` is part of the haystack:
///
/// 1. The separator is NUL only when the data ENDS with a NUL; otherwise it is
///    a space. A process that rewrote its title without terminating it would
///    otherwise arrive as one long argument.
/// 2. Even when it does end with a NUL, a result of exactly ONE argument that
///    contains a space is re-split on spaces. `systemd-userwork: waiting...`
///    on this machine is exactly that shape, and it is why psutil calls it
///    `systemd-userwork:` where a naive reader calls it the whole sentence.
///
/// Exactly ONE trailing separator is dropped, not every empty argument: two
/// trailing NULs really do mean a final empty argument, and psutil keeps it.
fn read_cmdline(dir: &std::path::Path) -> Vec<String> {
    let Ok(raw) = std::fs::read(dir.join("cmdline")) else {
        return Vec::new();
    };
    // psutil decodes with `surrogateescape` where this uses replacement
    // characters. They differ only for a command line that is not valid UTF-8,
    // which nothing this tool matches on has ever been.
    let data = String::from_utf8_lossy(&raw);
    if data.is_empty() {
        return Vec::new(); // a zombie, usually
    }
    let separator = if data.ends_with('\0') { '\0' } else { ' ' };
    let trimmed = data.strip_suffix(separator).unwrap_or(&data);
    let mut parts: Vec<String> = trimmed.split(separator).map(str::to_string).collect();
    if separator == '\0' && parts.len() == 1 && data.contains(' ') {
        parts = trimmed.split(' ').map(str::to_string).collect();
    }
    parts
}

/// Resident set size in bytes, from `statm`.
///
/// Field two, in pages. Zero when it cannot be read, which is what the
/// observer already does with a process whose memory it cannot see - such a
/// process simply never wins the fattest-process contest.
fn read_rss(dir: &std::path::Path) -> i64 {
    let Ok(statm) = std::fs::read_to_string(dir.join("statm")) else {
        return 0;
    };
    let Some(pages) = statm.split_whitespace().nth(1) else {
        return 0;
    };
    pages.parse::<i64>().unwrap_or(0) * page_size()
}

fn page_size() -> i64 {
    // 4 KiB everywhere this ships. Read once rather than assumed would need
    // libc; the observer only ever COMPARES these numbers, so a wrong scale
    // would not change which process is fattest.
    4096
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake_proc(entries: &[(&str, &str, &[&str], &str)]) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "gmp-proc-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        for (pid, comm, cmdline, statm) in entries {
            let dir = root.join(pid);
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(dir.join("comm"), format!("{comm}\n")).unwrap();
            let mut raw = String::new();
            for arg in *cmdline {
                raw.push_str(arg);
                raw.push('\0');
            }
            std::fs::write(dir.join("cmdline"), raw).unwrap();
            std::fs::write(dir.join("statm"), statm).unwrap();
        }
        // Things in /proc that are not processes.
        std::fs::create_dir_all(root.join("sys")).unwrap();
        std::fs::write(root.join("uptime"), "1 1\n").unwrap();
        root
    }

    #[test]
    fn a_truncated_comm_is_extended_from_the_command_line() {
        // The rule psutil applies, and the reason the blocklist works.
        assert_eq!(
            extend_name("gameoverlayui.e".into(), &["/x/gameoverlayui.exe".into()]),
            "gameoverlayui.exe"
        );
        assert_eq!(
            extend_name("steamwebhelper.".into(), &["/x/steamwebhelper.exe".into()]),
            "steamwebhelper.exe"
        );
        assert_eq!(
            extend_name("wine64-preloade".into(), &["/x/wine64-preloader".into()]),
            "wine64-preloader"
        );
    }

    #[test]
    fn a_short_name_is_never_extended() {
        // It was not truncated, so there is nothing to restore - and
        // extending it would let any process rename itself by its argv.
        assert_eq!(
            extend_name("wine".into(), &["/x/wineserver".into()]),
            "wine"
        );
    }

    #[test]
    fn an_extension_that_does_not_continue_the_name_is_refused() {
        assert_eq!(
            extend_name(
                "some-long-name-".into(),
                &["/x/completely-different".into()]
            ),
            "some-long-name-"
        );
    }

    #[test]
    fn a_process_with_no_command_line_keeps_its_comm() {
        // Kernel threads have none, and so do some zombies.
        assert_eq!(
            extend_name("kworker/u32:1-e".into(), &[]),
            "kworker/u32:1-e"
        );
    }

    #[test]
    fn the_trailing_nul_does_not_become_an_empty_argument() {
        let root = fake_proc(&[("42", "sh", &["/bin/sh", "-c", "true"], "0 0 0")]);
        let procs = scan_root(&root);
        assert_eq!(procs[0].cmdline, vec!["/bin/sh", "-c", "true"]);
        std::fs::remove_dir_all(&root).ok();
    }

    /// Write a raw `cmdline` rather than a well-formed one.
    fn proc_with_raw_cmdline(raw: &[u8]) -> std::path::PathBuf {
        let root = fake_proc(&[("42", "systemd-userwo", &[], "0 0")]);
        std::fs::write(root.join("42").join("cmdline"), raw).unwrap();
        root
    }

    #[test]
    fn a_setproctitle_process_is_split_on_spaces() {
        // The shape this machine really produces: one NUL-terminated argument
        // containing spaces. psutil re-splits it, and the observer's matching
        // sees the pieces.
        let root = proc_with_raw_cmdline(b"systemd-userwork: waiting...\0");
        assert_eq!(
            scan_root(&root)[0].cmdline,
            vec!["systemd-userwork:", "waiting..."]
        );
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_command_line_with_no_trailing_nul_is_split_on_spaces() {
        let root = proc_with_raw_cmdline(b"some title here");
        assert_eq!(scan_root(&root)[0].cmdline, vec!["some", "title", "here"]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_single_argument_with_no_spaces_is_left_alone() {
        // The re-split only applies when there is a space to split on.
        let root = proc_with_raw_cmdline(b"/usr/bin/somedaemon\0");
        assert_eq!(scan_root(&root)[0].cmdline, vec!["/usr/bin/somedaemon"]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn only_one_trailing_separator_is_dropped() {
        // Two trailing NULs really do mean a final empty argument, and psutil
        // keeps it rather than tidying it away.
        let root = proc_with_raw_cmdline(b"/bin/sh\0-c\0\0");
        assert_eq!(scan_root(&root)[0].cmdline, vec!["/bin/sh", "-c", ""]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn an_empty_command_line_is_no_arguments() {
        let root = proc_with_raw_cmdline(b"");
        assert!(scan_root(&root)[0].cmdline.is_empty());
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn entries_in_proc_that_are_not_processes_are_skipped() {
        let root = fake_proc(&[("42", "sh", &["/bin/sh"], "0 0 0")]);
        let procs = scan_root(&root);
        assert_eq!(procs.len(), 1);
        assert_eq!(procs[0].pid, 42);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn resident_size_is_the_second_statm_field_in_bytes() {
        let root = fake_proc(&[("42", "sh", &["/bin/sh"], "1000 250 3 4 5 6 7")]);
        assert_eq!(scan_root(&root)[0].rss, 250 * 4096);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn an_unreadable_statm_is_zero_rather_than_a_missing_process() {
        let root = fake_proc(&[("42", "sh", &["/bin/sh"], "")]);
        let procs = scan_root(&root);
        assert_eq!(procs.len(), 1, "the process is still reported");
        assert_eq!(procs[0].rss, 0);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn the_scan_is_ordered_by_pid() {
        let root = fake_proc(&[
            ("300", "c", &["/c"], "0 0"),
            ("42", "a", &["/a"], "0 0"),
            ("101", "b", &["/b"], "0 0"),
        ]);
        let pids: Vec<i64> = scan_root(&root).iter().map(|p| p.pid).collect();
        assert_eq!(pids, vec![42, 101, 300]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_missing_proc_is_an_empty_table_rather_than_a_panic() {
        assert!(scan_root(std::path::Path::new("/nonexistent/proc")).is_empty());
    }
}
