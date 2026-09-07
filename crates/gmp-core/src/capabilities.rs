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

/// Kernels that get named, in the order they are looked for.
///
/// Order is a decision, not an accident: a kernel called `6.6-lts-zen` is a
/// zen kernel that happens to be the LTS base, and the tag list is checked
/// before the `-lts` rule so it is named for the part that changes how it
/// behaves.
const KERNEL_FLAVORS: &[&str] = &[
    "cachyos", "xanmod", "liquorix", "lqx", "zen", "tkg", "nobara", "bazzite", "clear", "xero",
];

/// Package managers, in the order they are tried, with the name reported for
/// each. Only `apt-get` answers to a different name than it is found by.
const PACKAGE_MANAGERS: &[(&str, &str)] = &[
    ("pacman", "pacman"),
    ("apt-get", "apt"),
    ("dnf", "dnf"),
    ("zypper", "zypper"),
    ("xbps-install", "xbps-install"),
    ("eopkg", "eopkg"),
    ("emerge", "emerge"),
];

/// Screen recorders, best first.
const RECORDERS: &[&str] = &["gpu-screen-recorder", "wf-recorder", "obs", "spectacle"];

/// A rough classification of the running kernel.
///
/// Gaming-oriented builds get named and everything else is `generic`. This
/// feeds a gentle upgrade nudge and nothing else, so a kernel named wrongly
/// costs a suggestion the user did not need, never a tweak.
pub fn kernel_flavor(release: &str) -> String {
    let rel = release.to_lowercase();
    for tag in KERNEL_FLAVORS {
        if rel.contains(tag) {
            // Liquorix packages itself under both names and reports the short
            // one, so the two spellings collapse here rather than downstream.
            return if *tag == "liquorix" { "lqx" } else { tag }.to_string();
        }
    }
    if rel.contains("-lts") {
        return "lts".to_string();
    }
    "generic".to_string()
}

/// Which compositor is running, from the session's own environment.
///
/// KDE and GNOME each answer twice, because what a tweak has to do to them
/// differs between X11 and Wayland. The desktop name is matched as an
/// uppercased SUBSTRING, which is what makes `KDE:Plasma` and `ubuntu:GNOME`
/// - both of them real, both shipped by distributions - resolve at all.
///
/// The wlroots pair are found by their own variables and only when the
/// desktop name is not one of the two above; and a variable set to the empty
/// string does not count, which is the difference between asking whether it
/// is SET and asking what it says.
pub fn compositor(env: &dyn Fn(&str) -> String) -> String {
    let desktop = env("XDG_CURRENT_DESKTOP").to_uppercase();
    let session = env("XDG_SESSION_TYPE");
    if desktop.contains("KDE") {
        return if session == "wayland" {
            "kwin-wayland"
        } else {
            "kwin-x11"
        }
        .to_string();
    }
    if desktop.contains("GNOME") {
        return if session == "wayland" {
            "mutter-wayland"
        } else {
            "mutter-x11"
        }
        .to_string();
    }
    if !env("HYPRLAND_INSTANCE_SIGNATURE").is_empty() {
        return "hyprland".to_string();
    }
    if !env("SWAYSOCK").is_empty() {
        return "sway".to_string();
    }
    if session.is_empty() {
        return "unknown".to_string();
    }
    session
}

/// Which package manager this machine has, or nothing.
///
/// Used only to hand the user a copy-pasteable install line - see
/// [`install_command`], which never runs anything itself.
pub fn package_manager(have: &dyn Fn(&str) -> bool) -> Option<&'static str> {
    PACKAGE_MANAGERS
        .iter()
        .find(|(tool, _)| have(tool))
        .map(|(_, name)| *name)
}

/// Which screen recorder to offer the clip feature through, or nothing.
pub fn session_recorder(have: &dyn Fn(&str) -> bool) -> Option<&'static str> {
    RECORDERS.iter().copied().find(|tool| have(tool))
}

/// Whether any hwmon exposes the standard `pwmN` + `pwmN_enable` pair.
///
/// Existence only, never a write test, so this is safe to call unprivileged.
/// Most laptops and handhelds answer no: the EC or the firmware owns the fan
/// curve and does not hand it over.
///
/// The pair has to live in the SAME hwmon, and the control file has to be
/// `pwm` followed by digits and nothing else - `pwm1_enable` is itself a file
/// whose name begins `pwm` and a digit, so without that the enable file would
/// happily stand in for the control file it is supposed to accompany.
pub fn has_writable_pwm(hwmon_root: &std::path::Path) -> bool {
    let Ok(hwmons) = std::fs::read_dir(hwmon_root) else {
        return false;
    };
    for hwmon in hwmons.flatten() {
        if !hwmon.file_name().to_string_lossy().starts_with("hwmon") {
            continue;
        }
        let Ok(files) = std::fs::read_dir(hwmon.path()) else {
            continue;
        };
        for file in files.flatten() {
            let name = file.file_name().to_string_lossy().into_owned();
            if !is_pwm_control(&name) {
                continue;
            }
            if hwmon.path().join(format!("{name}_enable")).exists() {
                return true;
            }
        }
    }
    false
}

/// `pwm` followed by one or more digits and nothing else.
///
/// Two different digit rules, both the Python's and neither one redundant:
/// the glob that finds these files insists the FIRST digit is ASCII, and the
/// pattern that then checks the name accepts any Unicode decimal digit for
/// the rest. No kernel names a file this way; it is copied because a probe
/// that reads a slightly different set of files than the shipped one is
/// exactly the kind of difference that never announces itself.
fn is_pwm_control(name: &str) -> bool {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"^pwm[0-9][\p{Nd}]*$").expect("valid"));
    re.is_match(name)
}

/// `true` on mains, `false` on battery, `None` when there is nothing to ask.
///
/// `None` is the answer on most desktops, and it is not the same as "on
/// battery": nothing downstream should drop to a handheld's battery preset
/// because a machine has no power supply to report on.
///
/// Deliberately uncached - unlike the rest of this module, this changes at
/// plug and unplug rather than once at process start.
pub fn on_ac_power(supply_root: &std::path::Path) -> Option<bool> {
    let read = |path: std::path::PathBuf| {
        std::fs::read_to_string(path)
            .unwrap_or_default()
            .trim()
            .to_string()
    };
    let Ok(entries) = std::fs::read_dir(supply_root) else {
        return None;
    };
    // Sorted, where the Python takes the directory in whatever order the
    // kernel hands it over. It can only matter on a machine with two mains
    // supplies that disagree, which is not a machine that exists - but an
    // answer that depends on readdir order is not one worth having either.
    let mut supplies: Vec<std::path::PathBuf> = entries.flatten().map(|e| e.path()).collect();
    supplies.sort();
    for supply in supplies {
        if read(supply.join("type")) != "Mains" {
            continue;
        }
        let online = read(supply.join("online"));
        if !online.is_empty() {
            return Some(online == "1");
        }
    }
    None
}

/// The one file whose existence means the CPU power limit can be raised.
const RAPL_LIMIT: &str = "intel-rapl/intel-rapl:0/constraint_0_power_limit_uw";

/// Everything a capability snapshot needs to know about a machine.
///
/// Every root is an argument and both lookups are closures, which is what
/// lets one desktop be graded against a Steam Deck, an AMD laptop with no
/// RAPL and a VM with no cpufreq driver at all. The alternative - reading
/// `/sys` directly - can only ever confirm the machine the test runs on.
pub struct Machine<'a> {
    pub cpuinfo: &'a str,
    pub os_release: &'a str,
    pub kernel_release: &'a str,
    pub cpu_root: &'a std::path::Path,
    pub dmi_root: &'a std::path::Path,
    pub drm_root: &'a std::path::Path,
    pub hwmon_root: &'a std::path::Path,
    pub powercap_root: &'a std::path::Path,
    pub sched_ext_sysfs: &'a std::path::Path,
    pub vkbasalt_layer: &'a std::path::Path,
    pub scx_bin_dirs: &'a [std::path::PathBuf],
    /// Whether a named tool is on `$PATH`.
    pub have: &'a dyn Fn(&str) -> bool,
    /// An environment variable, or the empty string.
    pub env: &'a dyn Fn(&str) -> String,
}

/// Everything this machine can do, as one map.
///
/// This goes onto the daemon status, and the GUI hides the features it says
/// are missing rather than letting them fail silently. So a field that is
/// wrong here is a control that appears on a machine that cannot honour it,
/// or vanishes from one that can - which is why the whole map is graded
/// against the Python rather than the interesting fields.
///
/// Nothing here is privileged and nothing here writes: every question is
/// asked by reading a file's name, or by looking for a tool on `$PATH`.
pub fn detect(m: &Machine) -> serde_json::Map<String, serde_json::Value> {
    use serde_json::{json, Value};

    let vendor = cpu_vendor(m.cpuinfo);
    let nvidia_smi = (m.have)("nvidia-smi");
    let gpus = gpu_vendors(m.drm_root, nvidia_smi);
    let ryzenadj = (m.have)("ryzenadj");

    // Two existence tests that behave differently, and both are the
    // Python's. The EPP and governor files are found with a glob, which
    // reports a name that is THERE; the RAPL file is found with `exists`,
    // which reports a name that RESOLVES. A dangling symlink is therefore an
    // EPP that is present and a RAPL that is not - a distinction no real
    // sysfs makes, kept because a probe that reads a slightly different set
    // of files than the shipped one never announces itself.
    let listed = |path: std::path::PathBuf| path.symlink_metadata().is_ok();
    let epp = listed(
        m.cpu_root
            .join("cpu0/cpufreq/energy_performance_preference"),
    );
    let governor = listed(m.cpu_root.join("cpu0/cpufreq/scaling_governor"));
    let rapl = m.powercap_root.join(RAPL_LIMIT).exists();

    // sched_ext is a kernel feature plus a userspace loader, and the list of
    // installed schedulers is only read when both are there. That is not an
    // optimisation: it keeps a machine that has never heard of sched_ext
    // from having its /usr/bin walked by a capability probe.
    let scx_kernel = m.sched_ext_sysfs.is_dir();
    let scx_loader = (m.have)("scx_loader");
    let schedulers = if scx_kernel && scx_loader {
        crate::scx::scheduler_binaries(m.scx_bin_dirs)
    } else {
        Vec::new()
    };

    let mut out = serde_json::Map::new();
    let mut put = |key: &str, value: Value| {
        out.insert(key.to_string(), value);
    };
    put("cpu_vendor", json!(vendor));
    put("cpu_model", json!(cpu_model(m.cpuinfo)));
    put("cpufreq_driver", json!(cpufreq_driver(m.cpu_root)));
    put("governor_control", json!(governor));
    put("epp_control", json!(epp));
    put("rapl_control", json!(rapl));
    put("ryzenadj", json!(ryzenadj));
    // RAPL first: it is the kernel's own interface and needs no extra tool.
    // ryzenadj is the fallback, and it is chosen on the TOOL rather than on
    // the CPU vendor - an Intel machine with no RAPL and ryzenadj installed
    // reports ryzenadj, which is what the Python does.
    put(
        "tdp_control",
        match (rapl, ryzenadj) {
            (true, _) => json!("rapl"),
            (false, true) => json!("ryzenadj"),
            (false, false) => Value::Null,
        },
    );
    put("gpu_vendors", json!(gpus));
    put("nvidia_smi", json!(nvidia_smi));
    // A conjunction, not either half: the deep GPU snapshot needs an NVIDIA
    // card AND the tool that reads it.
    put(
        "gpu_deep_stats",
        json!(gpus.iter().any(|v| v == "nvidia") && nvidia_smi),
    );
    put("gamescope", json!((m.have)("gamescope")));
    put("gamemode", json!((m.have)("gamemoderun")));
    put("mangohud", json!((m.have)("mangohud")));
    put("compositor", json!(compositor(m.env)));
    put("distro_id", json!(distro_id(m.os_release)));
    put("package_manager", json!(package_manager(m.have)));
    put(
        "core_layout",
        Value::Object(crate::cpulayout::core_layout(m.cpu_root)),
    );
    put("kernel_release", json!(m.kernel_release));
    put("kernel_flavor", json!(kernel_flavor(m.kernel_release)));
    put("handheld", json!(handheld(m.dmi_root)));
    // Both undervolt fields are gated on the CPU vendor as well as on the
    // tool, so an AMD machine with intel-undervolt installed still reports
    // nothing for `undervolt`. Two fields rather than one because they are
    // different tools with different risks, and the GUI labels them apart.
    put(
        "undervolt",
        if vendor == "intel" && (m.have)("intel-undervolt") {
            json!("intel-undervolt")
        } else {
            Value::Null
        },
    );
    put(
        "amd_undervolt",
        if vendor == "amd" && ryzenadj {
            json!("ryzenadj")
        } else {
            Value::Null
        },
    );
    put("fan_control", json!(has_writable_pwm(m.hwmon_root)));
    put(
        "sched_ext",
        json!({
            "kernel": scx_kernel,
            "loader": scx_loader,
            "available": scx_kernel && scx_loader,
            "schedulers": schedulers,
        }),
    );
    put("session_recorder", json!(session_recorder(m.have)));
    // Either the binary or the implicit layer file: vkBasalt is usable
    // through the layer alone, with nothing on $PATH to find.
    put(
        "vkbasalt",
        json!((m.have)("vkBasalt") || m.vkbasalt_layer.exists()),
    );
    out
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
