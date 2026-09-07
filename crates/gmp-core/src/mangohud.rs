//! The MangoHud configurator: the block this tool owns inside a file it does
//! not.
//!
//! `~/.config/MangoHud/MangoHud.conf` belongs to the user. Goblin Mode Pro
//! writes a fenced block into it and removes exactly that block again, so
//! every key the user set by hand survives a round trip. Getting that wrong
//! does not fail loudly: it silently eats settings somebody typed.
//!
//! The block is also what the frame-rate watchdog depends on. `output_folder`
//! and `autostart_log` are what produce the CSV `fpswatch` tails, so a profile
//! with the watchdog on and no overlay still needs a block written.

use crate::config::{truthy, GameProfile};
use crate::store::splitlines;

pub const BEGIN: &str = "### goblin-mode-pro begin";
pub const END: &str = "### goblin-mode-pro end";

/// The overlay toggles, in the order they are written. Order is not cosmetic:
/// the file is compared byte for byte against the Python's.
const TOGGLES: &[&str] = &["fps", "cpu_temp", "gpu_temp", "ram", "frame_timing"];

/// The in-game keys written into every managed block.
///
/// MangoHud only reads its config at launch, so these are the user's escape
/// hatch for a change made mid-session - which is why they are pinned rather
/// than left to whatever the file already said.
const HOTKEYS: &[&str] = &[
    "toggle_hud=Shift_R+F12",
    "toggle_logging=Shift_L+F2",
    "reload_cfg=Shift_L+F4",
];

/// What the managed block says for this profile.
pub fn entries_for(profile: &GameProfile, log_dir: &str) -> Vec<String> {
    let mut entries = Vec::new();
    let on = |key: &str| profile.mangohud.get(key).is_some_and(truthy);

    if on("enabled") {
        entries.push("no_display=0".to_string());
        for key in TOGGLES {
            if on(key) {
                entries.push((*key).to_string());
            }
        }
    } else {
        entries.push("no_display=1".to_string());
    }

    // The watchdog tails the CSV this produces, and it works whether or not
    // the overlay is drawn.
    if truthy(&profile.fps_watchdog) {
        entries.push(format!("output_folder={log_dir}"));
        entries.push("log_interval=200".to_string());
        entries.push("autostart_log=1".to_string());
        entries.push("log_duration=0".to_string());
    }

    if on("enabled") || truthy(&profile.fps_watchdog) {
        entries.extend(HOTKEYS.iter().map(|k| (*k).to_string()));
    }
    entries
}

/// Remove the managed block, leaving everything else exactly as it was.
///
/// The markers are matched on their STRIPPED form, so a block someone
/// re-indented is still found. A BEGIN with no END swallows the rest of the
/// file - which is what the Python does, and is the right answer: a truncated
/// block means the tool died mid-write, and what follows it is ours.
pub fn strip_block(lines: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut inside = false;
    for line in lines {
        let trimmed = line.trim();
        if trimmed == BEGIN {
            inside = true;
            continue;
        }
        if trimmed == END {
            inside = false;
            continue;
        }
        if !inside {
            out.push(line.clone());
        }
    }
    out
}

/// Replace the managed block, keeping one blank line between it and whatever
/// the user wrote.
///
/// Trailing blank lines are dropped first, so repeated writes do not push the
/// block further down the file each time - and the separator is added only
/// when there is something to separate it FROM, so a file that is nothing but
/// our block does not start with an empty line.
pub fn set_block(lines: &[String], entries: &[String]) -> Vec<String> {
    let mut out = strip_block(lines);
    while out.last().is_some_and(|line| line.trim().is_empty()) {
        out.pop();
    }
    if !out.is_empty() {
        out.push(String::new());
    }
    out.push(BEGIN.to_string());
    out.extend(entries.iter().cloned());
    out.push(END.to_string());
    out
}

/// The file as it is written: joined, with trailing newlines collapsed to
/// exactly one.
///
/// `rstrip("\n")` strips only newlines, so trailing SPACES on the last line
/// survive - and an empty file is a single newline rather than nothing.
pub fn render(lines: &[String]) -> String {
    let joined = lines.join("\n");
    format!("{}\n", joined.trim_end_matches('\n'))
}

/// The whole of `apply`: read a file, replace our block, write it back.
pub fn apply_text(existing: &str, profile: &GameProfile, log_dir: &str) -> String {
    let lines: Vec<String> = splitlines(existing).iter().map(|s| s.to_string()).collect();
    render(&set_block(&lines, &entries_for(profile, log_dir)))
}

/// The whole of `revert`: take our block out, and touch nothing otherwise.
///
/// A file with no block of ours is returned BYTE FOR BYTE, not re-rendered.
/// The Python only writes when the stripped lines differ from what it read,
/// and that guard is the module's promise rather than an optimisation: a
/// revert that rewrote the file anyway would collapse the user's trailing
/// blank lines, turn every exotic line terminator `splitlines` breaks on into
/// a newline, and add a trailing newline to a file that had none - on every
/// game exit, for a file it had nothing to remove from.
pub fn revert_text(existing: &str) -> String {
    let lines: Vec<String> = splitlines(existing).iter().map(|s| s.to_string()).collect();
    let stripped = strip_block(&lines);
    if stripped == lines {
        return existing.to_string();
    }
    render(&stripped)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(enabled: bool, watchdog: bool, toggles: &[&str]) -> GameProfile {
        let mut map = serde_json::Map::new();
        map.insert("enabled".into(), serde_json::json!(enabled));
        for key in TOGGLES {
            map.insert((*key).into(), serde_json::json!(toggles.contains(key)));
        }
        GameProfile {
            exe: "Wow.exe".into(),
            mangohud: map,
            fps_watchdog: serde_json::json!(watchdog),
            ..GameProfile::default()
        }
    }

    fn lines(text: &str) -> Vec<String> {
        splitlines(text).iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn an_overlay_that_is_off_says_so_and_nothing_else() {
        assert_eq!(
            entries_for(&profile(false, false, &[]), "/logs"),
            vec!["no_display=1"]
        );
    }

    #[test]
    fn an_overlay_that_is_on_lists_its_toggles_in_a_fixed_order() {
        assert_eq!(
            entries_for(&profile(true, false, &["ram", "fps", "gpu_temp"]), "/logs"),
            vec![
                "no_display=0",
                "fps",
                "gpu_temp",
                "ram",
                "toggle_hud=Shift_R+F12",
                "toggle_logging=Shift_L+F2",
                "reload_cfg=Shift_L+F4",
            ]
        );
    }

    #[test]
    fn the_watchdog_writes_its_logging_keys_with_the_overlay_hidden() {
        // The case that matters: no overlay, but the CSV the frame-rate
        // watcher tails still has to be produced.
        assert_eq!(
            entries_for(&profile(false, true, &[]), "/logs"),
            vec![
                "no_display=1",
                "output_folder=/logs",
                "log_interval=200",
                "autostart_log=1",
                "log_duration=0",
                "toggle_hud=Shift_R+F12",
                "toggle_logging=Shift_L+F2",
                "reload_cfg=Shift_L+F4",
            ]
        );
    }

    #[test]
    fn nothing_on_means_no_hotkeys_to_pin() {
        let entries = entries_for(&profile(false, false, &[]), "/logs");
        assert!(!entries.iter().any(|e| e.starts_with("toggle_hud")));
    }

    #[test]
    fn a_users_own_keys_survive_a_round_trip() {
        let original = "fps_limit=144\nfont_size=24\n";
        let written = apply_text(original, &profile(true, false, &["fps"]), "/logs");
        assert!(written.starts_with("fps_limit=144\nfont_size=24\n"));
        assert_eq!(revert_text(&written), original);
    }

    #[test]
    fn writing_twice_does_not_walk_the_block_down_the_file() {
        let p = profile(true, false, &["fps"]);
        let once = apply_text("fps_limit=144\n", &p, "/logs");
        let twice = apply_text(&once, &p, "/logs");
        assert_eq!(once, twice);
    }

    #[test]
    fn a_file_that_is_only_our_block_does_not_start_blank() {
        let written = apply_text("", &profile(false, false, &[]), "/logs");
        assert_eq!(written, format!("{BEGIN}\nno_display=1\n{END}\n"));
    }

    #[test]
    fn a_re_indented_marker_is_still_our_block() {
        let text = format!("keep=1\n   {BEGIN}\nno_display=1\n  {END}  \n");
        assert_eq!(revert_text(&text), "keep=1\n");
    }

    #[test]
    fn a_block_that_was_never_closed_takes_the_rest_with_it() {
        // A begin with no end means a write that died partway. What follows
        // is ours, not the user's.
        let text = format!("keep=1\n{BEGIN}\nno_display=1\nhalf=written\n");
        assert_eq!(revert_text(&text), "keep=1\n");
    }

    #[test]
    fn a_file_with_no_block_is_returned_unchanged() {
        assert_eq!(revert_text("a=1\nb=2\n"), "a=1\nb=2\n");
    }

    #[test]
    fn a_file_with_no_block_is_not_even_re_rendered() {
        // Not "equivalent after a round trip" - the same bytes. Everything
        // here would survive a revert that found something to remove and is
        // destroyed by one that rewrites for no reason.
        for text in [
            "",
            "a=1",
            "a=1\n\n\n",
            "a=1\r\nb=2\r\n",
            "a\u{b}b\n",
            "a\u{2028}b\n",
        ] {
            assert_eq!(revert_text(text), text, "{text:?}");
        }
    }

    #[test]
    fn trailing_newlines_collapse_to_one_and_trailing_spaces_do_not() {
        assert_eq!(render(&lines("a\n\n\n")), "a\n");
        assert_eq!(render(&["a".to_string(), "   ".to_string()]), "a\n   \n");
        assert_eq!(render(&[]), "\n");
    }
}
