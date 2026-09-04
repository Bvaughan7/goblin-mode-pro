//! Wine / Proton log rule base - known Linux-gaming failure patterns.
//!
//! A port of `src/goblinmode/logrules.py`. Each rule maps a regex to a
//! plain-language diagnosis and a fix, and is used two ways: the live stderr
//! tail raises an incident on the rules flagged `live`, and `analyze_text`
//! scans a whole captured log for the bug report and the Diagnostics action.
//!
//! THE RULE TABLE IS THE SPECIFICATION. Every pattern, label, cause and fix
//! here was generated mechanically from the Python table rather than retyped,
//! because a regex that differs by one character is a rule that silently stops
//! matching and nothing downstream would notice.

use std::sync::OnceLock;

use regex::{Regex, RegexBuilder};

/// One known failure pattern.
///
/// `category` is one of gpu | memory | anticheat | runtime | deps | crash |
/// config, and `severity` is warn | error. Both are plain strings rather than
/// enums so the port stays a translation: the Python side puts these straight
/// into a JSON payload, and an enum here would invite a different spelling.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rule {
    pub id: &'static str,
    pub pattern: &'static str,
    pub label: &'static str,
    pub category: &'static str,
    pub cause: &'static str,
    pub fix: &'static str,
    /// Also watched on the live stderr for incidents.
    pub live: bool,
    pub severity: &'static str,
    /// A runnable remedy; may contain `{appid}`.
    pub fix_cmd: Option<&'static str>,
}

pub const RULES: &[Rule] = &[
    Rule {
        id: r"esync_fd",
        pattern: r"esync:.*up to \d+|eventfd: Too many open files|pipe\(\) failed.*Too many",
        label: r"esync ran out of file descriptors",
        category: r"config",
        cause: r"The open-file hard limit is too low for esync.",
        fix: r"Pre-flight -> raise the open-file limit (524288), or set PROTON_NO_ESYNC=1 for this game.",
        live: false,
        severity: r"error",
        fix_cmd: None,
    },
    Rule {
        id: r"fsync_unsupported",
        pattern: r"fsync: warning|FUTEX_WAIT_MULTIPLE.*not|futex_waitv.*ENOSYS",
        label: r"Kernel fsync not available",
        category: r"config",
        cause: r"This kernel lacks futex_waitv; Proton falls back to esync (slower).",
        fix: r"Update to a kernel >= 5.16 (CachyOS ships current).",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"vram_oom",
        pattern: r"VK_ERROR_OUT_OF_DEVICE_MEMORY|DXVK:.*Failed to allocate|CUDA.*out of memory|Failed to allocate .* device memory",
        label: r"GPU ran out of video memory",
        category: r"memory",
        cause: r"VRAM exhausted - the driver spills to system RAM over PCIe (huge stalls) or the game crashes.",
        fix: r"Lower texture quality / resolution; close other GPU apps; try DXVK's gplasync.",
        live: true,
        severity: r"error",
        fix_cmd: None,
    },
    Rule {
        id: r"device_lost",
        pattern: r"VK_ERROR_DEVICE_LOST|vkQueueSubmit.*DEVICE_LOST|VKD3D.*(Device|Driver)\s+lost|D3D12.*device removed",
        label: r"GPU device lost",
        category: r"gpu",
        cause: r"The GPU stopped responding - driver bug, an unstable overclock/undervolt, overheating, or a VKD3D/DXVK issue.",
        fix: r"Reset any GPU OC; update the driver; check temps; try a different Proton (GE) build.",
        live: true,
        severity: r"error",
        fix_cmd: None,
    },
    Rule {
        id: r"host_oom",
        pattern: r"std::bad_alloc|Out of memory|Oom|cannot allocate memory|MADV_.*failed",
        label: r"Out of system memory",
        category: r"memory",
        cause: r"System RAM exhausted.",
        fix: r"Enable zram/swap; close background apps; check for a memory leak in the game/Proton build.",
        live: true,
        severity: r"error",
        fix_cmd: None,
    },
    Rule {
        id: r"anticheat",
        pattern: r"(EasyAntiCheat|EAC|BattlEye).*(not|unsupported|failed to (init|load))|AntiCheat.*Linux",
        label: r"Anti-cheat not initialising",
        category: r"anticheat",
        cause: r"The game's anti-cheat isn't starting - usually the Linux/Proton path isn't enabled for the title.",
        fix: r"Check areweanticheatyet.com; in Steam enable the Proton EAC/BattlEye runtime; some titles block Linux entirely.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"wine_mono",
        pattern: r"wine: failed to load l?mscoree|Mono.*not installed|wine-mono",
        label: r"wine-mono (.NET) missing",
        category: r"deps",
        cause: r"The prefix has no .NET runtime.",
        fix: r"Let Proton install wine-mono (delete and recreate the prefix), or install it with protontricks.",
        live: false,
        severity: r"warn",
        fix_cmd: Some(r"protontricks {appid} mono"),
    },
    Rule {
        id: r"vcrun",
        pattern: r"err:module:.*MSVC[PR]\d|api-ms-win-crt|vcruntime\d+\.dll.*not found",
        label: r"Visual C++ runtime missing",
        category: r"deps",
        cause: r"The game needs a Microsoft VC++ redistributable that isn't in the prefix.",
        fix: r"protontricks <appid> vcrun2022 (or the version the game bundles).",
        live: false,
        severity: r"warn",
        fix_cmd: Some(r"protontricks {appid} vcrun2022"),
    },
    Rule {
        id: r"dxvk_d3d",
        pattern: r"d3d11: Direct3D 11 is not supported|D3D_FEATURE_LEVEL.*fail|Failed to create D3D(9|11) device",
        label: r"Direct3D device creation failed",
        category: r"gpu",
        cause: r"DXVK couldn't create the D3D device - Vulkan driver missing in the prefix, or a feature-level mismatch.",
        fix: r"Verify a Vulkan ICD is installed; try Proton Experimental; check DXVK_HUD=1 loads.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"vulkan_loader",
        pattern: r"Failed to load vulkan|vulkan-1\.dll.*not found|winevulkan.*not|No Vulkan.*ICD|ErrorIncompatibleDriver",
        label: r"Vulkan not available to the game",
        category: r"gpu",
        cause: r"The Vulkan loader or ICD isn't reachable.",
        fix: r"Install the vulkan driver for your GPU (pre-flight checks this); reinstall the Proton prefix.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"shader_cache",
        pattern: r"Shader cache.*disabled|DISK_CACHE.*(failed|read-only)|__GL_SHADER_DISK_CACHE.*denied",
        label: r"Shader disk cache not writable",
        category: r"config",
        cause: r"Shaders can't be cached to disk -> constant recompilation stutter.",
        fix: r"Point __GL_SHADER_DISK_CACHE_PATH / DXVK_STATE_CACHE_PATH at a writable dir with space.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"pressure_vessel",
        pattern: r"pressure-vessel.*(error|failed)|pv-bwrap.*failed|steam-runtime.*cannot",
        label: r"Steam Linux Runtime container failed",
        category: r"runtime",
        cause: r"The pressure-vessel sandbox couldn't start.",
        fix: r"Verify 'Steam Linux Runtime' is installed; try 'Runtime: Legacy' or force a Proton version.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"page_fault",
        pattern: r"wine: Unhandled (page fault|exception)|err:seh:|Assertion .* failed|Segmentation fault",
        label: r"The game process crashed",
        category: r"crash",
        cause: r"An unhandled fault in the game or Proton.",
        fix: r"Note the module in the log; search ProtonDB for the title + that module; try another Proton build.",
        live: true,
        severity: r"error",
        fix_cmd: None,
    },
    Rule {
        id: r"amdgpu_reset",
        pattern: r"amdgpu.*(ring .* timeout|GPU reset|GPU fault)|\[drm\].*reset",
        label: r"amdgpu hang / reset",
        category: r"gpu",
        cause: r"The AMD GPU hung and was reset.",
        fix: r"Try mesa-git; check for a known regression; disable any GPU OC; add amdgpu.gpu_recovery=1.",
        live: true,
        severity: r"error",
        fix_cmd: None,
    },
    Rule {
        id: r"nvidia_xid",
        pattern: r"NVRM: Xid.*: (\d+)|nvidia.*Xid",
        label: r"NVIDIA Xid error",
        category: r"gpu",
        cause: r"The NVIDIA kernel driver logged a hardware/driver fault (Xid).",
        fix: r"Look up the Xid code; common ones mean OC instability, bad power, or overheating.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
    Rule {
        id: r"gl_mismatch",
        pattern: r"libGL error|MESA-INTEL:.*not supported|GLX.*Bad(Value|Match)|failed to load driver: (i965|iris|radeonsi)",
        label: r"OpenGL driver / loader problem",
        category: r"gpu",
        cause: r"The GL driver failed to load - often a 32-bit lib missing or a driver mismatch.",
        fix: r"Install the 32-bit GL/vulkan driver (lib32-*); ensure host and container drivers match.",
        live: false,
        severity: r"warn",
        fix_cmd: None,
    },
];

/// What a scan found: one per matched rule, never one per matched line.
///
/// The field ORDER is the serialised order, and it matches the Python
/// dataclass, because `AnalyzeLog` returns `f.__dict__` and a dict keeps
/// insertion order. A reordering here is a visible change to that reply.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Finding {
    pub rule_id: String,
    pub label: String,
    pub category: String,
    pub cause: String,
    pub fix: String,
    pub severity: String,
    pub count: usize,
    pub sample: String,
    pub fix_cmd: Option<String>,
}

/// Every rule compiled once. Case-insensitive, matching the Python's `re.I`.
fn compiled() -> &'static [(Regex, &'static Rule)] {
    static COMPILED: OnceLock<Vec<(Regex, &'static Rule)>> = OnceLock::new();
    COMPILED.get_or_init(|| {
        RULES
            .iter()
            .map(|rule| {
                let rx = RegexBuilder::new(rule.pattern)
                    .case_insensitive(true)
                    .build()
                    .unwrap_or_else(|e| panic!("rule {} has an invalid pattern: {e}", rule.id));
                (rx, rule)
            })
            .collect()
    })
}

/// The rules watched on the live stderr, as (regex, label).
pub fn live_patterns() -> Vec<(&'static Regex, &'static str)> {
    compiled()
        .iter()
        .filter(|(_, rule)| rule.live)
        .map(|(rx, rule)| (rx, rule.label))
        .collect()
}

/// A log sample is truncated to this many characters before being stored.
const SAMPLE_LIMIT: usize = 300;

/// Strip the home path and login name from a line before it leaves the machine.
///
/// `home` and `user` are passed in rather than read from the environment so
/// this can be tested against a fixed identity. [`redact`] reads them for you.
///
/// The order is load-bearing: the literal home path goes first, because once
/// `/home/alice` has become `~` the more general patterns cannot see it, and
/// the bare-username pass goes last so it only catches what the path passes
/// missed.
pub fn redact_as(text: &str, home: &str, user: &str) -> String {
    if text.is_empty() {
        return text.to_owned();
    }
    static HOME_PATH: OnceLock<Regex> = OnceLock::new();
    static USERS_PATH: OnceLock<Regex> = OnceLock::new();

    let mut out = text.to_owned();
    if !home.is_empty() && home != "~" {
        out = out.replace(home, "~");
    }
    let home_rx = HOME_PATH.get_or_init(|| Regex::new(r#"/home/[^/\s"']+"#).unwrap());
    out = home_rx.replace_all(&out, "/home/<user>").into_owned();

    // Windows-shaped paths inside a Proton prefix: \users\alice or /users/alice
    let users_rx = USERS_PATH.get_or_init(|| {
        RegexBuilder::new(r#"([\\/])users([\\/])[^\\/\s"']+"#)
            .case_insensitive(true)
            .build()
            .unwrap()
    });
    out = users_rx
        .replace_all(&out, "${1}users${2}<user>")
        .into_owned();

    // A very short login name would match far too much - "al" inside "alias".
    if user.len() > 2 {
        if let Ok(rx) = Regex::new(&format!(r"\b{}\b", regex::escape(user))) {
            out = rx.replace_all(&out, "<user>").into_owned();
        }
    }
    out
}

/// [`redact_as`] with this machine's home directory and login name.
pub fn redact(text: &str) -> String {
    static IDENTITY: OnceLock<(String, String)> = OnceLock::new();
    let (home, user) = IDENTITY.get_or_init(|| {
        (
            std::env::var("HOME").unwrap_or_default(),
            std::env::var("USER").unwrap_or_default(),
        )
    });
    redact_as(text, home, user)
}

/// Scan a whole log; one [`Finding`] per matched rule, most severe first.
///
/// `appid` (a Steam AppID), when known, is substituted into a rule's `fix_cmd`
/// so the remedy is copy-paste ready instead of needing the user to fill in a
/// placeholder.
pub fn analyze_text(text: &str, appid: &str) -> Vec<Finding> {
    let mut hits: Vec<(usize, Vec<String>)> = Vec::new();
    for (index, _) in RULES.iter().enumerate() {
        hits.push((index, Vec::new()));
    }
    for line in text.lines() {
        for (index, (rx, _rule)) in compiled().iter().enumerate() {
            if rx.is_match(line) {
                let trimmed = line.trim();
                hits[index]
                    .1
                    .push(trimmed.chars().take(SAMPLE_LIMIT).collect());
            }
        }
    }

    let mut out: Vec<Finding> = Vec::new();
    for (index, samples) in &hits {
        if samples.is_empty() {
            continue;
        }
        let rule = &RULES[*index];
        out.push(Finding {
            rule_id: rule.id.to_owned(),
            label: rule.label.to_owned(),
            category: rule.category.to_owned(),
            cause: rule.cause.to_owned(),
            fix: rule.fix.to_owned(),
            severity: rule.severity.to_owned(),
            count: samples.len(),
            sample: samples[0].clone(),
            fix_cmd: rule.fix_cmd.map(|template| {
                template.replace("{appid}", if appid.is_empty() { "<appid>" } else { appid })
            }),
        });
    }
    // Errors first, then by category. A stable sort, so rules that tie keep the
    // order of the table - which is the order the Python reports them in.
    out.sort_by(|a, b| {
        (a.severity != "error", &a.category).cmp(&(b.severity != "error", &b.category))
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- translated from tests/test_logrules.py, verbatim ----------------
    // These five are the Python module's own tests. They are translated rather
    // than rewritten: if one of them fails, the two implementations have
    // diverged and the answer is to find out which is right, not to adjust the
    // test until it passes.

    #[test]
    fn matches_a_known_rule() {
        let findings = analyze_text(
            "err:module:import_dll api-ms-win-crt-runtime-l1-1-0.dll not found",
            "",
        );
        assert!(findings.iter().any(|f| f.rule_id == "vcrun"));
    }

    #[test]
    fn fix_cmd_uses_placeholder_without_appid() {
        let findings = analyze_text("vcruntime140.dll not found", "");
        let vcrun = findings.iter().find(|f| f.rule_id == "vcrun").unwrap();
        assert_eq!(
            vcrun.fix_cmd.as_deref(),
            Some("protontricks <appid> vcrun2022")
        );
    }

    #[test]
    fn fix_cmd_substitutes_known_appid() {
        let findings = analyze_text("vcruntime140.dll not found", "123456");
        let vcrun = findings.iter().find(|f| f.rule_id == "vcrun").unwrap();
        assert_eq!(
            vcrun.fix_cmd.as_deref(),
            Some("protontricks 123456 vcrun2022")
        );
    }

    #[test]
    fn rule_without_fix_cmd_stays_none() {
        let findings = analyze_text("std::bad_alloc thrown", "");
        let oom = findings.iter().find(|f| f.rule_id == "host_oom").unwrap();
        assert_eq!(oom.fix_cmd, None);
    }

    #[test]
    fn no_matches_returns_empty() {
        assert!(analyze_text("nothing interesting here", "").is_empty());
    }

    // ---- the rule table itself -------------------------------------------

    #[test]
    fn every_pattern_compiles() {
        // compiled() panics with the rule id on a bad pattern, which is what
        // makes a typo in the table a build-time failure rather than a rule
        // that silently never matches.
        assert_eq!(compiled().len(), RULES.len());
    }

    #[test]
    fn rule_ids_are_unique() {
        let mut ids: Vec<&str> = RULES.iter().map(|r| r.id).collect();
        ids.sort_unstable();
        let before = ids.len();
        ids.dedup();
        assert_eq!(ids.len(), before, "two rules share an id");
    }

    #[test]
    fn every_rule_declares_a_known_category_and_severity() {
        for rule in RULES {
            assert!(
                matches!(
                    rule.category,
                    "gpu" | "memory" | "anticheat" | "runtime" | "deps" | "crash" | "config"
                ),
                "{} has category {}",
                rule.id,
                rule.category
            );
            assert!(
                matches!(rule.severity, "warn" | "error"),
                "{} has severity {}",
                rule.id,
                rule.severity
            );
        }
    }

    #[test]
    fn live_rules_are_the_ones_worth_interrupting_a_game_for() {
        // A live rule raises an incident mid-session. The set is small on
        // purpose: device-lost, out-of-memory and crashes, not configuration
        // advice that can wait until the log is read.
        let live: Vec<&str> = RULES.iter().filter(|r| r.live).map(|r| r.id).collect();
        assert_eq!(
            live,
            [
                "vram_oom",
                "device_lost",
                "host_oom",
                "page_fault",
                "amdgpu_reset"
            ]
        );
        assert_eq!(live_patterns().len(), live.len());
    }

    // ---- behaviour the Python tests do not cover --------------------------

    #[test]
    fn a_rule_reports_once_however_many_lines_matched() {
        let log = "VK_ERROR_DEVICE_LOST\nVK_ERROR_DEVICE_LOST\nVK_ERROR_DEVICE_LOST";
        let findings = analyze_text(log, "");
        let lost = findings
            .iter()
            .find(|f| f.rule_id == "device_lost")
            .unwrap();
        assert_eq!(lost.count, 3, "the count is the number of matching lines");
        assert_eq!(
            findings
                .iter()
                .filter(|f| f.rule_id == "device_lost")
                .count(),
            1
        );
    }

    #[test]
    fn the_sample_is_the_first_match_trimmed() {
        let findings = analyze_text("   std::bad_alloc while loading   ", "");
        let oom = findings.iter().find(|f| f.rule_id == "host_oom").unwrap();
        assert_eq!(oom.sample, "std::bad_alloc while loading");
    }

    #[test]
    fn a_very_long_line_is_truncated_to_the_sample_limit() {
        let long = format!("std::bad_alloc {}", "x".repeat(1000));
        let findings = analyze_text(&long, "");
        let oom = findings.iter().find(|f| f.rule_id == "host_oom").unwrap();
        assert_eq!(oom.sample.chars().count(), SAMPLE_LIMIT);
    }

    #[test]
    fn errors_sort_before_warnings() {
        // A user reads the top of this list first. Configuration advice above
        // a device-lost would bury the thing that actually broke.
        let log = "fsync: warning\nVK_ERROR_DEVICE_LOST";
        let findings = analyze_text(log, "");
        assert_eq!(findings[0].severity, "error");
        assert!(findings.iter().rev().any(|f| f.severity == "warn"));
    }

    #[test]
    fn matching_is_case_insensitive() {
        assert!(!analyze_text("vk_error_device_lost", "").is_empty());
    }

    // ---- redaction: what leaves the machine -------------------------------

    #[test]
    fn the_home_path_becomes_a_tilde() {
        assert_eq!(
            redact_as("opening /home/alice/games/x.log", "/home/alice", "alice"),
            "opening ~/games/x.log"
        );
    }

    #[test]
    fn another_users_home_path_is_still_redacted() {
        // The literal-home pass cannot catch this one; the general pattern must.
        let out = redact_as("/home/bob/prefix/x.dll", "/home/alice", "alice");
        assert_eq!(out, "/home/<user>/prefix/x.dll");
        assert!(!out.contains("bob"));
    }

    #[test]
    fn a_wine_prefix_users_directory_is_redacted() {
        for raw in [
            r"C:\users\alice\Documents",
            "/drive_c/users/alice/Documents",
        ] {
            let out = redact_as(raw, "/nowhere", "zz");
            assert!(out.contains("<user>"), "{raw} -> {out}");
            assert!(!out.contains("alice"), "{raw} -> {out}");
        }
    }

    #[test]
    fn a_bare_login_name_is_redacted() {
        assert_eq!(
            redact_as("user=alice failed", "/nowhere", "alice"),
            "user=<user> failed"
        );
    }

    #[test]
    fn a_very_short_login_name_is_left_alone() {
        // "al" would match inside "alias", "already", "final" - redacting it
        // would mangle the log without protecting anything.
        let text = "already aligned";
        assert_eq!(redact_as(text, "/nowhere", "al"), text);
    }

    #[test]
    fn a_login_name_is_only_redacted_on_a_word_boundary() {
        assert_eq!(
            redact_as("alicia was here", "/nowhere", "alice"),
            "alicia was here"
        );
    }

    #[test]
    fn empty_text_stays_empty() {
        assert_eq!(redact_as("", "/home/alice", "alice"), "");
    }
}
