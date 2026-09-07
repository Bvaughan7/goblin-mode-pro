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

/// The PCI vendor ids this tool knows how to name.
const GPU_VENDORS: &[(&str, &str)] =
    &[("0x10de", "nvidia"), ("0x1002", "amd"), ("0x8086", "intel")];

/// Which CPU this is, from `/proc/cpuinfo`.
///
/// A substring of the WHOLE file lowercased, rather than a parse: the vendor
/// string appears once per core and the file has no stable shape across
/// architectures, so looking for the name is more robust than looking for the
/// field that should hold it.
pub fn cpu_vendor(cpuinfo: &str) -> &'static str {
    // Not stripped, unlike its neighbours: this is a substring test over the
    // whole file, so leading whitespace cannot change the answer, and a
    // `.trim()` here would be a line no test could ever fail on.
    let blob = cpuinfo.to_lowercase();
    if blob.contains("genuineintel") {
        "intel"
    } else if blob.contains("authenticamd") {
        "amd"
    } else {
        "other"
    }
}

/// The processor's marketing name, capped at eighty CHARACTERS.
///
/// The first `model name` line wins, and the match is case-insensitive
/// because the field is spelled differently on different architectures. The
/// cap is a display cap: these strings run long and this one goes into a
/// status line and a bug report.
pub fn cpu_model(cpuinfo: &str) -> String {
    for line in crate::store::splitlines(cpuinfo.trim()) {
        if line.to_lowercase().starts_with("model name") {
            let Some((_, value)) = line.split_once(':') else {
                continue;
            };
            return value.trim().chars().take(80).collect();
        }
    }
    String::new()
}

/// The cpufreq driver in charge, or the word `none`.
///
/// `none` rather than an empty string: a machine with no cpufreq driver is a
/// real answer (a VM, usually), and an empty field reads as a failed probe.
pub fn cpufreq_driver(cpu_root: &std::path::Path) -> String {
    let text =
        std::fs::read_to_string(cpu_root.join("cpu0/cpufreq/scaling_driver")).unwrap_or_default();
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return "none".to_string();
    }
    trimmed.to_string()
}

/// Which GPU vendors are present, named and sorted.
///
/// Two things here are narrower than they look and both are the Python's.
/// The cards are matched as `card` followed by ONE digit, so a machine with
/// more than ten DRM devices does not report the eleventh. Rare enough to have
/// never come up, and reproduced rather than quietly widened.
///
/// And a vendor this build cannot name is dropped rather than listed as
/// `other`, so a machine with only unknown cards answers `unknown`: one word
/// saying "something is there and I do not know what", instead of a list that
/// looks like a finding.
pub fn gpu_vendors(drm_root: &std::path::Path, nvidia_smi: bool) -> Vec<String> {
    let mut found: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    if let Ok(entries) = std::fs::read_dir(drm_root) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let is_card = name
                .strip_prefix("card")
                .is_some_and(|rest| rest.len() == 1 && rest.chars().all(|c| c.is_ascii_digit()));
            if !is_card {
                continue;
            }
            let vendor = std::fs::read_to_string(entry.path().join("device/vendor"))
                .unwrap_or_default()
                .trim()
                .to_lowercase();
            let named = GPU_VENDORS
                .iter()
                .find(|(id, _)| *id == vendor)
                .map_or("other", |(_, name)| *name);
            found.insert(named.to_string());
        }
    }
    if nvidia_smi {
        found.insert("nvidia".to_string());
    }
    let named: Vec<String> = found.into_iter().filter(|v| v != "other").collect();
    if named.is_empty() {
        return vec!["unknown".to_string()];
    }
    named
}

/// Which handheld this is, from DMI, or nothing.
///
/// Matched on the product name, board name and vendor joined together, so a
/// model that identifies itself in any one of the three is recognised. Valve
/// needs BOTH of its words: `valve` alone is a vendor that also ships other
/// things, and `steam` alone appears on machines that merely have Steam.
pub fn handheld(dmi_root: &std::path::Path) -> Option<&'static str> {
    let field = |name: &str| {
        std::fs::read_to_string(dmi_root.join(name))
            .unwrap_or_default()
            .trim()
            .to_string()
    };
    let board = format!(
        "{} {} {}",
        field("product_name"),
        field("board_name"),
        field("sys_vendor")
    )
    .to_lowercase();

    if board.contains("jupiter")
        || board.contains("galileo")
        || (board.contains("valve") && board.contains("steam"))
    {
        return Some("steamdeck");
    }
    if board.contains("rog ally") || board.contains("rc71") || board.contains("rc72") {
        return Some("rog_ally");
    }
    if board.contains("83e1") || board.contains("legion go") {
        return Some("legion_go");
    }
    if board.contains("aokzoe")
        || board.contains("onexplayer")
        || board.contains("aya neo")
        || board.contains("ayaneo")
    {
        return Some("other_handheld");
    }
    None
}

/// The distribution's id, from `/etc/os-release`.
///
/// The first `ID=` line, unquoted. Anchored on the whole prefix, so that
/// `ID_LIKE=` (which sits right beside it in most of these files, and holds
/// something different) is not mistaken for it.
///
/// The blob is stripped before it is split, because the Python reader strips
/// every file it reads. A leading blank line therefore does not shift the
/// first line's indentation onto the anchor.
pub fn distro_id(os_release: &str) -> String {
    for line in crate::store::splitlines(os_release.trim()) {
        if let Some(value) = line.strip_prefix("ID=") {
            return value.trim().trim_matches('"').to_string();
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- the file-reading probes -----------------------------------------

    fn tree(files: &[(&str, &str)]) -> std::path::PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "gmp-caps-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        for (path, contents) in files {
            let full = root.join(path);
            std::fs::create_dir_all(full.parent().expect("has a parent")).unwrap();
            std::fs::write(full, contents).unwrap();
        }
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn the_vendor_is_read_out_of_the_whole_file() {
        assert_eq!(cpu_vendor("vendor_id\t: GenuineIntel\n"), "intel");
        assert_eq!(cpu_vendor("vendor_id\t: AuthenticAMD\n"), "amd");
        assert_eq!(cpu_vendor("vendor_id\t: Something\n"), "other");
        assert_eq!(cpu_vendor(""), "other");
    }

    #[test]
    fn the_model_name_is_the_first_one_and_is_capped() {
        let cpuinfo = "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7\n\
                       processor\t: 1\nmodel name\t: something else\n";
        assert_eq!(cpu_model(cpuinfo), "Intel(R) Core(TM) i7");
        let long = format!("model name\t: {}\n", "x".repeat(200));
        assert_eq!(cpu_model(&long).chars().count(), 80);
        assert_eq!(cpu_model("no model here"), "");
    }

    #[test]
    fn a_machine_with_no_cpufreq_driver_says_none() {
        // A real answer - usually a VM - and an empty string would read as a
        // probe that failed.
        let root = tree(&[("cpu0/x", "")]);
        assert_eq!(cpufreq_driver(&root), "none");
        std::fs::remove_dir_all(&root).ok();
        let root = tree(&[("cpu0/cpufreq/scaling_driver", "intel_pstate\n")]);
        assert_eq!(cpufreq_driver(&root), "intel_pstate");
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn gpu_vendors_are_named_sorted_and_deduplicated() {
        let root = tree(&[
            ("card0/device/vendor", "0x10de\n"),
            ("card1/device/vendor", "0x8086\n"),
            ("card2/device/vendor", "0x10de\n"),
        ]);
        assert_eq!(gpu_vendors(&root, false), vec!["intel", "nvidia"]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn only_a_single_digit_card_is_a_card() {
        // The Python globs `card[0-9]`. Reproduced rather than widened.
        let root = tree(&[
            ("card0/device/vendor", "0x10de\n"),
            ("card10/device/vendor", "0x1002\n"),
            ("renderD128/device/vendor", "0x1002\n"),
        ]);
        assert_eq!(gpu_vendors(&root, false), vec!["nvidia"]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_machine_of_only_unknown_cards_says_unknown() {
        let root = tree(&[("card0/device/vendor", "0xbeef\n")]);
        assert_eq!(gpu_vendors(&root, false), vec!["unknown"]);
        std::fs::remove_dir_all(&root).ok();
        let empty = tree(&[("nothing", "")]);
        assert_eq!(gpu_vendors(&empty, false), vec!["unknown"]);
        std::fs::remove_dir_all(&empty).ok();
    }

    #[test]
    fn nvidia_smi_counts_even_with_no_card_to_read() {
        // The card can be hidden from sysfs while the driver is loaded.
        let root = tree(&[("nothing", "")]);
        assert_eq!(gpu_vendors(&root, true), vec!["nvidia"]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_handheld_is_recognised_from_any_of_the_three_fields() {
        for (file, value, want) in [
            ("product_name", "Jupiter", "steamdeck"),
            ("board_name", "Galileo", "steamdeck"),
            ("product_name", "ROG Ally RC71L", "rog_ally"),
            ("board_name", "RC72LA", "rog_ally"),
            ("product_name", "83E1", "legion_go"),
            ("product_name", "Legion Go", "legion_go"),
            ("product_name", "AOKZOE A1", "other_handheld"),
            ("product_name", "ONEXPLAYER 2", "other_handheld"),
            ("product_name", "AYANEO 2S", "other_handheld"),
            ("product_name", "AYA NEO FOUNDER", "other_handheld"),
        ] {
            let root = tree(&[(file, value)]);
            assert_eq!(handheld(&root), Some(want), "{value}");
            std::fs::remove_dir_all(&root).ok();
        }
    }

    #[test]
    fn valve_needs_both_of_its_words() {
        // `valve` alone is a vendor that ships other things; `steam` alone
        // appears on machines that merely have Steam installed.
        let root = tree(&[("sys_vendor", "Valve")]);
        assert_eq!(handheld(&root), None);
        std::fs::remove_dir_all(&root).ok();
        let root = tree(&[("product_name", "Steam Machine")]);
        assert_eq!(handheld(&root), None);
        std::fs::remove_dir_all(&root).ok();
        let root = tree(&[("sys_vendor", "Valve"), ("product_name", "Steam Deck")]);
        assert_eq!(handheld(&root), Some("steamdeck"));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn an_ordinary_desktop_is_not_a_handheld() {
        let root = tree(&[
            ("product_name", "XPS 15"),
            ("board_name", "0ABCDE"),
            ("sys_vendor", "Dell Inc."),
        ]);
        assert_eq!(handheld(&root), None);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn the_whole_blob_is_stripped_first_the_way_the_python_reader_does() {
        // `_read` strips, so the first line of a file that opens with a blank
        // line is the first line WITH CONTENT, not an empty one.
        assert_eq!(distro_id("\n\nID=arch\n\n"), "arch");
        assert_eq!(cpu_model("\n  model name\t: Ryzen\n"), "Ryzen");
    }

    #[test]
    fn the_distro_id_is_unquoted_and_is_not_id_like() {
        assert_eq!(
            distro_id("NAME=\"Arch\"\nID=arch\nID_LIKE=archlinux\n"),
            "arch"
        );
        assert_eq!(distro_id("ID_LIKE=debian\nID=\"ubuntu\"\n"), "ubuntu");
        assert_eq!(distro_id("ID=cachyos"), "cachyos");
        assert_eq!(distro_id("NAME=nothing\n"), "");
        assert_eq!(distro_id(""), "");
    }

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
