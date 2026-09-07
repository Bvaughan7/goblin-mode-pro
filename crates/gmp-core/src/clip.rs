//! The replay buffer: thirty seconds of video kept in memory, flushed to a
//! file when something goes wrong.
//!
//! The process handling and the waiting stay with the caller. What is here is
//! the command line, the two refusals, and which file a flush produced -
//! small, and all three are things that fail silently if they drift. A wrong
//! flag makes a recorder that starts and records nothing; a missing debounce
//! makes one clip per frame of a stuttering game.

/// The recorder this drives.
pub const TOOL: &str = "gpu-screen-recorder";

/// How much is kept in the ring buffer.
pub const REPLAY_SECONDS: u32 = 30;

/// The shortest gap between two saves.
///
/// A dip that lasts three seconds raises an incident per sample, and every one
/// of them asks for a clip of the same thirty seconds. Twenty is long enough
/// that the second clip would be of something else.
pub const SAVE_DEBOUNCE_SECONDS: f64 = 20.0;

/// The command that starts the buffer.
///
/// Written out rather than assembled from options, because every one of these
/// is load-bearing and none of them fails loudly: `-r` is what makes it a
/// replay buffer rather than a recording, and `-ro` is where a flush lands.
/// A recorder started without them runs happily and produces nothing.
pub fn start_argv(out_dir: &str) -> Vec<String> {
    [
        TOOL,
        "-w",
        "screen",
        "-f",
        "60",
        "-c",
        "mp4",
        "-r",
        &REPLAY_SECONDS.to_string(),
        "-ro",
        out_dir,
    ]
    .iter()
    .map(|a| (*a).to_string())
    .collect()
}

/// Why a save was refused, or that it was not.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Save {
    /// Nothing is recording.
    NotRunning,
    /// One landed too recently.
    TooSoon,
    /// Signal the recorder.
    Flush,
}

/// Whether to ask the recorder for a clip.
///
/// `last_save` is when the previous one was asked for, on a monotonic clock,
/// and `None` means never. Never is not "a long time ago" here only because
/// the reference point of a monotonic clock is not guaranteed to be far from
/// zero - a zero sentinel would refuse the first clip on a machine that had
/// just booted.
pub fn may_save(running: bool, last_save: Option<f64>, now: f64) -> Save {
    if !running {
        return Save::NotRunning;
    }
    match last_save {
        Some(last) if now - last < SAVE_DEBOUNCE_SECONDS => Save::TooSoon,
        _ => Save::Flush,
    }
}

/// The file a flush produced, given what was in the directory before and
/// after.
///
/// The recorder writes asynchronously, so the caller watches the directory
/// rather than being told. If more than one file appeared - another recorder,
/// or a save that landed while this one was waiting - the NEWEST wins, because
/// that is the one this flush asked for.
pub fn saved_file<'a>(before: &[&str], after: &[(&'a str, f64)]) -> Option<&'a str> {
    let mut fresh: Vec<&(&str, f64)> = after
        .iter()
        .filter(|(name, _)| !before.contains(name))
        .collect();
    // Stable and ascending, then take the last. For equal timestamps the
    // Python has NO defined answer: it sorts a SET, whose iteration order
    // depends on string hashing, which is randomised per process - the same
    // two files pick differently between runs. This picks the later of the
    // two in listing order, deterministically, because an arbitrary answer is
    // worth having consistently.
    fresh.sort_by(|a, b| a.1.total_cmp(&b.1));
    fresh.last().map(|(name, _)| *name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_command_asks_for_a_replay_buffer_and_says_where_to_put_it() {
        let argv = start_argv("/home/x/Videos/Goblin Mode Pro");
        assert_eq!(argv[0], TOOL);
        let flag = |name: &str| {
            argv.iter()
                .position(|a| a == name)
                .map(|i| argv[i + 1].clone())
        };
        assert_eq!(flag("-r").as_deref(), Some("30"), "without -r it records");
        assert_eq!(
            flag("-ro").as_deref(),
            Some("/home/x/Videos/Goblin Mode Pro"),
            "and without -ro a flush lands nowhere"
        );
        assert_eq!(flag("-w").as_deref(), Some("screen"));
        assert_eq!(flag("-c").as_deref(), Some("mp4"));
        assert_eq!(flag("-f").as_deref(), Some("60"));
    }

    #[test]
    fn a_directory_with_spaces_stays_one_argument() {
        let argv = start_argv("/home/x/Videos/Goblin Mode Pro");
        assert!(argv.contains(&"/home/x/Videos/Goblin Mode Pro".to_string()));
    }

    #[test]
    fn nothing_recording_means_nothing_to_save() {
        assert_eq!(may_save(false, None, 100.0), Save::NotRunning);
        assert_eq!(may_save(false, Some(0.0), 100.0), Save::NotRunning);
    }

    #[test]
    fn the_first_clip_is_never_too_soon() {
        // Even at a monotonic zero, which is why "never" is not spelled as a
        // timestamp of zero.
        assert_eq!(may_save(true, None, 0.0), Save::Flush);
        assert_eq!(may_save(true, None, 1.0), Save::Flush);
    }

    #[test]
    fn a_second_clip_waits_for_the_debounce() {
        assert_eq!(may_save(true, Some(100.0), 110.0), Save::TooSoon);
        assert_eq!(may_save(true, Some(100.0), 119.9), Save::TooSoon);
        assert_eq!(may_save(true, Some(100.0), 120.0), Save::Flush);
        assert_eq!(may_save(true, Some(100.0), 200.0), Save::Flush);
    }

    #[test]
    fn nothing_new_in_the_directory_is_no_clip() {
        assert_eq!(saved_file(&["a.mp4"], &[("a.mp4", 1.0)]), None);
        assert_eq!(saved_file(&[], &[]), None);
    }

    #[test]
    fn the_newest_new_file_is_the_one_this_flush_asked_for() {
        let after = [("old.mp4", 1.0), ("new.mp4", 3.0), ("mid.mp4", 2.0)];
        assert_eq!(saved_file(&[], &after), Some("new.mp4"));
    }

    #[test]
    fn a_file_that_was_already_there_is_not_a_clip() {
        let after = [("old.mp4", 9.0), ("new.mp4", 1.0)];
        assert_eq!(
            saved_file(&["old.mp4"], &after),
            Some("new.mp4"),
            "even though it is older than the one already there"
        );
    }
}
