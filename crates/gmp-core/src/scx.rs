//! sched_ext scheduler selection.
//!
//! A port of the pure slice of `src/goblinmode/scx.py`. Everything that talks
//! to `scx_loader` over D-Bus, or looks for scheduler binaries on disk, stays
//! in Python. What moves is the mode table and the name handling.
//!
//! Worth knowing while reading: the scheduler NAME is the substantive setting
//! and is verified by reading `CurrentScheduler` back after a switch. The mode
//! is a tuning variant of that scheduler, so a wrong id here would quietly
//! pick a different tuning of the right scheduler rather than fail - which is
//! why the table is carried across exactly and pinned by a test.

/// `scx_loader`'s SchedMode enum, by name.
///
/// Only `auto` (the restore default) and `gaming` are used unless a profile
/// asks for something else, and those two are the unambiguous ends of the
/// enum. The middle three have never been confirmed against a running loader.
pub const SCHED_MODES: &[(&str, u32)] = &[
    ("auto", 0),
    ("gaming", 1),
    ("lowlatency", 2),
    ("powersave", 3),
    ("server", 4),
];

pub const DEFAULT_MODE: &str = "gaming";

/// The enum id for a mode name, falling back to [`DEFAULT_MODE`].
///
/// An unknown name does NOT fail. A profile naming a mode this build does not
/// know should still start the game with a sensible scheduler rather than
/// refuse to launch it.
pub fn mode_id(mode: &str) -> u32 {
    SCHED_MODES
        .iter()
        .find(|(name, _)| *name == mode)
        .or_else(|| SCHED_MODES.iter().find(|(name, _)| *name == DEFAULT_MODE))
        .map_or(0, |(_, id)| *id)
}

/// The short form of a scheduler name: `scx_lavd` and `lavd` both give `lavd`.
pub fn short_name(scheduler: &str) -> String {
    scheduler
        .strip_prefix("scx_")
        .unwrap_or(scheduler)
        .to_owned()
}

/// The full binary name. Idempotent: feeding it its own output is a no-op,
/// which matters because profiles are written by both the GUI and by hand.
pub fn full_name(scheduler: &str) -> String {
    format!("scx_{}", short_name(scheduler))
}

/// Whether a scheduler name is the right SHAPE.
///
/// A shape check rather than an allowlist, deliberately: new scx schedulers
/// ship often, and a list here would reject one the moment it appeared. It
/// also keeps a profile from smuggling a path or an argument into something
/// that becomes a binary name.
pub fn valid_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    if bytes.is_empty() || bytes.len() > 32 {
        return false;
    }
    if !bytes[0].is_ascii_lowercase() && !bytes[0].is_ascii_digit() {
        return false;
    }
    bytes
        .iter()
        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_mode_table_matches_the_loaders_enum() {
        // A wrong id picks a different TUNING of the right scheduler rather
        // than failing, so nothing downstream would notice.
        assert_eq!(mode_id("auto"), 0);
        assert_eq!(mode_id("gaming"), 1);
        assert_eq!(mode_id("lowlatency"), 2);
        assert_eq!(mode_id("powersave"), 3);
        assert_eq!(mode_id("server"), 4);
    }

    #[test]
    fn an_unknown_mode_falls_back_rather_than_failing() {
        // A profile naming a mode this build does not know should still start
        // the game.
        assert_eq!(mode_id("nonsense"), mode_id(DEFAULT_MODE));
        assert_eq!(mode_id(""), mode_id(DEFAULT_MODE));
    }

    #[test]
    fn scheduler_names_normalise_both_ways() {
        assert_eq!(short_name("scx_lavd"), "lavd");
        assert_eq!(short_name("lavd"), "lavd");
        assert_eq!(full_name("lavd"), "scx_lavd");
        assert_eq!(full_name("scx_lavd"), "scx_lavd", "idempotent");
        assert_eq!(full_name(&full_name("lavd")), "scx_lavd");
        // Exactly ONE prefix comes off, matching str.removeprefix.
        // Stripping repeatedly would turn a name that legitimately
        // begins "scx_" into a different scheduler.
        assert_eq!(short_name("scx_scx_lavd"), "scx_lavd");
    }

    #[test]
    fn the_name_shape_accepts_real_schedulers() {
        for name in [
            "lavd", "bpfland", "rusty", "simple", "central", "flatcg", "p2dq",
        ] {
            assert!(valid_name(name), "{name}");
        }
    }

    #[test]
    fn the_name_shape_refuses_anything_that_is_not_one() {
        // The point is to stop a profile smuggling a path or an argument into
        // something that becomes a binary name.
        for name in [
            "",
            "../etc/passwd",
            "lavd; rm -rf /",
            "scx lavd",
            "LAVD",
            "-rf",
            "/usr/bin/scx_lavd",
            "lavd\n",
            &"a".repeat(33),
            // Legal characters, illegal FIRST character. This is the only
            // shape that isolates the leading-character rule from the
            // character class - without it, deleting that rule outright
            // still passes every case above.
            "_lavd",
            "__",
        ] {
            assert!(!valid_name(name), "{name:?} was accepted");
        }
        assert!(valid_name(&"a".repeat(32)), "32 is the limit, not 31");
    }
}
