//! Which running process is the game.
//!
//! The observer's poll loop is plumbing - it reads `/proc` through psutil and
//! stays in Python. What moves here is the judgement it applies to what it
//! finds: whether a process belongs to a profile, and which of several
//! matching processes is the one to actually tune.
//!
//! This asks a similar question to [`crate::runner`] and answers it
//! differently, on purpose. The runner sees an argv it is about to execute;
//! the observer sees a process that already exists, with a `comm` the kernel
//! truncated to 15 characters and a wrapper tree around it. Two of the three
//! match modes therefore behave differently between the two, and the parity
//! corpus pins each difference rather than treating either as the canonical
//! one.

use std::collections::BTreeSet;

use crate::config::GameProfile;
use crate::runner::basename;

/// Longest string a user regex is run against.
///
/// A backtracking guard: Python's `re` has no timeout and the poll runs on the
/// daemon's main loop, so an unbounded haystack is a way to freeze the UI with
/// a pattern typed into a text box.
pub const MAX_HAYSTACK: usize = 4096;

/// Wine/Proton wrapper processes never treated as "the game" when hunting for
/// the PID to renice.
pub const WINE_INFRA: &[&str] = &[
    "wine",
    "wine64",
    "wineserver",
    "wine-preloader",
    "wine64-preloader",
    "start.exe",
    "services.exe",
    "explorer.exe",
    "rpcss.exe",
    "plugplay.exe",
    "winedevice.exe",
    "conhost.exe",
    "svchost.exe",
    "tabtip.exe",
    "steam.exe",
    "steamwebhelper.exe",
    "gameoverlayui.exe",
    "proton",
    "python3",
    "pv-bwrap",
    "srt-bwrap",
    "reaper",
];

/// One process as the poll loop saw it.
///
/// `rss` is carried rather than looked up because reading it can fail for a
/// process that exits mid-poll, and the Python treats that as zero rather than
/// as a reason to drop the candidate. Where the number comes from is the
/// caller's problem; what it decides is this module's.
#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
pub struct Process {
    pub pid: i64,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub exe: String,
    #[serde(default)]
    pub cmdline: Vec<String>,
    #[serde(default)]
    pub rss: i64,
}

/// Every name a process could be known by: its `comm`, the basename of its
/// executable, and the basename of each command-line token.
///
/// `name` goes in as it stands. It is already a bare `comm` rather than a
/// path, and running it through the basename split would corrupt a process
/// that legitimately has a slash or a backslash in its name.
pub fn candidate_names(name: &str, exe: &str, cmdline: &[String]) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    if !name.is_empty() {
        out.insert(name.to_string());
    }
    if !exe.is_empty() {
        out.insert(basename(exe));
    }
    for token in cmdline {
        out.insert(basename(token));
    }
    out.remove("");
    out
}

/// Does this process belong to this profile?
pub fn matches(profile: &GameProfile, name: &str, exe: &str, cmdline: &[String]) -> bool {
    let target = &profile.exe;

    match profile.match_mode.as_str().unwrap_or("") {
        "exact" => {
            let target_lower = target.to_lowercase();
            candidate_names(name, exe, cmdline).iter().any(|candidate| {
                let lower = candidate.to_lowercase();
                // A Windows exe is case-insensitive, and `comm` is capped at
                // 15 characters - so a long name arrives truncated and can
                // only ever be a prefix of what the profile stores.
                lower == target_lower
                    || (candidate.chars().count() >= 15 && target_lower.starts_with(&lower))
            })
        }
        "substring" => {
            // Lowercased before truncating, which is the order the Python
            // uses and is observable: case folding can change a string's
            // length, so folding after the cut would keep a different amount.
            let haystack: String = haystack(name, exe, cmdline)
                .to_lowercase()
                .chars()
                .take(MAX_HAYSTACK)
                .collect();
            haystack.contains(&target.to_lowercase())
        }
        "regex" => {
            // Bounded before compiling - it is user input and this is the
            // ReDoS guard. A pattern that will not compile skips the profile,
            // which is also what happens to a pattern Rust's `regex` refuses
            // but Python's `re` accepts; see the runner for why that is the
            // safe direction.
            //
            // Raising this bound is unobservable rather than untested:
            // `sanitize_exe` already caps `exe` at 128 characters, so no
            // stored pattern can reach the cut. Lowering it is caught.
            let source: String = target.chars().take(128).collect();
            let Ok(pattern) = regex::Regex::new(&source) else {
                return false;
            };
            let haystack: String = haystack(name, exe, cmdline)
                .chars()
                .take(MAX_HAYSTACK)
                .collect();
            pattern.is_match(&haystack)
        }
        _ => false,
    }
}

/// The one string substring and regex matching are run against.
///
/// Both separators are unconditional, so an empty `exe` leaves a double space
/// rather than closing up. That is load-bearing for a substring profile whose
/// text spans the join.
fn haystack(name: &str, exe: &str, cmdline: &[String]) -> String {
    format!("{name} {exe} {}", cmdline.join(" "))
}

/// The PID to optimise: the real game process, not a wrapper around it.
pub fn pick_pid(profile: &GameProfile, procs: &[Process]) -> Option<i64> {
    let mut candidates: Vec<&Process> = procs
        .iter()
        .filter(|p| matches(profile, &p.name, &p.exe, &p.cmdline))
        // Only the process `comm` disqualifies a match - not `/proc/*/exe`,
        // which for a Wine or Proton game points at the loader itself, so
        // filtering on it would reject every Windows game there is.
        .filter(|p| !WINE_INFRA.contains(&p.name.to_lowercase().as_str()))
        .collect();

    // Prefer the fattest matching process: the game, not its launcher. The
    // sort is stable in both languages, so processes of equal size stay in
    // the order the scan produced them and the answer does not depend on
    // which of two identical candidates the sort happened to look at first.
    candidates.sort_by_key(|p| std::cmp::Reverse(p.rss));
    candidates.first().map(|p| p.pid)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::from_value;

    fn profile(exe: &str, mode: &str) -> GameProfile {
        let settings = from_value(&serde_json::json!({
            "profiles": [{"exe": exe, "match_mode": mode}]
        }));
        settings.profiles.into_iter().next().expect("one profile")
    }

    fn cmd(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|s| s.to_string()).collect()
    }

    fn proc(pid: i64, name: &str, rss: i64) -> Process {
        Process {
            pid,
            name: name.to_string(),
            exe: String::new(),
            cmdline: Vec::new(),
            rss,
        }
    }

    #[test]
    fn a_comm_matches_the_profile_it_names() {
        let p = profile("Wow.exe", "exact");
        assert!(matches(&p, "Wow.exe", "", &[]));
        assert!(matches(&p, "WOW.EXE", "", &[]));
        assert!(!matches(&p, "NotWow.exe", "", &[]));
    }

    #[test]
    fn an_exe_path_matches_on_its_basename() {
        let p = profile("Wow.exe", "exact");
        assert!(matches(&p, "wine", "C:\\Games\\Wow.exe", &[]));
        assert!(matches(&p, "wine", "/games/wow/Wow.exe", &[]));
    }

    #[test]
    fn a_truncated_comm_matches_the_full_name_it_was_cut_from() {
        // The kernel caps `comm` at 15 characters, so a game with a longer
        // executable name never presents its whole name to the scan. Fifteen
        // is exactly the floor: one character less and the prefix rule is off.
        let p = profile("VeryLongGameName.exe", "exact");
        assert_eq!("VeryLongGameNam".chars().count(), 15);
        assert!(matches(&p, "VeryLongGameNam", "", &[]));
        assert!(!matches(&p, "VeryLongGameNa", "", &[]));
    }

    #[test]
    fn a_short_prefix_is_not_a_truncated_comm() {
        // Without the length floor "W" would match "Wow.exe", and every
        // profile would claim half the process table.
        let p = profile("Wow.exe", "exact");
        assert!(!matches(&p, "W", "", &[]));
        assert!(!matches(&p, "Wow", "", &[]));
    }

    #[test]
    fn the_fifteen_character_floor_counts_characters_not_bytes() {
        // Fifteen multi-byte characters are one character short of the floor
        // by Python's count and well past it by a byte count, so a byte-based
        // translation matches here and Python does not.
        let name = "ゲームゲームゲームゲームゲー"; // 14 characters, 42 bytes
        assert_eq!(name.chars().count(), 14);
        assert!(name.len() > 15);
        let p = profile("ゲームゲームゲームゲームゲームX", "exact");
        assert!(!matches(&p, name, "", &[]));
    }

    #[test]
    fn substring_looks_at_the_whole_command_line() {
        let p = profile("rs2client", "substring");
        assert!(matches(
            &p,
            "sh",
            "/bin/sh",
            &cmd(["-c", "/opt/rs2client"].as_ref())
        ));
        assert!(!matches(
            &p,
            "sh",
            "/bin/sh",
            &cmd(["-c", "/opt/other"].as_ref())
        ));
    }

    #[test]
    fn substring_spans_the_join_between_fields() {
        // The haystack is "name exe cmdline", and a profile can be written to
        // sit across one of those spaces.
        let p = profile("wow.exe wine", "substring");
        assert!(matches(&p, "Wow.exe", "wine", &[]));
    }

    #[test]
    fn an_empty_field_still_contributes_its_separator() {
        // Both separators go in unconditionally, so a process with no
        // resolvable exe leaves two spaces rather than closing the gap. A
        // substring profile written against the real haystack depends on it.
        let p = profile("wow.exe  -dx12", "substring");
        assert!(matches(&p, "Wow.exe", "", &cmd(["-dx12"].as_ref())));
        let single = profile("wow.exe -dx12", "substring");
        assert!(!matches(&single, "Wow.exe", "", &cmd(["-dx12"].as_ref())));
    }

    #[test]
    fn regex_searches_the_haystack_case_sensitively() {
        let p = profile("[Ww]ow[0-9]*", "regex");
        assert!(matches(&p, "wow64", "", &[]));
        let anchored = profile("^Wow", "regex");
        assert!(anchored.match_mode.as_str() == Some("regex"));
        assert!(matches(&anchored, "Wow.exe", "", &[]));
        assert!(!matches(&anchored, "xWow.exe", "", &[]));
    }

    #[test]
    fn a_pattern_that_will_not_compile_matches_nothing() {
        let p = profile("*[bad", "regex");
        assert!(!matches(&p, "anything", "", &[]));
    }

    #[test]
    fn an_unknown_match_mode_never_reaches_the_matcher() {
        // The config layer normalises anything outside the three modes back to
        // "exact", so the fallthrough arm below only ever sees a profile built
        // some other way. Pinned here so a change to that normalisation shows
        // up as a failure rather than as silently unmatched games.
        let p = profile("Wow.exe", "fuzzy");
        assert_eq!(p.match_mode.as_str(), Some("exact"));
        assert!(matches(&p, "Wow.exe", "", &[]));

        let mut raw = p.clone();
        raw.match_mode = serde_json::Value::String("fuzzy".into());
        assert!(!matches(&raw, "Wow.exe", "", &[]));
        raw.match_mode = serde_json::Value::Null;
        assert!(!matches(&raw, "Wow.exe", "", &[]));
    }

    #[test]
    fn the_fattest_matching_process_wins() {
        let p = profile("Wow.exe", "exact");
        let procs = vec![
            proc(10, "Wow.exe", 1_000),
            proc(11, "Wow.exe", 900_000),
            proc(12, "Wow.exe", 5_000),
        ];
        assert_eq!(pick_pid(&p, &procs), Some(11));
    }

    #[test]
    fn a_tie_keeps_the_order_the_scan_produced() {
        // Stability is the difference between a stable answer and one that
        // moves between polls for no reason the user can see.
        let p = profile("Wow.exe", "exact");
        let procs = vec![proc(10, "Wow.exe", 4_096), proc(11, "Wow.exe", 4_096)];
        assert_eq!(pick_pid(&p, &procs), Some(10));
    }

    #[test]
    fn a_wine_wrapper_never_wins_however_fat_it_is() {
        let p = profile("Wow.exe", "substring");
        let procs = vec![
            Process {
                pid: 10,
                name: "explorer.exe".into(),
                exe: String::new(),
                cmdline: cmd(["Wow.exe"].as_ref()),
                rss: 900_000,
            },
            Process {
                pid: 11,
                name: "Wow.exe".into(),
                exe: String::new(),
                cmdline: cmd(["Wow.exe"].as_ref()),
                rss: 1_000,
            },
        ];
        assert_eq!(pick_pid(&p, &procs), Some(11));
    }

    #[test]
    fn the_wine_blocklist_is_matched_case_insensitively() {
        let p = profile("Wow.exe", "substring");
        let procs = vec![Process {
            pid: 10,
            name: "Explorer.EXE".into(),
            exe: String::new(),
            cmdline: cmd(["Wow.exe"].as_ref()),
            rss: 900_000,
        }];
        assert_eq!(pick_pid(&p, &procs), None);
    }

    #[test]
    fn nothing_matching_yields_no_pid() {
        let p = profile("Wow.exe", "exact");
        assert_eq!(pick_pid(&p, &[proc(10, "firefox", 900_000)]), None);
    }

    #[test]
    fn an_empty_name_is_not_a_candidate() {
        assert!(candidate_names("", "", &[]).is_empty());
        assert!(candidate_names("", "", &cmd(["", "  "].as_ref())).is_empty());
    }

    #[test]
    fn the_comm_is_not_run_through_the_basename_split() {
        // A `comm` containing a separator is a name, not a path. Splitting it
        // would let a profile for "exe" claim a process called "a/exe".
        let names = candidate_names("weird/name", "", &[]);
        assert!(names.contains("weird/name"));
        assert!(!names.contains("name"));
    }
}
