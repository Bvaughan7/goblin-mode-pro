//! Undervolt offsets.
//!
//! This helper NEVER chooses an undervolt value - getting one wrong hangs the
//! machine. It only re-applies offsets the user already configured, because
//! suspend and thermald silently drop them. `ReadUndervolt` is the read half:
//! it reports what the user's own tool says, and nothing here interprets it.

use std::time::Duration;

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
}
