//! Undervolt offsets.
//!
//! This helper NEVER chooses an undervolt value - getting one wrong hangs the
//! machine. It only re-applies offsets the user already configured, because
//! suspend and thermald silently drop them. `ReadUndervolt` is the read half:
//! it reports what the user's own tool says, and nothing here interprets it.

use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::power;

/// How long `intel-undervolt read` gets before it is abandoned.
const READ_TIMEOUT: Duration = Duration::from_secs(10);

/// The reply is capped so a misbehaving tool cannot push an unbounded string
/// through the bus. 2000 CHARACTERS, matching the Python helper's
/// `stdout[:2000]` - counted in characters rather than bytes so the two
/// implementations truncate at the same place.
const MAX_OUTPUT_CHARS: usize = 2000;

/// What `intel-undervolt read` reports, or `""`.
///
/// Every failure is an empty string: no intel-undervolt installed, the binary
/// failing to start, a timeout, a non-zero exit. The Python helper is equally
/// forgiving, and deliberately - this feeds an informational panel, and a
/// machine without intel-undervolt is the normal case, not a fault.
pub async fn read_undervolt() -> String {
    let Some(binary) = crate::sys::which("intel-undervolt") else {
        return String::new();
    };
    let run = tokio::process::Command::new(&binary)
        .arg("read")
        .stdin(std::process::Stdio::null())
        // Without this a timed-out child is leaked: the future is dropped but
        // the process keeps running, holding the pipe open.
        .kill_on_drop(true)
        .output();

    let output = match tokio::time::timeout(READ_TIMEOUT, run).await {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => {
            tracing::warn!("could not run {}: {err}", binary.display());
            return String::new();
        }
        Err(_elapsed) => {
            tracing::warn!("{} read timed out", binary.display());
            return String::new();
        }
    };
    // A non-zero exit is NOT treated as failure, matching the Python helper:
    // intel-undervolt reports partial state and exits non-zero on hardware it
    // only half understands, and that partial answer is still worth showing.
    truncate_chars(&String::from_utf8_lossy(&output.stdout), MAX_OUTPUT_CHARS)
}

fn truncate_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// The user's own AMD Curve Optimizer offsets. ryzenadj has no config-file
/// concept of its own, so this is a small one the user writes by hand.
pub const AMD_UNDERVOLT_CONF: &str = "/etc/goblin-mode-pro/amd-undervolt.conf";

/// The range every ryzenadj curve-optimizer guide uses. More negative is more
/// aggressive; positive offsets are not a thing this tool will apply.
const AMD_UV_RANGE: (i32, i32) = (-30, 0);

/// Re-apply the offsets in /etc/intel-undervolt.conf.
///
/// This helper NEVER chooses a value - it only re-runs what the user already
/// configured, because suspend and thermald silently drop the offsets.
pub async fn apply_undervolt() -> bool {
    let Some(binary) = crate::sys::which("intel-undervolt") else {
        return false;
    };
    let run = tokio::process::Command::new(&binary)
        .arg("apply")
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    match tokio::time::timeout(READ_TIMEOUT, run).await {
        Ok(Ok(output)) if output.status.success() => {
            tracing::info!("re-applied intel-undervolt offsets");
            true
        }
        Ok(Ok(output)) => {
            tracing::warn!(
                "intel-undervolt apply failed: {} {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            );
            false
        }
        Ok(Err(err)) => {
            tracing::warn!("intel-undervolt apply failed: {err}");
            false
        }
        Err(_elapsed) => {
            tracing::warn!("intel-undervolt apply timed out");
            false
        }
    }
}

/// `[("coall", -15), ("coper0", -20), ...]` from the user's config.
///
/// Insertion order is kept, matching the Python dict, and a repeated key is
/// replaced rather than duplicated. Malformed or out-of-range lines are
/// SKIPPED rather than fatal: this file is edited by hand, and one bad line
/// should not stop the other offsets being applied.
fn parse_amd_undervolt_conf(path: &Path) -> Vec<(String, i32)> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let (low, high) = AMD_UV_RANGE;
    let mut out: Vec<(String, i32)> = Vec::new();
    for line in text.lines() {
        // Everything after a # is a comment.
        let line = line.split('#').next().unwrap_or_default().trim();
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if !is_offset_key(key) {
            continue;
        }
        let Ok(offset) = value.trim().parse::<i32>() else {
            continue;
        };
        if offset < low || offset > high {
            tracing::warn!("amd-undervolt.conf: {key}={offset} out of range, skipping");
            continue;
        }
        match out.iter_mut().find(|(existing, _)| existing == key) {
            Some(entry) => entry.1 = offset,
            None => out.push((key.to_owned(), offset)),
        }
    }
    out
}

/// `coall`, or `coper` followed by a core index and nothing else.
fn is_offset_key(key: &str) -> bool {
    if key == "coall" {
        return true;
    }
    match key.strip_prefix("coper") {
        Some(core) => !core.is_empty() && core.chars().all(|c| c.is_ascii_digit()),
        None => false,
    }
}

/// The ryzenadj arguments for a set of offsets.
fn coper_args(offsets: &[(String, i32)]) -> Vec<String> {
    offsets
        .iter()
        .map(|(key, offset)| match key.as_str() {
            "coall" => format!("--set-coall={offset}"),
            other => format!("--set-coper={},{offset}", &other["coper".len()..]),
        })
        .collect()
}

/// Re-apply the AMD Curve Optimizer offsets the user configured.
///
/// Same rule as the Intel path: this never chooses a value, it only re-runs
/// what is already in the file, because suspend resets the SMU state.
pub async fn apply_amd_undervolt(conf: &Path) -> bool {
    let Some(binary) = power::ryzenadj() else {
        return false;
    };
    let offsets = parse_amd_undervolt_conf(conf);
    if offsets.is_empty() {
        tracing::info!("{} has no valid offsets, nothing to do", conf.display());
        return false;
    }
    match power::run_ryzenadj(binary, &coper_args(&offsets)).await {
        Ok(_) => {
            tracing::info!("re-applied AMD Curve Optimizer offsets: {offsets:?}");
            true
        }
        Err(err) => {
            tracing::warn!("ryzenadj curve-optimizer apply failed: {err}");
            false
        }
    }
}

/// The AMD undervolt config path, as a PathBuf.
pub fn amd_conf() -> PathBuf {
    PathBuf::from(AMD_UNDERVOLT_CONF)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncation_counts_characters_not_bytes() {
        // Python slices str by character. Counting bytes here would cut a
        // multi-byte character in half and produce different output from the
        // Python helper on the same input - or panic on a char boundary.
        let multibyte = "µ".repeat(10);
        assert_eq!(multibyte.len(), 20, "µ is two bytes");
        assert_eq!(truncate_chars(&multibyte, 10).chars().count(), 10);
        assert_eq!(truncate_chars(&multibyte, 4), "µµµµ");
    }

    #[test]
    fn shorter_than_the_limit_is_unchanged() {
        assert_eq!(
            truncate_chars("cpu 0: -100 mV", MAX_OUTPUT_CHARS),
            "cpu 0: -100 mV"
        );
        assert_eq!(truncate_chars("", MAX_OUTPUT_CHARS), "");
    }

    #[tokio::test]
    async fn a_machine_without_intel_undervolt_reports_nothing() {
        // The normal case on almost every machine: absent tool, empty string,
        // no error. Asserted because returning an error here would light up a
        // failure in the GUI on hardware that is working perfectly.
        if crate::sys::which("intel-undervolt").is_none() {
            assert_eq!(read_undervolt().await, "");
        }
    }

    fn conf_with(tag: &str, body: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-uv-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("amd-undervolt.conf");
        std::fs::write(&path, body).unwrap();
        path
    }

    /// Byte-for-byte the same input the Python parser was run on, and the same
    /// answer: a repeated key is REPLACED in place rather than appended, so
    /// coall keeps its original position with the later value.
    #[test]
    fn the_amd_config_parses_exactly_as_the_python_helper_does() {
        let conf = conf_with(
            "parse",
            "coall = -15\ncoper0=-20\n# a comment\ncoper1 = -99\nbogus = -5\ncoall = -10\ncoper2 = 5\n",
        );
        let parsed = parse_amd_undervolt_conf(&conf);
        assert_eq!(
            parsed,
            vec![("coall".to_owned(), -10), ("coper0".to_owned(), -20)],
            "out-of-range, unknown and commented lines are skipped; a repeat replaces"
        );
        let _ = std::fs::remove_dir_all(conf.parent().unwrap());
    }

    #[test]
    fn offsets_become_the_right_ryzenadj_flags() {
        let offsets = vec![("coall".to_owned(), -10), ("coper3".to_owned(), -20)];
        assert_eq!(
            coper_args(&offsets),
            ["--set-coall=-10", "--set-coper=3,-20"]
        );
    }

    #[test]
    fn a_missing_config_is_no_offsets_not_an_error() {
        // The normal case: almost nobody writes this file.
        assert!(parse_amd_undervolt_conf(Path::new("/nonexistent/gmp.conf")).is_empty());
    }

    #[test]
    fn only_coall_and_numbered_cores_are_offset_keys() {
        assert!(is_offset_key("coall"));
        assert!(is_offset_key("coper0"));
        assert!(is_offset_key("coper12"));
        assert!(!is_offset_key("coper"));
        assert!(!is_offset_key("coperX"));
        assert!(!is_offset_key("bogus"));
    }

    #[test]
    fn a_positive_offset_is_refused() {
        // This tool only ever undervolts. A positive Curve Optimizer value
        // raises voltage, which is not something to do on a user's behalf.
        let conf = conf_with("positive", "coall = 5\n");
        assert!(parse_amd_undervolt_conf(&conf).is_empty());
        let _ = std::fs::remove_dir_all(conf.parent().unwrap());
    }
}
