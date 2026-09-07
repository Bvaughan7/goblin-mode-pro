//! Pre-warming the shader cache Steam has already downloaded.
//!
//! Best-effort, and the reason it is worth porting at all is the messages: the
//! four outcomes are indistinguishable to the caller except by what they say,
//! because none of them is actionable. "Nothing to do" and "it failed" are the
//! same `false` with different words, and the words are what a person reads in
//! the log when a launch felt slow.
//!
//! Finding the binary and the archives stays with the caller; this is the
//! order the questions are asked in, and what each answer means.

/// What a pre-warm attempt comes to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Prewarm {
    /// This profile has no Steam AppID, so there is no cache to warm.
    NoAppId,
    /// Steam's replay tool is not installed and not on the path.
    NoReplayTool,
    /// Steam has not downloaded a cache for this game yet.
    NoArchives,
    /// Replay these.
    Run { argv: Vec<String>, archives: usize },
}

impl Prewarm {
    /// What the caller logs, and whether it counts as having done something.
    ///
    /// The message for a run is written by the caller afterwards, because it
    /// reports what happened rather than what was attempted.
    pub fn message(&self) -> Option<&'static str> {
        match self {
            Prewarm::NoAppId => Some("no Steam AppID on this profile"),
            Prewarm::NoReplayTool => Some("fossilize_replay not found (no Steam install detected)"),
            Prewarm::NoArchives => Some("no downloaded shader-cache archive for this AppID yet"),
            Prewarm::Run { .. } => None,
        }
    }
}

/// How many threads the replay is allowed.
///
/// Two, on purpose. This runs while a game is starting, and a replay that
/// takes every core makes the launch it is meant to smooth out worse.
pub const REPLAY_THREADS: &str = "2";

/// What to do about a profile's shader cache.
///
/// The order of the three refusals is the order the questions can be answered
/// in - no AppID needs nothing, no tool needs no filesystem, no archives needs
/// both - and each one is cheaper than the next.
pub fn prewarm(app_id: &str, replay_tool: Option<&str>, archives: &[String]) -> Prewarm {
    if app_id.is_empty() {
        return Prewarm::NoAppId;
    }
    let Some(tool) = replay_tool else {
        return Prewarm::NoReplayTool;
    };
    if archives.is_empty() {
        return Prewarm::NoArchives;
    }
    let mut argv = vec![
        tool.to_string(),
        "--num-threads".to_string(),
        REPLAY_THREADS.to_string(),
    ];
    argv.extend(archives.iter().cloned());
    Prewarm::Run {
        argv,
        archives: archives.len(),
    }
}

/// What a finished run is reported as.
pub fn ran_message(archives: usize) -> String {
    format!("replayed {archives} archive(s)")
}

/// What a run that exited badly is reported as.
pub fn failed_message(code: i32) -> String {
    format!("fossilize_replay exited {code}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn archives(n: usize) -> Vec<String> {
        (0..n).map(|i| format!("/cache/{i}.foz")).collect()
    }

    #[test]
    fn a_profile_with_no_app_id_has_no_cache_to_warm() {
        assert_eq!(
            prewarm("", Some("/usr/bin/fossilize_replay"), &archives(2)),
            Prewarm::NoAppId
        );
    }

    #[test]
    fn the_questions_are_asked_cheapest_first() {
        // No AppID is answered without looking for a tool, and no tool
        // without looking for archives.
        assert_eq!(prewarm("", None, &[]), Prewarm::NoAppId);
        assert_eq!(prewarm("1091500", None, &[]), Prewarm::NoReplayTool);
        assert_eq!(prewarm("1091500", Some("/x"), &[]), Prewarm::NoArchives);
    }

    #[test]
    fn every_archive_is_passed_after_the_thread_count() {
        let plan = prewarm("1091500", Some("/usr/bin/fossilize_replay"), &archives(3));
        assert_eq!(
            plan,
            Prewarm::Run {
                argv: vec![
                    "/usr/bin/fossilize_replay".into(),
                    "--num-threads".into(),
                    "2".into(),
                    "/cache/0.foz".into(),
                    "/cache/1.foz".into(),
                    "/cache/2.foz".into(),
                ],
                archives: 3,
            }
        );
    }

    #[test]
    fn a_replay_does_not_take_the_machine_the_game_is_starting_on() {
        assert_eq!(REPLAY_THREADS, "2");
    }

    #[test]
    fn each_refusal_says_which_one_it_was() {
        // The caller cannot act on any of them; the words are the only thing
        // that tells them apart in a log.
        assert_eq!(
            prewarm("", None, &[]).message(),
            Some("no Steam AppID on this profile")
        );
        assert_eq!(
            prewarm("1", None, &[]).message(),
            Some("fossilize_replay not found (no Steam install detected)")
        );
        assert_eq!(
            prewarm("1", Some("/x"), &[]).message(),
            Some("no downloaded shader-cache archive for this AppID yet")
        );
        assert_eq!(prewarm("1", Some("/x"), &archives(1)).message(), None);
    }

    #[test]
    fn a_finished_run_says_how_much_it_replayed() {
        assert_eq!(ran_message(1), "replayed 1 archive(s)");
        assert_eq!(ran_message(12), "replayed 12 archive(s)");
        assert_eq!(failed_message(1), "fossilize_replay exited 1");
        assert_eq!(failed_message(-11), "fossilize_replay exited -11");
    }
}
