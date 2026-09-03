//! Deciding what counts as a game, without a hardcoded executable list.
//!
//! A port of the scoring half of `src/goblinmode/gamedetect.py`. The
//! per-process signals - reading `/proc/<pid>/fdinfo` for render-engine time,
//! `/proc/<pid>/maps` for linked libraries, the resident set size, and the
//! Steam appmanifest for a title - stay in Python for now. What moves here is
//! the decision made from them.
//!
//! The asymmetry to keep in mind while reading: a false NEGATIVE means the
//! tool does nothing, which is disappointing. A false POSITIVE means it
//! renices a browser and pins the governor for a text editor, which is worse.
//! Almost every rule below exists to make the second one hard.

use regex::Regex;
use std::sync::OnceLock;

/// The score a launcher-tagged process needs to be treated as a game.
pub const GAME_SCORE: i32 = 5;

/// A generic process - one with no launcher tag - needs this much, which it
/// cannot reach on any single signal. Corroboration is the whole point.
const GENERIC_SCORE: i32 = 6;

/// Exact process names that are never games. Carried across verbatim.
/// Adding a name here is how a false positive gets fixed, so the list is
/// the specification rather than a convenience.
pub const BLOCKLIST: &[&str] = &[
    r"Xorg",
    r"Xwayland",
    r"alacritty",
    r"blender",
    r"bottles",
    r"brave",
    r"chrome",
    r"chromium",
    r"code",
    r"dbus-daemon",
    r"discord",
    r"dolphin",
    r"electron",
    r"firefox",
    r"ghostty",
    r"gimp",
    r"gjs",
    r"heroic",
    r"kitty",
    r"konsole",
    r"lutris",
    r"nautilus",
    r"node",
    r"obs",
    r"pipewire",
    r"pulseaudio",
    r"python",
    r"python3",
    r"spotify",
    r"steam",
    r"steamwebhelper",
    r"systemd",
    r"telegram-desktop",
    r"thunderbird",
    r"wezterm-gui",
    r"wireplumber",
];

/// Name/exe substrings marking a desktop-environment or system process.
/// `goblin-mode` is in here on purpose: the tool holds a DRM fd and links
/// GL, so without it the tool would score its own GUI as a game.
pub const BLOCK_STEMS: &[&str] = &[
    r"-portal",
    r"baloo",
    r"colord",
    r"flatpak",
    r"fwupd",
    r"gdm",
    r"geoclue",
    r"gmenudbus",
    r"gnome-session",
    r"gnome-shell",
    r"goblin-mode",
    r"greetd",
    r"gsd-",
    r"gvfs",
    r"hyprpaper",
    r"kaccess",
    r"kactivity",
    r"kdeconnect",
    r"kded",
    r"kglobalaccel",
    r"kiod",
    r"kioworker",
    r"krunner",
    r"ksmserver",
    r"kwalletd",
    r"kwin",
    r"mutter",
    r"org.kde",
    r"org_kde",
    r"packagekit",
    r"plasma",
    r"polkit",
    r"sddm",
    r"startplasma",
    r"swaync",
    r"tracker-",
    r"waybar",
    r"xdg-desktop-portal",
    r"xdg-document",
    r"xdg-permission",
    r"xembed",
];

/// Wine/Proton scaffolding. Matched, but never chosen as "the game" pid,
/// and it earns no GPU or library points - explorer.exe runs in every
/// prefix and links the same libraries the game does.
pub const WINE_INFRA: &[&str] = &[
    r"Agent.exe",
    r"Battle.net Helper.exe",
    r"Battle.net.exe",
    r"SteamLaunch",
    r"conhost.exe",
    r"crashpad_handler",
    r"explorer.exe",
    r"gameoverlayui.exe",
    r"iexplore.exe",
    r"plugplay.exe",
    r"proton",
    r"pv-bwrap",
    r"python3",
    r"reaper",
    r"rpcss.exe",
    r"rundll32.exe",
    r"services.exe",
    r"srt-bwrap",
    r"start.exe",
    r"steam-runtime-launcher-service",
    r"steam.exe",
    r"steamwebhelper.exe",
    r"svchost.exe",
    r"tabtip.exe",
    r"wine",
    r"wine-preloader",
    r"wine64",
    r"wine64-preloader",
    r"wineboot.exe",
    r"winedevice.exe",
    r"wineserver",
    r"xalia.exe",
];

/// Libraries that actually mark a game or game runtime. Deliberately NOT
/// libGL/libEGL/libvulkan alone - modern KDE and GTK link those.
pub const LIB_HINTS: &[&str] = &[
    r"libFAudio",
    r"libSDL2-",
    r"libSDL3",
    r"libdxvk",
    r"libopenxr",
    r"libvkd3d",
    r"libwine.so",
    r"steamclient.so",
];

/// What the caller learned about a process by looking at the system.
///
/// Passed in rather than read here, so the decision can be tested against a
/// described process instead of a real one.
#[derive(Debug, Clone, Copy, Default)]
pub struct Signals {
    /// 0 = no GPU client, 1 = holds a DRM fd, 2 = actively rendering.
    pub gpu_load: u8,
    pub links_game_libs: bool,
    /// Resident set size in bytes, or None if the process vanished.
    pub rss_bytes: Option<u64>,
}

/// A process that scored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scored {
    pub score: i32,
    /// steam | lutris | heroic | generic
    pub source: String,
    pub display_name: String,
    pub app_id: String,
}

/// The basename of a path that may be in Windows or POSIX form.
pub fn win_basename(s: &str) -> String {
    let mut s = s.trim().trim_matches('"').trim_matches('\'');
    for sep in ['\\', '/'] {
        if let Some((_, tail)) = s.rsplit_once(sep) {
            s = tail;
        }
    }
    s.to_owned()
}

/// Whether this is something that is never a game.
///
/// Checked against BOTH the process name and the executable basename, because
/// under Proton they differ - the name is the wine process, the basename is
/// the .exe.
pub fn blocked(name: &str, base: &str) -> bool {
    let (n, b) = (name.to_lowercase(), base.to_lowercase());
    // The tables are written the way processes actually spell themselves -
    // "Xorg", "Xwayland" - which reads better and was silently useless: the
    // names are lowercased before the lookup, so those two entries could never
    // match and the display server was not on the effective blocklist at all.
    // Lowercase BOTH sides.
    if BLOCKLIST
        .iter()
        .any(|e| e.eq_ignore_ascii_case(&n) || e.eq_ignore_ascii_case(&b))
    {
        return true;
    }
    BLOCK_STEMS
        .iter()
        .any(|s| n.contains(&s.to_lowercase()) || b.contains(&s.to_lowercase()))
}

fn steam_launch_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"SteamLaunch\s+AppId=(\d+)").unwrap())
}

fn bare_appid_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"AppId=(\d+)").unwrap())
}

/// The Steam AppID from a command line, if it really is one.
///
/// A bare `AppId=` is only accepted when the command line also mentions steam.
/// Plenty of unrelated programs take an `--AppId` argument, and accepting it
/// unconditionally hands them a five-point launcher score.
pub fn steam_appid_from_cmd(cmd: &str) -> Option<String> {
    if let Some(c) = steam_launch_re().captures(cmd) {
        return Some(c[1].to_owned());
    }
    let c = bare_appid_re().captures(cmd)?;
    if cmd.to_lowercase().contains("steam") {
        Some(c[1].to_owned())
    } else {
        None
    }
}

fn lutris_title_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"lutris-wrapper:\s*(.+)$").unwrap())
}

fn lutris_argv_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r#"lutris-wrapper["']?\s+["']?([^"'\s]+(?:\s+[^"'\s0-9][^"'\s]*)?)"#).unwrap()
    })
}

/// The game's name from a Lutris wrapper command line.
///
/// The colon form is tried FIRST and matters most: `lutris-wrapper` calls
/// `setproctitle("lutris-wrapper: " + title)`, so that is what a RUNNING game
/// looks like. Missing it cost every Lutris game its launcher score.
///
/// The argv form allows a two-word title but stops at a digit, because the
/// include/exclude counts follow the title and would otherwise be swallowed
/// into the name.
pub fn lutris_name_from_cmd(cmd: &str) -> Option<String> {
    for re in [lutris_title_re(), lutris_argv_re()] {
        if let Some(c) = re.captures(cmd) {
            let name = c[1].trim().trim_matches('"').trim_matches('\'').trim();
            if !name.is_empty() {
                return Some(name.to_owned());
            }
        }
    }
    None
}

fn heroic_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\bheroic\b|legendary --|gogdl |/nile ").unwrap())
}

/// Score one process. `None` means "not a game".
///
/// `steam_app_name` is the title looked up from the appmanifest, if the caller
/// managed to read one.
pub fn score(
    name: &str,
    exe: &str,
    cmd: &str,
    signals: Signals,
    steam_app_name: Option<&str>,
) -> Option<Scored> {
    let base = {
        let b = win_basename(exe);
        if b.is_empty() {
            name.to_owned()
        } else {
            b
        }
    };
    if blocked(name, &base) {
        return None;
    }

    let mut score = 0;
    let mut source = "generic";
    let mut display = base.clone();
    let mut appid = String::new();

    if let Some(id) = steam_appid_from_cmd(cmd) {
        score += 5;
        source = "steam";
        display = steam_app_name
            .map(str::to_owned)
            .unwrap_or_else(|| format!("Steam app {id}"));
        appid = id;
    }
    if let Some(lname) = lutris_name_from_cmd(cmd) {
        score += 5;
        source = "lutris";
        display = lname;
    }
    if heroic_re().is_match(cmd) {
        score += 4;
        source = "heroic";
    }

    // Scaffolding earns nothing from the GPU or library signals: every Proton
    // prefix runs explorer.exe and services.exe, and they link what the game
    // links. Scoring them would tune the machine for the wrong pid.
    let infra = WINE_INFRA.contains(&base.to_lowercase().as_str())
        || WINE_INFRA.contains(&name.to_lowercase().as_str());
    if !infra {
        // Only ACTIVE rendering counts. Compositors and Xwayland hold a DRM
        // fd too, and level 1 is where they land.
        score += match signals.gpu_load {
            2 => 3,
            _ => 0,
        };
        if signals.links_game_libs {
            score += 2;
        }
    }
    if signals.rss_bytes.is_some_and(|rss| rss > 700 * 1024 * 1024) {
        score += 1;
    }

    if source == "generic" && score < GENERIC_SCORE {
        return None;
    }
    Some(Scored {
        score,
        source: source.to_owned(),
        display_name: display,
        app_id: appid,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sig(gpu: u8, libs: bool, rss_mb: u64) -> Signals {
        Signals {
            gpu_load: gpu,
            links_game_libs: libs,
            rss_bytes: Some(rss_mb * 1024 * 1024),
        }
    }

    // ---- translated from tests/test_gamedetect.py ------------------------

    #[test]
    fn splits_on_both_separators() {
        assert_eq!(win_basename(r"C:\Games\Wow.exe"), "Wow.exe");
        assert_eq!(win_basename("/usr/bin/game"), "game");
    }

    #[test]
    fn strips_the_quotes_a_command_line_carries() {
        assert_eq!(win_basename("\"C:\\x\\Wow.exe\""), "Wow.exe");
        assert_eq!(win_basename("'game'"), "game");
    }

    #[test]
    fn every_blocklist_entry_actually_blocks() {
        // Xorg and Xwayland are written with capitals while the lookup
        // lowercases its input, so both were dead entries and the display
        // server was not on the effective blocklist at all. Spelled both ways
        // here, because a test that spells it the way the table does passes
        // either way.
        for entry in BLOCKLIST {
            assert!(blocked(entry, entry), "{entry}");
            let lower = entry.to_lowercase();
            assert!(blocked(&lower, &lower), "{entry} lowercased");
        }
    }

    #[test]
    fn every_block_stem_actually_blocks() {
        for stem in BLOCK_STEMS {
            let name = format!("{stem}x");
            assert!(blocked(&name, &name), "{stem}");
        }
    }

    #[test]
    fn the_tool_never_detects_itself() {
        assert!(blocked("goblin-mode-pro", "goblin-mode-pro"));
        assert!(blocked("goblin-mode-pro-daemon", "x"));
    }

    #[test]
    fn a_real_game_is_not_blocked() {
        for name in ["Wow.exe", "hl2_linux", "factorio", "cyberpunk2077.exe"] {
            assert!(!blocked(name, name), "{name}");
        }
    }

    #[test]
    fn a_bare_appid_needs_steam_in_the_command_line() {
        assert_eq!(steam_appid_from_cmd("./tool --AppId=730"), None);
        assert_eq!(
            steam_appid_from_cmd("steam-runtime --AppId=730").as_deref(),
            Some("730")
        );
        assert_eq!(
            steam_appid_from_cmd("reaper SteamLaunch AppId=730 -- x").as_deref(),
            Some("730")
        );
    }

    #[test]
    fn a_running_lutris_game_is_recognised() {
        // setproctitle("lutris-wrapper: " + title) - the colon form is what a
        // game actually looks like once it is running.
        assert_eq!(
            lutris_name_from_cmd("lutris-wrapper: Deus Ex").as_deref(),
            Some("Deus Ex")
        );
        assert_eq!(
            lutris_name_from_cmd("/usr/share/lutris/bin/lutris-wrapper Factorio 0 0 /g/f")
                .as_deref(),
            Some("Factorio")
        );
    }

    // ---- the corroboration bar --------------------------------------------

    #[test]
    fn a_generic_process_needs_more_than_any_one_signal() {
        // Rendering is 3, libraries 2, a big RSS 1. None of them reaches 6.
        assert!(score("m", "m", "", sig(2, false, 100), None).is_none());
        assert!(score("m", "m", "", sig(0, true, 100), None).is_none());
        assert!(score("m", "m", "", sig(0, false, 1000), None).is_none());
        assert!(score("m", "m", "", sig(2, true, 1000), None).is_some());
    }

    #[test]
    fn holding_a_drm_fd_alone_scores_nothing() {
        // Compositors and Xwayland land at level 1.
        assert!(score("m", "m", "", sig(1, true, 1000), None).is_none());
    }

    #[test]
    fn scaffolding_earns_no_gpu_or_library_points() {
        for infra in ["explorer.exe", "services.exe", "wineserver", "steam.exe"] {
            assert!(
                score(infra, infra, "", sig(2, true, 1000), None).is_none(),
                "{infra}"
            );
        }
    }

    #[test]
    fn a_launcher_tag_clears_the_bar_alone() {
        let s = score(
            "g",
            "g",
            "reaper SteamLaunch AppId=730 -- x",
            sig(0, false, 1),
            None,
        )
        .unwrap();
        assert_eq!((s.source.as_str(), s.app_id.as_str()), ("steam", "730"));
        assert!(s.score >= GAME_SCORE);
        assert_eq!(s.display_name, "Steam app 730");
    }
}
