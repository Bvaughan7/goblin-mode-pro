//! Intel RAPL power limits and AMD ryzenadj TDP control.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use crate::sys;

fn rapl_constraint(rapl_base: &Path, idx: u8, leaf: &str) -> PathBuf {
    rapl_base.join(format!("constraint_{idx}_{leaf}"))
}

/// PL1 and PL2, in microwatts.
///
/// Fails rather than reporting zeros when the machine has no RAPL: PL1 = 0 is
/// a value a caller could act on, and "this machine cannot report power
/// limits" is not the same statement as "this machine's limit is nothing".
/// The Python helper propagates the `OSError` here for the same reason.
pub fn get_power_limits(rapl_base: &Path) -> std::io::Result<(u64, u64)> {
    let read = |idx: u8| -> std::io::Result<u64> {
        let path = rapl_constraint(rapl_base, idx, "power_limit_uw");
        sys::read_trimmed(&path)?.parse::<u64>().map_err(|err| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("{} is not a number: {err}", path.display()),
            )
        })
    };
    Ok((read(0)?, read(1)?))
}

/// `ryzenadj`, resolved once at startup.
///
/// Resolved once rather than per call because the Python helper binds
/// `RYZENADJ` at import: a machine that installs ryzenadj while the helper is
/// running keeps reporting no TDP control until the service restarts, on both
/// implementations. Matching that matters more than being cleverer, because
/// `HasTDPControl` is what the GUI greys a control on.
pub fn ryzenadj() -> Option<&'static Path> {
    static RYZENADJ: OnceLock<Option<PathBuf>> = OnceLock::new();
    RYZENADJ.get_or_init(|| sys::which("ryzenadj")).as_deref()
}

pub fn has_tdp_control() -> bool {
    ryzenadj().is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-power-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn reads_both_constraints() {
        let base = scratch("ok");
        std::fs::write(base.join("constraint_0_power_limit_uw"), "107000000\n").unwrap();
        std::fs::write(base.join("constraint_1_power_limit_uw"), "107000000\n").unwrap();
        assert_eq!(get_power_limits(&base).unwrap(), (107_000_000, 107_000_000));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn no_rapl_is_an_error_not_a_zero() {
        // A zero would be a power limit a caller could act on. "Cannot report"
        // has to stay distinguishable from "is nothing".
        assert!(get_power_limits(Path::new("/nonexistent/gmp")).is_err());
    }

    #[test]
    fn a_non_numeric_constraint_is_an_error() {
        let base = scratch("junk");
        std::fs::write(base.join("constraint_0_power_limit_uw"), "banana\n").unwrap();
        std::fs::write(base.join("constraint_1_power_limit_uw"), "1\n").unwrap();
        assert!(get_power_limits(&base).is_err());
        let _ = std::fs::remove_dir_all(&base);
    }
}
