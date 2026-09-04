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

use crate::config::{GameProfile, Settings};
use crate::pyfmt::names;
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

/// A game the auto-detector found that has no profile yet.
///
/// Produced by the sweep, which reads `/proc/*/maps` and fdinfo and so stays
/// in Python; this side only decides what to do with the result.
#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
pub struct Candidate {
    pub exe: String,
    pub pid: i64,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub source: String,
}

/// A game started or stopped.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Event {
    /// The exe the daemon keys everything on - a profile's, or a candidate's.
    pub exe: String,
    /// The profile that matched, or `None` for an auto-detected game that has
    /// none yet.
    pub profile_exe: Option<String>,
    pub pid: Option<i64>,
    /// True for a launch, false for an exit.
    pub running: bool,
    pub auto: bool,
}

/// What one poll saw, and what it now believes is running.
///
/// `running` is an ordered list rather than a map because the ORDER is
/// observable: Python keeps it in a dict, exit events come out in the order
/// games were first seen, and a caller reverting tweaks in a different order
/// is a different program.
#[derive(Debug, Clone, PartialEq)]
pub struct Poll {
    pub events: Vec<Event>,
    pub running: Vec<(String, i64)>,
}

/// One tick of the observer: what changed since the last one.
///
/// Pure. The process table and the auto-detect sweep are both handed in, so
/// the decision can be diffed against the Python without a machine.
///
/// The master switch has a subtlety worth stating. When it is off AND nothing
/// is running, this returns immediately - there is nothing to do and no reason
/// to walk the table. When it is off and something IS running, it does NOT
/// return early: `enabled_profiles` is empty with the master off, so nothing
/// is found, and everything currently running gets an exit event. That is the
/// path that reverts a game's tweaks when the user turns the tool off
/// mid-session, and short-circuiting it would leave them applied.
pub fn poll_once(
    settings: &Settings,
    procs: &[Process],
    candidates: &[Candidate],
    running: &[(String, i64)],
) -> Poll {
    let master = crate::config::truthy(&settings.master_enabled);
    // The Python returns here to avoid walking the process table at all. With
    // the table already in hand this is only a shortcut - nothing is enabled
    // with the master off, so the work below would reach the same answer - but
    // it is kept because the caller uses it to skip the scan.
    if !master && running.is_empty() {
        return Poll {
            events: Vec::new(),
            running: running.to_vec(),
        };
    }

    // exe -> (pid, candidate), in the order found. Insertion order decides the
    // order launch events come out in, so it is a list.
    let mut found: Vec<(String, i64, Option<Candidate>)> = Vec::new();
    for profile in settings.enabled_profiles() {
        if let Some(pid) = pick_pid(profile, procs) {
            found.push((profile.exe.clone(), pid, None));
        }
    }

    // The sweep is the expensive half, and it is skipped entirely while a
    // profiled game is already matched: a SECOND concurrent game is rare and
    // not worth the per-process /proc reads on every poll during play.
    if crate::config::truthy(&settings.auto_detect) && master && found.is_empty() {
        let ignored: Vec<String> = names(&settings.ignored_games)
            .into_iter()
            .map(|name| name.to_lowercase())
            .collect();
        for candidate in candidates {
            if found.iter().any(|(exe, _, _)| exe == &candidate.exe)
                || ignored.contains(&candidate.exe.to_lowercase())
                // A disabled profile already exists for it, which is the
                // user having said no. Respect that rather than re-offering.
                || settings.profile_for_exe(&candidate.exe).is_some()
            {
                continue;
            }
            found.push((
                candidate.exe.clone(),
                candidate.pid,
                Some(candidate.clone()),
            ));
        }
    }

    let mut events = Vec::new();
    let mut next: Vec<(String, i64)> = running.to_vec();
    for (exe, pid, candidate) in &found {
        match next.iter_mut().find(|(seen, _)| seen == exe) {
            // Already running: just follow the pid, which can change when a
            // launcher hands off to the real game process.
            Some(entry) => entry.1 = *pid,
            None => {
                next.push((exe.clone(), *pid));
                events.push(Event {
                    exe: exe.clone(),
                    profile_exe: candidate.is_none().then(|| exe.clone()),
                    pid: Some(*pid),
                    running: true,
                    auto: candidate.is_some(),
                });
            }
        }
    }

    // Exits, in the order the games were first seen.
    let gone: Vec<String> = next
        .iter()
        .filter(|(exe, _)| !found.iter().any(|(f, _, _)| f == exe))
        .map(|(exe, _)| exe.clone())
        .collect();
    for exe in gone {
        next.retain(|(seen, _)| seen != &exe);
        events.push(Event {
            // An exit carries the profile if there still is one - the daemon
            // needs it to know what to revert - and none if the game was
            // auto-detected and never given one.
            profile_exe: settings.profile_for_exe(&exe).map(|p| p.exe.clone()),
            exe,
            pid: None,
            running: false,
            auto: false,
        });
    }

    Poll {
        events,
        running: next,
    }
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
    use serde_json::json;

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

    fn settings_with(profiles: serde_json::Value, extra: serde_json::Value) -> Settings {
        let mut raw = serde_json::json!({"profiles": profiles});
        for (key, value) in extra.as_object().unwrap() {
            raw[key] = value.clone();
        }
        crate::config::from_value(&raw)
    }

    fn running_proc(pid: i64, name: &str) -> Process {
        proc(pid, name, 1000)
    }

    #[test]
    fn a_game_starting_is_one_launch_event() {
        let s = settings_with(json!([{"exe": "Wow.exe"}]), json!({}));
        let poll = poll_once(&s, &[running_proc(10, "Wow.exe")], &[], &[]);
        assert_eq!(poll.running, vec![("Wow.exe".to_string(), 10)]);
        assert_eq!(poll.events.len(), 1);
        assert!(poll.events[0].running && !poll.events[0].auto);
        assert_eq!(poll.events[0].profile_exe.as_deref(), Some("Wow.exe"));
    }

    #[test]
    fn a_game_still_running_is_no_event_at_all() {
        let s = settings_with(json!([{"exe": "Wow.exe"}]), json!({}));
        let was = vec![("Wow.exe".to_string(), 10)];
        let poll = poll_once(&s, &[running_proc(10, "Wow.exe")], &[], &was);
        assert!(poll.events.is_empty());
        assert_eq!(poll.running, was);
    }

    #[test]
    fn a_pid_that_moved_is_followed_without_an_event() {
        // A launcher handing off to the real game process does this, and
        // treating it as an exit-then-launch would revert and re-apply
        // everything mid-session.
        let s = settings_with(json!([{"exe": "Wow.exe"}]), json!({}));
        let was = vec![("Wow.exe".to_string(), 10)];
        let poll = poll_once(&s, &[running_proc(99, "Wow.exe")], &[], &was);
        assert!(poll.events.is_empty());
        assert_eq!(poll.running, vec![("Wow.exe".to_string(), 99)]);
    }

    #[test]
    fn a_game_stopping_is_one_exit_event_carrying_its_profile() {
        let s = settings_with(json!([{"exe": "Wow.exe"}]), json!({}));
        let was = vec![("Wow.exe".to_string(), 10)];
        let poll = poll_once(&s, &[], &[], &was);
        assert!(poll.running.is_empty());
        assert_eq!(poll.events.len(), 1);
        assert!(!poll.events[0].running);
        // The profile has to come with it: the daemon needs it to revert.
        assert_eq!(poll.events[0].profile_exe.as_deref(), Some("Wow.exe"));
    }

    #[test]
    fn turning_the_master_switch_off_mid_session_still_reports_the_exit() {
        // The path that reverts a running game's tweaks when the user turns
        // the tool off. Short-circuiting on `!master` would leave them applied.
        let s = settings_with(
            json!([{"exe": "Wow.exe"}]),
            json!({"master_enabled": false}),
        );
        let was = vec![("Wow.exe".to_string(), 10)];
        let poll = poll_once(&s, &[running_proc(10, "Wow.exe")], &[], &was);
        assert_eq!(poll.events.len(), 1);
        assert!(!poll.events[0].running);
        assert!(poll.running.is_empty());
    }

    #[test]
    fn the_master_switch_off_with_nothing_running_does_nothing() {
        let s = settings_with(
            json!([{"exe": "Wow.exe"}]),
            json!({"master_enabled": false}),
        );
        let poll = poll_once(&s, &[running_proc(10, "Wow.exe")], &[], &[]);
        assert!(poll.events.is_empty());
        assert!(poll.running.is_empty());
    }

    #[test]
    fn exits_come_out_in_the_order_the_games_were_first_seen() {
        let s = settings_with(json!([{"exe": "a"}, {"exe": "b"}]), json!({}));
        let was = vec![("b".to_string(), 2), ("a".to_string(), 1)];
        let poll = poll_once(&s, &[], &[], &was);
        let order: Vec<&str> = poll.events.iter().map(|e| e.exe.as_str()).collect();
        assert_eq!(order, vec!["b", "a"], "not sorted - first-seen order");
    }

    #[test]
    fn an_auto_detected_game_is_a_launch_with_no_profile() {
        let s = settings_with(json!([]), json!({"auto_detect": true}));
        let candidate = Candidate {
            exe: "newgame.exe".into(),
            pid: 42,
            display_name: "New Game".into(),
            source: "steam".into(),
        };
        let poll = poll_once(&s, &[], &[candidate], &[]);
        assert_eq!(poll.events.len(), 1);
        assert!(poll.events[0].auto);
        assert_eq!(poll.events[0].profile_exe, None);
        assert_eq!(poll.events[0].pid, Some(42));
    }

    #[test]
    fn the_sweep_is_skipped_while_a_profiled_game_is_matched() {
        // The expensive half, and a second concurrent game is not worth the
        // per-process /proc reads on every poll during play.
        let s = settings_with(json!([{"exe": "Wow.exe"}]), json!({"auto_detect": true}));
        let candidate = Candidate {
            exe: "other.exe".into(),
            pid: 42,
            display_name: String::new(),
            source: String::new(),
        };
        let poll = poll_once(&s, &[running_proc(10, "Wow.exe")], &[candidate], &[]);
        assert_eq!(poll.events.len(), 1);
        assert_eq!(poll.events[0].exe, "Wow.exe");
    }

    #[test]
    fn an_ignored_game_is_not_detected_however_it_is_cased() {
        let s = settings_with(
            json!([]),
            json!({"auto_detect": true, "ignored_games": ["NewGame.EXE"]}),
        );
        let candidate = Candidate {
            exe: "newgame.exe".into(),
            pid: 42,
            display_name: String::new(),
            source: String::new(),
        };
        assert!(poll_once(&s, &[], &[candidate], &[]).events.is_empty());
    }

    #[test]
    fn a_game_with_a_disabled_profile_is_not_re_offered() {
        // A disabled profile is the user having said no already.
        let s = settings_with(
            json!([{"exe": "newgame.exe", "enabled": false}]),
            json!({"auto_detect": true}),
        );
        let candidate = Candidate {
            exe: "newgame.exe".into(),
            pid: 42,
            display_name: String::new(),
            source: String::new(),
        };
        assert!(poll_once(&s, &[], &[candidate], &[]).events.is_empty());
    }

    #[test]
    fn auto_detect_off_means_no_sweep() {
        let s = settings_with(json!([]), json!({"auto_detect": false}));
        let candidate = Candidate {
            exe: "newgame.exe".into(),
            pid: 42,
            display_name: String::new(),
            source: String::new(),
        };
        assert!(poll_once(&s, &[], &[candidate], &[]).events.is_empty());
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
