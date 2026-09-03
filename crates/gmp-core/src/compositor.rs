//! Parsing `kscreen-doctor -o`, and the decisions taken from it.
//!
//! A port of the pure slice of `src/goblinmode/compositor.py`. Every
//! subprocess call - kscreen-doctor, kwriteconfig6, hyprctl - stays in Python.
//! What moves is the parser and the mode selection built on it.
//!
//! The thing to know before changing anything here: the Python original keeps
//! its outputs and modes in dicts, and BOTH of Python's dict-ordering rules
//! are load-bearing. `find_mode_id` returns the first match in insertion
//! order, and re-assigning an existing key updates the value while leaving the
//! key where it was. A `HashMap` would reproduce neither, so this uses an
//! ordered vector and an explicit upsert.

use serde::{Deserialize, Serialize};

/// The VRR policies kscreen accepts. Anything else is refused before it can
/// reach the command line.
pub const VRR_VALUES: &[&str] = &["never", "automatic", "always"];

/// One display mode: `(width, height, hz)`.
pub type Mode = (u64, u64, u64);

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Output {
    /// `(mode_id, mode)` in the order kscreen-doctor listed them.
    pub modes: Vec<(String, Mode)>,
    /// The mode marked active with `*`, if any.
    pub current: Option<String>,
}

impl Output {
    /// Insert or update, Python-dict style: an id already present keeps its
    /// position and takes the new value.
    fn upsert(&mut self, id: &str, mode: Mode) {
        match self.modes.iter_mut().find(|(existing, _)| existing == id) {
            Some(slot) => slot.1 = mode,
            None => self.modes.push((id.to_owned(), mode)),
        }
    }

    pub fn get(&self, id: &str) -> Option<Mode> {
        self.modes
            .iter()
            .find(|(existing, _)| existing == id)
            .map(|(_, mode)| *mode)
    }
}

fn output_name(line: &str) -> Option<&str> {
    // `^Output:\s+\d+\s+(\S+)` against the STRIPPED line. Each \s+ and \d+
    // needs at least one character, so each step checks that it consumed
    // something rather than trusting trim to have done it.
    let after_label = line.trim().strip_prefix("Output:")?;
    let after_gap = eat_space(after_label)?;
    let digits_end = after_gap
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(after_gap.len());
    if digits_end == 0 {
        return None;
    }
    let name = eat_space(&after_gap[digits_end..])?;
    let name = name.split(is_py_space).next().unwrap_or(name);
    (!name.is_empty()).then_some(name)
}

/// One or more whitespace characters, as `\s+` demands.
fn eat_space(s: &str) -> Option<&str> {
    let rest = s.trim_start_matches(is_py_space);
    (rest.len() != s.len()).then_some(rest)
}

/// What Python's `\s` matches in a `str` pattern.
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || c == '\u{1c}' || c == '\u{1d}' || c == '\u{1e}' || c == '\u{1f}'
}

/// Every `<id>:<w>x<h>@<hz>` on a line, with whether it carried the active `*`.
///
/// Deliberately a hand-written scan rather than a regex: it is the same
/// left-to-right, non-overlapping scan `re.findall` performs, and it keeps the
/// crate's only display parser readable without a second pattern dialect to
/// keep in sync with the Python one.
fn find_modes(line: &str) -> Vec<(String, Mode, bool)> {
    let bytes = line.as_bytes();
    let mut found = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        let Some((id, w, h, hz, active, next)) = scan_mode(bytes, i) else {
            i += 1;
            continue;
        };
        found.push((id, (w, h, hz), active));
        // findall does not rescan the text it consumed.
        i = next.max(i + 1);
    }
    found
}

fn scan_mode(bytes: &[u8], start: usize) -> Option<(String, u64, u64, u64, bool, usize)> {
    let mut i = start;
    let id = take_digits(bytes, &mut i)?;
    expect(bytes, &mut i, b':')?;
    let w = take_number(bytes, &mut i)?;
    expect(bytes, &mut i, b'x')?;
    let h = take_number(bytes, &mut i)?;
    expect(bytes, &mut i, b'@')?;
    let hz = take_number(bytes, &mut i)?;
    // (!?) then (*?) - both optional, in that order.
    if bytes.get(i) == Some(&b'!') {
        i += 1;
    }
    let active = bytes.get(i) == Some(&b'*');
    if active {
        i += 1;
    }
    Some((id, w, h, hz, active, i))
}

fn take_digits(bytes: &[u8], i: &mut usize) -> Option<String> {
    let start = *i;
    while bytes.get(*i).is_some_and(u8::is_ascii_digit) {
        *i += 1;
    }
    (*i > start).then(|| String::from_utf8_lossy(&bytes[start..*i]).into_owned())
}

/// A run of digits as a number.
///
/// Returns `None` on overflow, which drops the whole mode. Python's ints do
/// not overflow, so this is the one place the two implementations could
/// disagree - it takes a 20-digit refresh rate to reach, which is not
/// something kscreen-doctor can emit about real hardware. Dropping is the
/// conservative end: an unparseable mode is never selected.
fn take_number(bytes: &[u8], i: &mut usize) -> Option<u64> {
    take_digits(bytes, i)?.parse().ok()
}

fn expect(bytes: &[u8], i: &mut usize, want: u8) -> Option<()> {
    (bytes.get(i.to_owned()) == Some(&want)).then(|| *i += 1)
}

/// `{output: {modes, current}}` from `kscreen-doctor -o` output.
///
/// A `Modes:` line looks like
/// `Modes: 87:1920x1080@144!*  88:1920x1080@60` - `!` preferred, `*` active.
pub fn parse_output_modes(stdout: &str) -> Vec<(String, Output)> {
    let mut out: Vec<(String, Output)> = Vec::new();
    let mut name: Option<String> = None;
    for line in stdout.lines() {
        if let Some(found) = output_name(line) {
            // Python re-assigns the key, which RESETS the entry but keeps its
            // position. A duplicated output name discards the earlier modes.
            match out.iter_mut().find(|(existing, _)| existing == found) {
                Some(slot) => slot.1 = Output::default(),
                None => out.push((found.to_owned(), Output::default())),
            }
            name = Some(found.to_owned());
            continue;
        }
        let Some(current_name) = name.as_deref() else {
            continue;
        };
        if !line.contains("Modes:") {
            continue;
        }
        let modes = find_modes(line);
        let Some((_, entry)) = out.iter_mut().find(|(n, _)| n == current_name) else {
            continue;
        };
        for (id, mode, active) in modes {
            entry.upsert(&id, mode);
            if active {
                entry.current = Some(id);
            }
        }
    }
    out
}

/// The built-in panel, if any. kscreen-doctor names it `eDP-*`, the way every
/// other Linux display stack does.
pub fn internal_panel_output(stdout: &str) -> Option<String> {
    parse_output_modes(stdout)
        .into_iter()
        .find(|(name, _)| name.starts_with("eDP"))
        .map(|(name, _)| name)
}

/// The first mode matching all three of width, height and refresh.
///
/// First in the order kscreen-doctor listed them, which is why the modes are
/// a vector. Two ids can describe the same mode.
pub fn find_mode_id(modes: &[(String, Mode)], w: u64, h: u64, hz: u64) -> Option<String> {
    modes
        .iter()
        .find(|(_, (mw, mh, mhz))| *mw == w && *mh == h && *mhz == hz)
        .map(|(id, _)| id.clone())
}

/// The mode to switch `output` to for `hz`, and the one to restore afterwards.
///
/// `None` covers every reason not to act, which the caller treats alike:
/// no such output, no active mode to read the resolution from, no mode at that
/// refresh rate, or already there. The last is the one worth naming - asking
/// kscreen to set the mode that is already set is a needless modeset, and on
/// some panels a visible black frame.
pub fn plan_refresh_change(stdout: &str, output: &str, hz: u64) -> Option<(String, String)> {
    let parsed = parse_output_modes(stdout);
    let (_, entry) = parsed.iter().find(|(name, _)| name == output)?;
    let current = entry.current.as_deref()?;
    let (w, h, _) = entry.get(current)?;
    let target = find_mode_id(&entry.modes, w, h, hz)?;
    (target != current).then(|| (target, current.to_owned()))
}

/// VRR policy for each capable output, from `kscreen-doctor -o`.
///
/// `incapable` outputs are left out entirely: the caller uses presence in this
/// map to mean "this output can do VRR", so listing an incapable one would
/// have it try.
pub fn vrr_outputs(stdout: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    let mut name: Option<String> = None;
    for line in stdout.lines() {
        if let Some(found) = output_name(line) {
            name = Some(found.to_owned());
            continue;
        }
        let Some(state) = vrr_state(line) else {
            continue;
        };
        let Some(current) = name.take() else {
            continue;
        };
        if state == "incapable" {
            continue;
        }
        match out.iter_mut().find(|(existing, _)| *existing == current) {
            Some(slot) => slot.1 = state,
            None => out.push((current, state)),
        }
    }
    out
}

/// `Vrr:\s*(\w+)` - searched anywhere in the line, not anchored.
fn vrr_state(line: &str) -> Option<String> {
    let at = line.find("Vrr:")?;
    let rest = line[at + "Vrr:".len()..].trim_start_matches(is_py_space);
    let end = rest
        .find(|c: char| !(c.is_alphanumeric() || c == '_'))
        .unwrap_or(rest.len());
    (end > 0).then(|| rest[..end].to_lowercase())
}

pub fn valid_vrr_policy(policy: &str) -> bool {
    VRR_VALUES.contains(&policy)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The real shape of `kscreen-doctor -o`: one `Output:` line, then
    /// indented property lines. `Modes:` is never on the `Output:` line
    /// itself, which is why the parser can `continue` past it.
    const SAMPLE: &str = "\
Output: 1 eDP-1
\tenabled
\tconnected
\tpriority 1
\tPanel
\tModes: 87:1920x1080@144!*  88:1920x1080@60  89:1280x720@60
\tGeometry: 0,0 1920x1080
\tScale: 1
\tVrr: Automatic
Output: 2 DP-2
\tenabled
\tconnected
\tpriority 2
\tDisplayPort
\tModes: 12:2560x1440@165*  13:2560x1440@60
\tGeometry: 1920,0 2560x1440
\tVrr: Incapable
";

    /// One output with the given `Modes:` line, in the real two-line shape.
    fn dump(name: &str, modes: &str) -> String {
        format!("Output: 1 {name}\n\tModes: {modes}\n")
    }

    #[test]
    fn a_real_kscreen_dump_parses() {
        let parsed = parse_output_modes(SAMPLE);
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0].0, "eDP-1");
        assert_eq!(parsed[0].1.current.as_deref(), Some("87"));
        assert_eq!(parsed[0].1.get("89"), Some((1280, 720, 60)));
        assert_eq!(parsed[1].1.current.as_deref(), Some("12"));
    }

    #[test]
    fn the_active_marker_is_what_sets_current() {
        // `!` is "preferred" and must NOT be mistaken for it - a panel's
        // preferred mode is frequently not the one it is running.
        let one = dump("eDP-1", "1:1920x1080@144!  2:1920x1080@60*");
        let parsed = parse_output_modes(&one);
        assert_eq!(parsed[0].1.current.as_deref(), Some("2"));
    }

    #[test]
    fn an_output_with_no_active_mode_is_not_planned_for() {
        let one = dump("eDP-1", "1:1920x1080@144  2:1920x1080@60");
        assert_eq!(plan_refresh_change(&one, "eDP-1", 60), None);
    }

    #[test]
    fn a_refresh_change_keeps_the_resolution() {
        let plan = plan_refresh_change(SAMPLE, "eDP-1", 60).expect("60 Hz exists at 1920x1080");
        assert_eq!(plan, ("88".into(), "87".into()));
        // 720p60 exists, but not at the current resolution.
        assert_eq!(plan_refresh_change(SAMPLE, "eDP-1", 90), None);
    }

    #[test]
    fn switching_to_the_mode_already_set_is_not_a_change() {
        // Otherwise every profile apply causes a modeset, which on some
        // panels is a visible black frame.
        assert_eq!(plan_refresh_change(SAMPLE, "eDP-1", 144), None);
    }

    #[test]
    fn find_mode_id_returns_the_first_of_a_duplicate_pair() {
        let modes = vec![
            ("10".to_string(), (1920, 1080, 60)),
            ("11".to_string(), (1920, 1080, 60)),
        ];
        assert_eq!(find_mode_id(&modes, 1920, 1080, 60).as_deref(), Some("10"));
    }

    #[test]
    fn a_repeated_mode_id_keeps_its_place_and_takes_the_new_value() {
        // Python's dict assignment, exactly: position from the first write,
        // value from the last.
        let line = dump("eDP-1", "7:1920x1080@60  8:1280x720@60  7:800x600@75");
        let parsed = parse_output_modes(&line);
        assert_eq!(parsed[0].1.modes[0].0, "7");
        assert_eq!(parsed[0].1.get("7"), Some((800, 600, 75)));
        assert_eq!(parsed[0].1.modes.len(), 2);
    }

    #[test]
    fn a_repeated_output_name_resets_its_modes() {
        let two = dump("eDP-1", "1:1920x1080@60*") + &dump("eDP-1", "2:1280x720@60");
        let parsed = parse_output_modes(&two);
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].1.modes.len(), 1);
        assert_eq!(
            parsed[0].1.current, None,
            "the old active mode went with it"
        );
    }

    #[test]
    fn modes_before_any_output_line_are_dropped() {
        assert!(parse_output_modes("Modes: 1:1920x1080@60*").is_empty());
    }

    #[test]
    fn the_internal_panel_is_the_edp_one() {
        assert_eq!(internal_panel_output(SAMPLE).as_deref(), Some("eDP-1"));
        assert_eq!(internal_panel_output(&dump("DP-2", "1:800x600@60")), None);
    }

    #[test]
    fn incapable_outputs_are_left_out_of_the_vrr_map() {
        // Presence in the map means "can do VRR", so an incapable output
        // listed here would be asked to enable it.
        let vrr = vrr_outputs(SAMPLE);
        assert_eq!(vrr, vec![("eDP-1".to_string(), "automatic".to_string())]);
    }

    #[test]
    fn only_the_three_kscreen_policies_are_accepted() {
        for good in VRR_VALUES {
            assert!(valid_vrr_policy(good));
        }
        for bad in ["", "Never", "auto", "on", "always;reboot"] {
            assert!(!valid_vrr_policy(bad), "{bad:?}");
        }
    }

    #[test]
    fn a_mode_number_too_large_for_the_hardware_is_dropped_not_truncated() {
        // The one documented divergence from Python, whose ints do not
        // overflow. Truncating would be worse than dropping: a wrong mode
        // that gets SELECTED beats one that never matches anything.
        let line = dump("eDP-1", &format!("1:1920x1080@{}*", "9".repeat(25)));
        let parsed = parse_output_modes(&line);
        assert!(parsed[0].1.modes.is_empty());
        assert_eq!(parsed[0].1.current, None);
    }
}
