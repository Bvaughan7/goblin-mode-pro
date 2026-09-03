//! What the machine can do, and what to tell the user to install.
//!
//! A port of the pure slice of `src/goblinmode/capabilities.py`. Most of that
//! module probes the system - cpufreq drivers, DMI, hwmon, sched_ext, the
//! compositor - and stays in Python until the layer that owns system access
//! moves. What is here is the part that turns what was found into words: cpu
//! lists, install commands and kernel advice.
//!
//! THE TABLES ARE USER-FACING. Every string below is something a person is
//! invited to paste into a root shell, so they are carried across verbatim and
//! compared against the Python ones character for character.

use regex::Regex;
use std::sync::OnceLock;

/// package manager -> install command template.
const INSTALL_CMD: &[(&str, &str)] = &[
    (r"apt", r"sudo apt install {pkgs}"),
    (r"dnf", r"sudo dnf install {pkgs}"),
    (r"emerge", r"sudo emerge {pkgs}"),
    (r"eopkg", r"sudo eopkg install {pkgs}"),
    (r"pacman", r"sudo pacman -S --needed {pkgs}"),
    (r"xbps-install", r"sudo xbps-install {pkgs}"),
    (r"zypper", r"sudo zypper install {pkgs}"),
];

/// (package, package manager, the name it goes by there). Most distros agree;
/// these are the ones that do not.
const PKG_NAMES: &[(&str, &str, &str)] = &[
    (r"gamemode", r"emerge", r"games-util/gamemode"),
    (r"mangohud", r"emerge", r"games-util/mangohud"),
    (r"mangohud", r"xbps-install", r"MangoHud"),
];

/// distro -> (why, command) for a gaming-tuned kernel.
///
/// An empty pair means the distro's stock kernel is already fine - CachyOS
/// ships one - and the caller should say nothing rather than invent advice.
const KERNEL_TIPS: &[(&str, &str, &str)] = &[
    (
        r"arch",
        r"linux-zen is in the official repos and helps with stutter",
        r"sudo pacman -S linux-zen linux-zen-headers",
    ),
    (r"cachyos", r"", r""),
    (
        r"debian",
        r"A gaming-tuned kernel smooths out frame pacing",
        r"curl -s 'https://liquorix.net/install-liquorix.sh' | sudo bash",
    ),
    (
        r"fedora",
        r"A gaming-tuned kernel smooths out frame pacing",
        r"sudo dnf copr enable bieszczaders/kernel-cachyos && sudo dnf install kernel-cachyos",
    ),
    (
        r"manjaro",
        r"A -zen or -rt kernel helps with stutter",
        r"sudo mhwd-kernel -i linux-zen",
    ),
    (
        r"pop",
        r"A gaming-tuned kernel smooths out frame pacing",
        r"sudo apt install linux-xanmod-x64v3   # after adding the XanMod PPA",
    ),
    (
        r"ubuntu",
        r"A gaming-tuned kernel smooths out frame pacing",
        r"sudo add-apt-repository ppa:xanmod/stable && sudo apt update && sudo apt install linux-xanmod-x64v3",
    ),
];

/// Expand a Linux cpu-list ("0-3,8,10-11") into a sorted list.
///
/// Malformed parts are SKIPPED rather than raising. This parses kernel-exposed
/// text on a machine the author has never seen, and one unexpected token
/// should not cost the whole layout.
pub fn parse_cpu_list(spec: &str) -> Vec<u32> {
    let mut out: std::collections::BTreeSet<u32> = std::collections::BTreeSet::new();
    for part in spec.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if let Some((a, b)) = part.split_once('-') {
            // Trim each end: Python's int() accepts surrounding whitespace and
            // Rust's parse() does not, so "0 - 3" is a range there and an
            // error here. A general trap for this conversion, not a local one.
            //
            // A reversed range yields nothing, matching Python's range().
            if let (Ok(a), Ok(b)) = (a.trim().parse::<u32>(), b.trim().parse::<u32>()) {
                for cpu in a..=b {
                    out.insert(cpu);
                }
            }
            continue;
        }
        if let Ok(cpu) = part.trim().parse::<u32>() {
            out.insert(cpu);
        }
    }
    out.into_iter().collect()
}

/// A copy-pasteable install command, or None if the manager is unknown.
///
/// Never executed here - it is handed to the user to run themselves, which is
/// why an unknown manager returns nothing rather than guessing at a syntax.
pub fn install_command(package_manager: &str, pkgs: &[&str]) -> Option<String> {
    if pkgs.is_empty() {
        return None;
    }
    let tmpl = INSTALL_CMD
        .iter()
        .find(|(pm, _)| *pm == package_manager)
        .map(|(_, t)| *t)?;
    let names: Vec<&str> = pkgs
        .iter()
        .map(|p| {
            PKG_NAMES
                .iter()
                .find(|(pkg, pm, _)| pkg == p && *pm == package_manager)
                .map_or(*p, |(_, _, name)| *name)
        })
        .collect();
    Some(tmpl.replace("{pkgs}", &names.join(" ")))
}

/// `(why, command)` for a gaming-tuned kernel on `distro`.
///
/// An unknown distro gets generic advice and NO command, because a command for
/// the wrong distro is worse than none.
pub fn kernel_upgrade_tip(distro: &str) -> (String, String) {
    KERNEL_TIPS
        .iter()
        .find(|(d, _, _)| *d == distro)
        .map_or_else(
            || {
                (
                    "A gaming-tuned kernel (Zen / XanMod / CachyOS) helps with stutter".to_owned(),
                    String::new(),
                )
            },
            |(_, why, cmd)| ((*why).to_owned(), (*cmd).to_owned()),
        )
}

fn pad_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        regex::RegexBuilder::new(
            r"gamepad|controller|x-?box|dualshock|dualsense|joy-?con|joystick|steam ?(deck )?controller|ally|8bitdo",
        )
        .case_insensitive(true)
        .build()
        .unwrap()
    })
}

fn name_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r#"N: Name="([^"]+)""#).unwrap())
}

fn handlers_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"H: Handlers=([^\n]+)").unwrap())
}

fn js_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\bjs\d").unwrap())
}

/// Controller names out of `/proc/bus/input/devices`.
///
/// Takes the blob rather than reading it, so it can be tested against a
/// captured one. A kernel joystick handler (`jsN`) is the reliable signal;
/// the name pattern is the fallback for pads the kernel does not expose that
/// way. Order is first-seen and duplicates are dropped.
pub fn controllers_from_blob(blob: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for block in blob.split("\n\n") {
        let Some(name) = name_re().captures(block).map(|c| c[1].to_owned()) else {
            continue;
        };
        let is_js = handlers_re()
            .captures(block)
            .is_some_and(|c| js_re().is_match(&c[1]));
        if (is_js || pad_re().is_match(&name)) && !out.contains(&name) {
            out.push(name);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expands_a_cpu_list() {
        assert_eq!(parse_cpu_list("0-3,8,10-11"), vec![0, 1, 2, 3, 8, 10, 11]);
        assert_eq!(parse_cpu_list("0"), vec![0]);
        assert_eq!(parse_cpu_list(""), Vec::<u32>::new());
    }

    #[test]
    fn a_malformed_part_is_skipped_not_fatal() {
        // This parses kernel-exposed text on machines the author has never
        // seen; one unexpected token must not cost the whole layout.
        assert_eq!(parse_cpu_list("0-3,x,5"), vec![0, 1, 2, 3, 5]);
        assert_eq!(parse_cpu_list("a-b"), Vec::<u32>::new());
        assert_eq!(parse_cpu_list("0-3,,5"), vec![0, 1, 2, 3, 5]);
    }

    #[test]
    fn whitespace_around_a_number_is_tolerated() {
        // Python's int() accepts it; Rust's parse() does not, so the parts are
        // trimmed. Caught by the parity test, not by reading.
        assert_eq!(parse_cpu_list(" 0 - 3 "), vec![0, 1, 2, 3]);
    }

    #[test]
    fn a_reversed_range_yields_nothing() {
        assert_eq!(parse_cpu_list("3-0"), Vec::<u32>::new());
    }

    #[test]
    fn overlapping_ranges_are_deduplicated_and_sorted() {
        assert_eq!(
            parse_cpu_list("10-11,0-3,2-5"),
            vec![0, 1, 2, 3, 4, 5, 10, 11]
        );
    }

    #[test]
    fn an_unknown_package_manager_gets_no_command() {
        // Guessing at a syntax would hand the user something that fails, or
        // worse, does something else.
        assert_eq!(install_command("brew", &["mangohud"]), None);
        assert_eq!(install_command("", &["mangohud"]), None);
        assert_eq!(install_command("pacman", &[]), None);
    }

    #[test]
    fn a_package_is_renamed_only_where_it_differs() {
        assert_eq!(
            install_command("pacman", &["mangohud"]).unwrap(),
            "sudo pacman -S --needed mangohud"
        );
        assert_eq!(
            install_command("xbps-install", &["mangohud"]).unwrap(),
            "sudo xbps-install MangoHud"
        );
        assert_eq!(
            install_command("emerge", &["gamemode"]).unwrap(),
            "sudo emerge games-util/gamemode"
        );
    }

    #[test]
    fn cachyos_is_told_nothing_because_its_kernel_is_already_tuned() {
        assert_eq!(
            kernel_upgrade_tip("cachyos"),
            (String::new(), String::new())
        );
    }

    #[test]
    fn an_unknown_distro_gets_advice_but_no_command() {
        // A command for the wrong distro is worse than no command.
        let (why, cmd) = kernel_upgrade_tip("slackware");
        assert!(!why.is_empty());
        assert!(cmd.is_empty());
    }

    #[test]
    fn a_joystick_handler_is_the_reliable_signal() {
        let blob = "N: Name=\"Some Odd Pad\"\nH: Handlers=js0 event3\n";
        assert_eq!(controllers_from_blob(blob), vec!["Some Odd Pad"]);
    }

    #[test]
    fn a_mouse_is_not_a_controller() {
        let blob = "N: Name=\"Razer DeathAdder\"\nH: Handlers=mouse0 event4\n";
        assert!(controllers_from_blob(blob).is_empty());
    }

    #[test]
    fn the_same_pad_twice_is_listed_once() {
        let blob = "N: Name=\"X-Box 360 pad\"\nH: Handlers=js0\n\n\
                    N: Name=\"X-Box 360 pad\"\nH: Handlers=js1\n";
        assert_eq!(controllers_from_blob(blob).len(), 1);
    }
}
