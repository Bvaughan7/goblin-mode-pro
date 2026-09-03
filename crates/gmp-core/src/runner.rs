//! Resolving a profile for a launch, and the environment it implies.
//!
//! A port of the pure slice of `src/goblinmode/runner.py`. Writing the wrapper
//! script and listing log files stay in Python; everything that decides what a
//! game launches with moves, because this runs for every single launch and is
//! the code path a mistake in is hardest to notice.
//!
//! ## One divergence that cannot be engineered away
//!
//! A profile with `match_mode = "regex"` holds a user-supplied pattern, and
//! Python's `re` and Rust's `regex` are not the same language. `regex` refuses
//! backreferences and lookaround by design; `re` accepts both. A pattern using
//! them compiles in Python and does not here, and this side then behaves as
//! the Python does for a pattern that fails to compile - it skips the profile
//! and moves on.
//!
//! That is the safe direction, and it is deliberate: skipping means the game
//! launches untuned, which is what happens today for anyone whose pattern is
//! malformed. The alternative - matching something the user did not intend -
//! would apply another game's power limits. It is called out here, and pinned
//! by a test, because it is the one place the two implementations knowingly
//! disagree.
//!
//! The gap is narrower than it looks, for a reason worth knowing on its own:
//! `sanitize_exe` rejects backslashes as path separators, so a stored pattern
//! can contain NO escape sequence at all - not `\.`, not `\d`, not a
//! backreference. Everything the two engines do differently with escapes is
//! unreachable from a profile. What is left is lookaround and conditionals.

use crate::config::{slug, GameProfile, Settings};

/// Basename that also splits Windows-style paths (`C:\dir\Game.exe`).
///
/// Steam launch options carry Windows paths through to Proton games, so a
/// token here is as likely to use backslashes as forward ones.
pub fn basename(token: &str) -> String {
    let mut s = token
        .trim()
        .trim_matches('"')
        .trim_matches('\'')
        .to_string();
    for sep in ['\\', '/'] {
        if let Some(index) = s.rfind(sep) {
            s = s[index + sep.len_utf8()..].to_string();
        }
    }
    s
}

/// The enabled profile whose exe appears anywhere in `argv`.
///
/// First match in profile order wins, which makes the list order a user-facing
/// setting rather than an implementation detail.
pub fn resolve_profile_for_argv<'a>(
    argv: &[String],
    settings: &'a Settings,
) -> Option<&'a GameProfile> {
    let names: Vec<String> = argv.iter().map(|a| basename(a).to_lowercase()).collect();
    let joined = argv.join(" ").to_lowercase();

    for profile in settings.enabled_profiles() {
        let exe = profile.exe.to_lowercase();
        match profile.match_mode.as_str().unwrap_or("") {
            "exact" if names.contains(&exe) => return Some(profile),
            "substring" if joined.contains(&exe) => return Some(profile),
            "regex" => {
                // The pattern is bounded before compiling - it is user input
                // and this is the ReDoS guard.
                let source: String = profile.exe.chars().take(128).collect();
                let Ok(pattern) = regex::Regex::new(&source) else {
                    continue; // re.error -> continue, in the Python too
                };
                if argv
                    .iter()
                    .any(|a| pattern.is_match(&a.chars().take(4096).collect::<String>()))
                {
                    return Some(profile);
                }
            }
            _ => {}
        }
    }
    None
}

/// The environment a launch of `argv` should carry, before validation.
///
/// `mangohud_dir` is passed in rather than resolved, because it comes from the
/// XDG paths and is the one thing here that depends on where the user's home
/// is rather than on what they configured.
pub fn resolve_env_for_argv(
    argv: &[String],
    settings: &Settings,
    mangohud_dir: &str,
) -> Vec<(String, String)> {
    let Some(profile) = resolve_profile_for_argv(argv, settings) else {
        return Vec::new();
    };
    let mut env = profile.env_assignments();

    // MangoHud has to be told to load; the config file alone does not inject
    // it. Needed both for the visible overlay AND for the frame-rate
    // watchdog's CSV log, which is why fps_watchdog alone is enough.
    let overlay_on = profile
        .mangohud
        .get("enabled")
        .is_some_and(crate::config::truthy);
    if overlay_on || crate::config::truthy(&profile.fps_watchdog) {
        env.push(("MANGOHUD".to_string(), "1".to_string()));
        if crate::config::truthy(&profile.per_game_mangohud) {
            env.push((
                "MANGOHUD_CONFIGFILE".to_string(),
                format!("{mangohud_dir}/{}.conf", slug(&profile.exe)),
            ));
        }
    }
    env
}

/// Validated `NAME=VALUE` lines for the wrapper to import.
///
/// Names must be shell-safe identifiers and values single-line and free of
/// control characters; anything else is dropped rather than escaped. The
/// wrapper reads these back one line at a time with no `eval`, so a value
/// containing a newline would be read as a second assignment - which is
/// exactly what the value check exists to prevent.
pub fn print_env_for(argv: &[String], settings: &Settings, mangohud_dir: &str) -> String {
    let mut env = resolve_env_for_argv(argv, settings, mangohud_dir);
    env.sort_by(|a, b| a.0.cmp(&b.0));
    env.into_iter()
        .filter(|(name, value)| valid_env_name(name) && valid_env_value(value))
        .map(|(name, value)| format!("{name}={value}"))
        .collect::<Vec<_>>()
        .join("\n")
}

/// `^[A-Za-z_][A-Za-z0-9_]*\Z`.
///
/// `\Z` and not `$`: Python's `$` also matches just before a trailing newline,
/// which let a name ending in one through - and the wrapper then read it back
/// as the name alone, with an empty value, silently dropping the setting.
pub fn valid_env_name(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !(first.is_ascii_alphabetic() || first == '_') {
        return false;
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// `^[^\x00-\x1f\x7f]{0,4096}\Z`.
pub fn valid_env_value(value: &str) -> bool {
    value.chars().count() <= 4096 && !value.chars().any(|c| (c as u32) < 0x20 || c as u32 == 0x7f)
}

/// The gamescope argv for a profile, without the trailing `--`.
pub fn gamescope_args(profile: &GameProfile) -> Vec<String> {
    if !crate::config::truthy(&profile.gamescope_enabled) {
        return Vec::new();
    }
    let g = &profile.gamescope;
    let number = |key: &str| -> i64 {
        g.get(key)
            .and_then(serde_json::Value::as_i64)
            .unwrap_or_default()
    };
    // The defaults below are unreachable in practice and carried for
    // fidelity: normalise() fills every gamescope key in before this runs, so
    // `get` always finds something. They mirror the Python's `g.get(key, X)`
    // regardless, so a future change that stops filling them in cannot
    // silently flip a behaviour. Mutating them changes no output today.
    let flag =
        |key: &str, default: bool| -> bool { g.get(key).map_or(default, crate::config::truthy) };

    let mut args: Vec<String> = Vec::new();
    let (w, h, refresh) = (number("w"), number("h"), number("refresh"));
    if w != 0 && h != 0 {
        args.extend(["-W".into(), w.to_string(), "-H".into(), h.to_string()]);
    }
    if refresh != 0 {
        args.extend(["-r".into(), refresh.to_string()]);
    }
    match g.get("upscale").and_then(serde_json::Value::as_str) {
        Some("fsr") => args.extend(["-F".into(), "fsr".into()]),
        Some("nis") => args.extend(["-F".into(), "nis".into()]),
        Some("integer") => args.extend(["-S".into(), "integer".into()]),
        _ => {}
    }
    if flag("hdr", false) {
        args.push("--hdr-enabled".into());
    }
    // Borderless by default: a fullscreen gamescope on a desktop is a good way
    // to lose your other windows behind it.
    args.push(if flag("borderless", true) { "-b" } else { "-f" }.into());
    if flag("steam_overlay", true) {
        args.push("-e".into());
    }
    args
}

/// Space-joined gamescope tokens, safe to word-split in the wrapper.
pub fn print_gamescope(argv: &[String], settings: &Settings) -> String {
    resolve_profile_for_argv(argv, settings)
        .map_or(String::new(), |profile| gamescope_args(profile).join(" "))
}

/// `"1"` to wrap the game with `gamemoderun`, `"0"` to skip it.
///
/// An unmatched game keeps the historical default of `"1"`.
pub fn print_gamemode(argv: &[String], settings: &Settings) -> String {
    match resolve_profile_for_argv(argv, settings) {
        None => "1".to_string(),
        Some(profile) => {
            if profile
                .use_gamemode
                .as_bool()
                .unwrap_or_else(|| crate::config::truthy(&profile.use_gamemode))
            {
                "1".to_string()
            } else {
                "0".to_string()
            }
        }
    }
}

/// What a standalone gamescope session launches with no game specified:
/// Steam's Big Picture, the usual "session" content.
pub const DEFAULT_SESSION_COMMAND: &[&str] = &["steam", "-tenfoot"];

/// argv for a standalone gamescope session, where gamescope is the top-level
/// compositor hosting `command` rather than nesting inside one game process.
pub fn gamescope_session_argv(
    profile: Option<&GameProfile>,
    command: Option<&[String]>,
) -> Vec<String> {
    let args = match profile {
        Some(p) => gamescope_args(p),
        // No profile: borderless with the Steam overlay, the sane default for
        // a session nobody has configured.
        None => vec!["-b".into(), "-e".into()],
    };
    let tail: Vec<String> = match command {
        Some(c) if !c.is_empty() => c.to_vec(),
        _ => DEFAULT_SESSION_COMMAND
            .iter()
            .map(|s| (*s).to_string())
            .collect(),
    };
    let mut out = vec!["gamescope".to_string()];
    out.extend(args);
    out.push("--".to_string());
    out.extend(tail);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings(profiles: serde_json::Value) -> Settings {
        crate::config::from_value(&serde_json::json!({ "profiles": profiles }))
    }

    fn argv(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|s| (*s).to_string()).collect()
    }

    #[test]
    fn a_windows_path_still_yields_a_basename() {
        // Steam launch options carry these through to Proton games.
        assert_eq!(basename("C:\\Games\\Wow.exe"), "Wow.exe");
        assert_eq!(basename("/usr/games/rs2client"), "rs2client");
        assert_eq!(basename("\"C:\\a\\b\\Game.exe\""), "Game.exe");
        assert_eq!(basename("  'Game.exe'  "), "Game.exe");
        assert_eq!(basename("Game.exe"), "Game.exe");
        assert_eq!(basename(""), "");
        // Mixed separators: both are split, forward slash last.
        assert_eq!(basename("C:\\dir/sub\\Game.exe"), "Game.exe");
    }

    #[test]
    fn exact_matching_is_on_the_basename_not_the_whole_token() {
        let s = settings(serde_json::json!([{"exe": "Wow.exe", "match_mode": "exact"}]));
        assert!(resolve_profile_for_argv(&argv(&["C:\\G\\Wow.exe"]), &s).is_some());
        assert!(resolve_profile_for_argv(&argv(&["NotWow.exe"]), &s).is_none());
    }

    #[test]
    fn substring_matching_looks_at_the_whole_command_line() {
        let s = settings(serde_json::json!([{"exe": "rs2client", "match_mode": "substring"}]));
        assert!(resolve_profile_for_argv(&argv(&["/opt/rs2client-linux"]), &s).is_some());
        assert!(resolve_profile_for_argv(&argv(&["java", "-jar", "rs2client.jar"]), &s).is_some());
    }

    #[test]
    fn the_first_matching_profile_wins() {
        // Which makes the profile list ORDER a user-facing setting.
        let s = settings(serde_json::json!([
            {"exe": "game", "display_name": "first", "match_mode": "substring"},
            {"exe": "game", "display_name": "second", "match_mode": "substring"},
        ]));
        let found = resolve_profile_for_argv(&argv(&["/x/game"]), &s).expect("matched");
        assert_eq!(found.display_name, "first");
    }

    #[test]
    fn a_disabled_profile_is_never_matched() {
        let s = settings(serde_json::json!([{"exe": "Wow.exe", "enabled": false}]));
        assert!(resolve_profile_for_argv(&argv(&["Wow.exe"]), &s).is_none());
    }

    #[test]
    fn a_pattern_that_will_not_compile_skips_the_profile() {
        // Both implementations skip; neither treats it as a match. The next
        // profile still gets its chance.
        let s = settings(serde_json::json!([
            {"exe": "*[bad", "match_mode": "regex"},
            {"exe": "Wow.exe", "match_mode": "exact"},
        ]));
        let found = resolve_profile_for_argv(&argv(&["Wow.exe"]), &s).expect("matched");
        assert_eq!(found.exe, "Wow.exe");
    }

    #[test]
    fn a_lookaround_pattern_is_the_documented_divergence() {
        // Python's `re` compiles this; Rust's `regex` refuses it by design.
        // Here it therefore behaves as a pattern that failed to compile - the
        // profile is skipped and the game launches untuned. That is the safe
        // direction, and it is the one place the two knowingly disagree.
        let s = settings(serde_json::json!([{"exe": "Wow(?!64)", "match_mode": "regex"}]));
        assert!(resolve_profile_for_argv(&argv(&["Wow.exe"]), &s).is_none());
    }

    #[test]
    fn a_pattern_cannot_contain_an_escape_at_all() {
        // sanitize_exe bans backslashes as path separators, so the profile is
        // rejected before any regex engine sees it. Which means the regex
        // match mode cannot express "a literal dot", and `Wow.exe` as a
        // pattern also matches `WowXexe`.
        let s = settings(serde_json::json!([{"exe": r"Wow\.exe", "match_mode": "regex"}]));
        assert!(s.profiles.is_empty(), "the profile should not have loaded");
    }

    #[test]
    fn env_lines_are_sorted_and_validated() {
        let s = settings(serde_json::json!([{
            "exe": "Wow.exe",
            "runner_vars": {"nvapi": true, "fsync": true, "no_esync": false,
                            "dxvk_async": false},
        }]));
        let text = print_env_for(&argv(&["Wow.exe"]), &s, "/tmp/mangohud");
        assert_eq!(
            text,
            "DXVK_ENABLE_NVAPI=1\nPROTON_ENABLE_NVAPI=1\nWINEFSYNC=1"
        );
    }

    #[test]
    fn an_unmatched_launch_gets_nothing() {
        let s = settings(serde_json::json!([{"exe": "Wow.exe"}]));
        assert_eq!(print_env_for(&argv(&["other"]), &s, "/tmp"), "");
        assert_eq!(print_gamescope(&argv(&["other"]), &s), "");
        // ...except gamemode, which keeps its historical default.
        assert_eq!(print_gamemode(&argv(&["other"]), &s), "1");
    }

    #[test]
    fn the_watchdog_alone_turns_mangohud_on() {
        // The overlay and the CSV log come from the same switch: without
        // MANGOHUD=1 there is no log, and the frame-rate watchdog has nothing
        // to read.
        let s = settings(serde_json::json!([{
            "exe": "Wow.exe", "fps_watchdog": true,
            "mangohud": {"enabled": false},
            "runner_vars": {"nvapi": false, "fsync": false},
        }]));
        assert_eq!(print_env_for(&argv(&["Wow.exe"]), &s, "/tmp"), "MANGOHUD=1");
    }

    #[test]
    fn a_per_game_config_points_at_a_slug_of_the_exe() {
        let s = settings(serde_json::json!([{
            "exe": "Wow.exe", "fps_watchdog": true, "per_game_mangohud": true,
            "runner_vars": {"nvapi": false, "fsync": false},
        }]));
        let text = print_env_for(&argv(&["Wow.exe"]), &s, "/home/u/.config/mangohud");
        assert!(
            text.contains("MANGOHUD_CONFIGFILE=/home/u/.config/mangohud/Wow.exe.conf"),
            "{text}"
        );
    }

    #[test]
    fn a_name_or_value_that_is_not_shell_safe_is_dropped() {
        assert!(valid_env_name("PROTON_LOG"));
        assert!(valid_env_name("_x"));
        assert!(!valid_env_name(""));
        assert!(!valid_env_name("2FOO"));
        assert!(!valid_env_name("bad name"));
        // The trailing-newline case: the wrapper would read this back as the
        // name alone with an empty value.
        assert!(!valid_env_name("PROTON_LOG\n"));
        assert!(valid_env_value("1"));
        assert!(valid_env_value(""));
        assert!(!valid_env_value("has\nnewline"));
        assert!(!valid_env_value("1\n"));
        assert!(!valid_env_value("bell\u{7}"));
        assert!(valid_env_value(&"x".repeat(4096)));
        assert!(!valid_env_value(&"x".repeat(4097)));
    }

    #[test]
    fn gamescope_is_off_unless_asked_for() {
        let s = settings(serde_json::json!([{"exe": "a"}]));
        let profile = s.profiles.first().expect("one profile");
        assert!(gamescope_args(profile).is_empty());
    }

    #[test]
    fn gamescope_builds_the_argv_the_profile_describes() {
        let s = settings(serde_json::json!([{
            "exe": "a", "gamescope_enabled": true,
            "gamescope": {"w": 1920, "h": 1080, "refresh": 144, "upscale": "fsr",
                          "hdr": true, "borderless": false, "steam_overlay": true},
        }]));
        let profile = s.profiles.first().expect("one profile");
        assert_eq!(
            gamescope_args(profile),
            argv(&[
                "-W",
                "1920",
                "-H",
                "1080",
                "-r",
                "144",
                "-F",
                "fsr",
                "--hdr-enabled",
                "-f",
                "-e"
            ])
        );
    }

    #[test]
    fn a_resolution_needs_both_dimensions() {
        // One without the other is a half-configured profile, and passing a
        // width with no height to gamescope is not better than passing neither.
        let s = settings(serde_json::json!([{
            "exe": "a", "gamescope_enabled": true,
            "gamescope": {"w": 1920, "h": 0, "steam_overlay": false},
        }]));
        let profile = s.profiles.first().expect("one profile");
        assert_eq!(gamescope_args(profile), argv(&["-b"]));
    }

    #[test]
    fn a_session_with_no_profile_still_launches_something() {
        let session = gamescope_session_argv(None, None);
        assert_eq!(
            session,
            argv(&["gamescope", "-b", "-e", "--", "steam", "-tenfoot"])
        );
        let custom = gamescope_session_argv(None, Some(&argv(&["steam"])));
        assert_eq!(custom, argv(&["gamescope", "-b", "-e", "--", "steam"]));
    }
}
