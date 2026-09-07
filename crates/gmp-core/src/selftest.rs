//! What the self-test makes of what the system tells it.
//!
//! The probes stay in Python - they read sysfs, run `pkcheck` and call the
//! privileged helper. What is here is the reading of their answers, which is
//! the part that can be wrong while everything still runs: a capability
//! decoded from the wrong bit reports a helper as unprivileged when it is
//! fine, and a polkit answer read wrongly tells somebody their policy file is
//! missing when it is installed and working.
//!
//! The reporting layer this feeds is already ported - see [`crate::report`]
//! and the selftest report parity harness.

use regex::Regex;
use std::sync::OnceLock;

/// The capabilities the helper is checked for, and their bit numbers.
///
/// In the order they are reported, which is the order the Python's dict was
/// written in - `CAP_SYS_ADMIN` last even though its bit is lowest.
pub const CAP_BITS: &[(&str, u32)] = &[
    ("CAP_SYS_NICE", 23),
    ("CAP_SYS_RESOURCE", 24),
    ("CAP_SYS_ADMIN", 21),
];

/// The capabilities a mask holds, named.
pub fn decode_caps(mask: u64) -> Vec<&'static str> {
    CAP_BITS
        .iter()
        .filter(|(_, bit)| mask >> bit & 1 == 1)
        .map(|(name, _)| *name)
        .collect()
}

/// One capability set out of `/proc/<pid>/status`.
///
/// The field is matched on its own LINE and the value read as hex, which is
/// how the kernel writes it. Anchored, because `CapEff` is a substring of
/// nothing but `CapEff` today and that is not a promise the kernel has made.
pub fn read_cap_set(status: &str, field: &str) -> Option<u64> {
    static RE: OnceLock<std::sync::Mutex<Vec<(String, Regex)>>> = OnceLock::new();
    let cache = RE.get_or_init(|| std::sync::Mutex::new(Vec::new()));
    let pattern = {
        let mut cache = cache.lock().expect("not poisoned");
        if let Some((_, re)) = cache.iter().find(|(f, _)| f == field) {
            re.clone()
        } else {
            let re = Regex::new(&format!(
                r"(?m)^{}:[ \t]*([0-9a-fA-F]+)$",
                regex::escape(field)
            ))
            .expect("a valid pattern");
            cache.push((field.to_string(), re.clone()));
            re
        }
    };
    let captured = pattern.captures(status)?;
    u64::from_str_radix(captured.get(1)?.as_str(), 16).ok()
}

/// What `pkcheck` said about an action.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolkitAnswer {
    pub state: &'static str,
    pub detail: String,
}

/// Read `pkcheck`'s answer for one action.
///
/// It is run WITHOUT `--allow-user-interaction`, because the read-only mode
/// must never pop a password dialog - so `auth_required` is a healthy answer
/// here and not a failure. It means the policy is installed and polkit will
/// ask when the time comes.
///
/// The order of the tests is the whole of it. A zero exit wins outright, even
/// if the output happens to mention `auth_required`; only then does the text
/// get a say. Reading the text first would report an authorized session as one
/// that still needs a prompt.
pub fn pkcheck_answer(installed: bool, code: i32, output: &str) -> PolkitAnswer {
    if !installed {
        return PolkitAnswer {
            state: "unknown",
            detail: "pkcheck is not installed (polkit's CLI)".to_string(),
        };
    }
    if code == 0 {
        return PolkitAnswer {
            state: "yes",
            detail: "already authorized for this session, no prompt needed".to_string(),
        };
    }
    if output.contains("auth_required") || code == 2 {
        return PolkitAnswer {
            state: "prompt",
            detail: "polkit will prompt for a password when this is used".to_string(),
        };
    }
    if output.contains("not registered") || output.contains("No such") {
        return PolkitAnswer {
            state: "missing",
            detail: "polkit does not know this action - the policy file is not installed"
                .to_string(),
        };
    }
    PolkitAnswer {
        state: "no",
        detail: match crate::store::splitlines(output).first() {
            Some(line) => (*line).to_string(),
            None => format!("pkcheck exited {code}"),
        },
    }
}

/// Microwatts as the self-test prints them.
pub fn watts(uw: Option<i64>) -> String {
    match uw {
        None => "unknown".to_string(),
        Some(uw) => format!("{:.1} W", uw as f64 / 1_000_000.0),
    }
}

/// The STAPM limit out of `ryzenadj --info`, in watts.
pub fn ryzenadj_stapm(output: &str) -> Option<f64> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let pattern =
        RE.get_or_init(|| Regex::new(r"STAPM LIMIT\s*\|\s*([\d.]+)").expect("a valid pattern"));
    pattern
        .captures(output)?
        .get(1)?
        .as_str()
        .parse::<f64>()
        .ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_mask_names_the_capabilities_it_holds() {
        assert_eq!(decode_caps(0), Vec::<&str>::new());
        assert_eq!(decode_caps(1 << 23), vec!["CAP_SYS_NICE"]);
        assert_eq!(decode_caps(1 << 24), vec!["CAP_SYS_RESOURCE"]);
        assert_eq!(decode_caps(1 << 21), vec!["CAP_SYS_ADMIN"]);
    }

    #[test]
    fn the_names_come_in_the_order_they_are_reported() {
        // Not bit order: ADMIN is bit 21 and comes last.
        assert_eq!(
            decode_caps((1 << 21) | (1 << 23) | (1 << 24)),
            vec!["CAP_SYS_NICE", "CAP_SYS_RESOURCE", "CAP_SYS_ADMIN"]
        );
    }

    #[test]
    fn capabilities_the_test_does_not_ask_about_are_not_reported() {
        // A full mask holds far more than these three.
        assert_eq!(decode_caps(u64::MAX).len(), 3);
        assert_eq!(decode_caps(1 << 22), Vec::<&str>::new());
    }

    #[test]
    fn a_capability_set_is_read_as_hex_from_its_own_line() {
        let status = "Name:\thelper\nCapEff:\t0000000000800000\nCapPrm:\t0000000001000000\n";
        assert_eq!(read_cap_set(status, "CapEff"), Some(0x800000));
        assert_eq!(read_cap_set(status, "CapPrm"), Some(0x1000000));
        assert_eq!(
            decode_caps(read_cap_set(status, "CapEff").unwrap()),
            vec!["CAP_SYS_NICE"]
        );
    }

    #[test]
    fn a_field_that_is_not_there_is_no_answer() {
        assert_eq!(read_cap_set("Name:\thelper\n", "CapEff"), None);
        assert_eq!(read_cap_set("", "CapEff"), None);
    }

    #[test]
    fn a_field_name_that_only_appears_inside_another_line_is_not_matched() {
        // Anchored on purpose.
        assert_eq!(read_cap_set("NotCapEff:\t00ff\n", "CapEff"), None);
        assert_eq!(read_cap_set("CapEff has no colon 00ff\n", "CapEff"), None);
    }

    #[test]
    fn a_missing_pkcheck_is_not_a_missing_policy() {
        let answer = pkcheck_answer(false, 0, "");
        assert_eq!(answer.state, "unknown");
        assert!(answer.detail.contains("not installed"));
    }

    #[test]
    fn a_zero_exit_wins_over_anything_the_output_says() {
        // Reading the text first would report an authorized session as one
        // that still needs a prompt.
        assert_eq!(pkcheck_answer(true, 0, "auth_required").state, "yes");
        assert_eq!(pkcheck_answer(true, 0, "not registered").state, "yes");
    }

    #[test]
    fn a_prompt_is_a_healthy_answer() {
        // pkcheck is run without --allow-user-interaction, so this means the
        // policy is installed and polkit will ask when the time comes.
        assert_eq!(pkcheck_answer(true, 1, "auth_required").state, "prompt");
        assert_eq!(pkcheck_answer(true, 2, "").state, "prompt");
    }

    #[test]
    fn an_unknown_action_says_the_policy_file_is_missing() {
        assert_eq!(pkcheck_answer(true, 1, "not registered").state, "missing");
        assert_eq!(pkcheck_answer(true, 1, "No such action").state, "missing");
    }

    #[test]
    fn anything_else_is_reported_in_pkchecks_own_words() {
        let answer = pkcheck_answer(true, 3, "something went wrong\nand more\n");
        assert_eq!(answer.state, "no");
        assert_eq!(answer.detail, "something went wrong");
    }

    #[test]
    fn a_failure_with_nothing_to_say_reports_its_exit_code() {
        assert_eq!(pkcheck_answer(true, 7, "").detail, "pkcheck exited 7");
    }

    #[test]
    fn watts_are_printed_to_one_place_and_absence_is_a_word() {
        assert_eq!(watts(None), "unknown");
        assert_eq!(watts(Some(0)), "0.0 W");
        assert_eq!(watts(Some(45_000_000)), "45.0 W");
        assert_eq!(watts(Some(45_500_000)), "45.5 W");
        assert_eq!(watts(Some(7_250_000)), "7.2 W", "halves go to even");
    }

    #[test]
    fn the_stapm_limit_is_read_out_of_ryzenadjs_table() {
        let out = "CPU Family\t| Renoir\nSTAPM LIMIT      | 25.000 | stapm limit\n";
        assert_eq!(ryzenadj_stapm(out), Some(25.0));
        assert_eq!(ryzenadj_stapm("nothing here"), None);
        assert_eq!(ryzenadj_stapm(""), None);
    }
}
