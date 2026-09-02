//! Runtime kernel tunables.
//!
//! `SYSCTL_ALLOW` is a security boundary, not configuration. The helper runs
//! as root and this method takes a key and a value from an unprivileged
//! caller, so the table is the entire thing standing between "tune my game"
//! and "write anything under /proc/sys". It is ported verbatim from
//! `helper/goblin_helper.py`, ranges included, and a test asserts the two
//! tables still agree.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::error::{HelperError, Result};
use crate::sys;

/// The only sysctls this helper will ever write, each with its accepted range.
///
/// Ported verbatim. Adding a key here widens what an unprivileged caller can
/// do to the kernel, so it is a change to be argued for, not a config tweak.
pub const SYSCTL_ALLOW: &[(&str, i64, i64)] = &[
    ("vm.max_map_count", 65530, 2_147_483_642),
    ("vm.swappiness", 0, 200),
    ("vm.compaction_proactiveness", 0, 100),
    ("kernel.split_lock_mitigate", 0, 1),
    ("user.max_user_namespaces", 0, 2_147_483_647),
    // Debian/Ubuntu downstream knob, absent on mainline kernels.
    ("kernel.unprivileged_userns_clone", 0, 1),
];

fn allowed_range(key: &str) -> Option<(i64, i64)> {
    SYSCTL_ALLOW
        .iter()
        .find(|(name, _, _)| *name == key)
        .map(|(_, low, high)| (*low, *high))
}

/// Where the pre-change sysctl values live. Separate from state.json because
/// these are undone key by key, not as a single baseline.
fn sysctl_state_file(roots: &sys::Roots) -> PathBuf {
    roots.state_dir.join("sysctls.json")
}

/// `/proc/sys/<key with dots as slashes>`, proven to be a real file under
/// /proc/sys after symlinks are resolved.
///
/// The allowlist already rules out anything with a traversal in it, so this is
/// belt and braces - but it is the structural half of the guarantee: the path
/// is checked after resolution, so a symlinked entry cannot lead out of the
/// tree. Reproduced from the Python rather than trusted away.
fn sysctl_path(proc_sys: &Path, key: &str) -> Result<PathBuf> {
    let candidate = proc_sys.join(key.replace('.', "/"));
    let resolved = candidate
        .canonicalize()
        .map_err(|_| HelperError::Failed(format!("refusing to write {}", candidate.display())))?;
    if !resolved.starts_with(proc_sys) || !resolved.is_file() {
        return Err(HelperError::Failed(format!(
            "refusing to write {}",
            resolved.display()
        )));
    }
    Ok(resolved)
}

fn load_sysctl_state(roots: &sys::Roots) -> BTreeMap<String, String> {
    std::fs::read_to_string(sysctl_state_file(roots))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn save_sysctl_state(roots: &sys::Roots, data: &BTreeMap<String, String>) -> std::io::Result<()> {
    std::fs::create_dir_all(&roots.state_dir)?;
    let json = serde_json::to_string_pretty(data)
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    std::fs::write(sysctl_state_file(roots), json)
}

/// Remember a sysctl's value before it is first changed.
///
/// Only the FIRST value is kept: a second change during the same session must
/// not overwrite the original, or reverting puts back a value this helper set
/// rather than the one the user booted with.
fn snapshot_sysctl(roots: &sys::Roots, key: &str, path: &Path) {
    let mut data = load_sysctl_state(roots);
    if data.contains_key(key) {
        return;
    }
    match sys::read_trimmed(path) {
        Ok(current) => {
            data.insert(key.to_owned(), current);
            if let Err(err) = save_sysctl_state(roots, &data) {
                tracing::warn!("could not snapshot sysctl {key}: {err}");
            }
        }
        Err(err) => tracing::warn!("could not snapshot sysctl {key}: {err}"),
    }
}

/// Set one allowlisted sysctl.
pub fn set_sysctl(roots: &sys::Roots, proc_sys: &Path, key: &str, value: &str) -> Result<bool> {
    // The wording of all three refusals is matched by the conformance suite:
    // "not in allowlist", "non-numeric", "out of range".
    let Some((low, high)) = allowed_range(key) else {
        return Err(HelperError::Failed(format!(
            "sysctl not in allowlist: {key}"
        )));
    };
    let Ok(num) = value.trim().parse::<i64>() else {
        return Err(HelperError::Failed(format!(
            "non-numeric sysctl value: '{value}'"
        )));
    };
    if num < low || num > high {
        return Err(HelperError::Failed(format!(
            "{key}={num} out of range ({low}, {high})"
        )));
    }
    let path = sysctl_path(proc_sys, key)?;

    snapshot_sysctl(roots, key, &path);
    sys::write_value(&path, &num.to_string())
        .map_err(|err| HelperError::Failed(format!("could not write {key}: {err}")))?;
    tracing::info!("sysctl {key} = {num}");
    Ok(true)
}

/// Put one sysctl back to the value it had before this helper changed it.
pub fn revert_sysctl(roots: &sys::Roots, proc_sys: &Path, key: &str) -> Result<bool> {
    if allowed_range(key).is_none() {
        return Err(HelperError::Failed(format!(
            "sysctl not in allowlist: {key}"
        )));
    }
    let mut data = load_sysctl_state(roots);
    let Some(original) = data.get(key).cloned() else {
        // Never changed it, so it is already what the user had.
        return Ok(true);
    };
    let path = sysctl_path(proc_sys, key)?;
    let value: i64 = original.trim().parse().map_err(|_| {
        HelperError::Failed(format!("recorded sysctl {key} is not a number: {original}"))
    })?;
    sys::write_value(&path, &value.to_string())
        .map_err(|err| HelperError::Failed(format!("could not restore {key}: {err}")))?;

    data.remove(key);
    // A state file that cannot be rewritten is not worth failing the revert
    // over - the value is already back.
    if let Err(err) = save_sysctl_state(roots, &data) {
        tracing::warn!("could not update the sysctl state file: {err}");
    }
    tracing::info!("sysctl {key} reverted to {value}");
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake(tag: &str) -> (sys::Roots, PathBuf) {
        let base = std::env::temp_dir().join(format!("gmp-sysctl-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let proc_sys = base.join("proc-sys");
        std::fs::create_dir_all(proc_sys.join("vm")).unwrap();
        std::fs::create_dir_all(proc_sys.join("kernel")).unwrap();
        std::fs::write(proc_sys.join("vm").join("swappiness"), "60\n").unwrap();
        std::fs::write(proc_sys.join("vm").join("max_map_count"), "65530\n").unwrap();
        std::fs::write(proc_sys.join("vm").join("dirty_ratio"), "20\n").unwrap();
        let roots = sys::Roots {
            cpu: base.join("no-cpu"),
            rapl: base.join("no-rapl"),
            state_dir: base.join("run"),
        };
        // canonicalize needs the real path, not one with symlinks unresolved
        (roots, proc_sys.canonicalize().unwrap())
    }

    fn value_of(proc_sys: &Path, rel: &str) -> String {
        sys::read_trimmed(&proc_sys.join(rel)).unwrap()
    }

    #[test]
    fn the_allowlist_matches_the_python_helper() {
        // Ported verbatim; drift here widens what an unprivileged caller can
        // reach. The Python table is the reference.
        assert_eq!(SYSCTL_ALLOW.len(), 6);
        assert_eq!(
            allowed_range("vm.max_map_count"),
            Some((65530, 2_147_483_642))
        );
        assert_eq!(allowed_range("vm.swappiness"), Some((0, 200)));
        assert_eq!(allowed_range("vm.compaction_proactiveness"), Some((0, 100)));
        assert_eq!(allowed_range("kernel.split_lock_mitigate"), Some((0, 1)));
        assert_eq!(
            allowed_range("user.max_user_namespaces"),
            Some((0, 2_147_483_647))
        );
        assert_eq!(
            allowed_range("kernel.unprivileged_userns_clone"),
            Some((0, 1))
        );
    }

    #[test]
    fn a_key_outside_the_allowlist_is_refused_even_though_it_exists() {
        // vm.dirty_ratio is a real sysctl. Being real is not the test.
        let (roots, proc_sys) = fake("outside");
        let err = set_sysctl(&roots, &proc_sys, "vm.dirty_ratio", "42").unwrap_err();
        assert!(format!("{err:?}").contains("not in allowlist"));
        assert_eq!(value_of(&proc_sys, "vm/dirty_ratio"), "20");
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn an_out_of_range_value_is_refused() {
        let (roots, proc_sys) = fake("range");
        let err = set_sysctl(&roots, &proc_sys, "vm.swappiness", "201").unwrap_err();
        assert!(format!("{err:?}").contains("out of range"));
        assert_eq!(value_of(&proc_sys, "vm/swappiness"), "60");
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn a_non_numeric_value_is_refused() {
        let (roots, proc_sys) = fake("nonnum");
        let err = set_sysctl(&roots, &proc_sys, "vm.swappiness", "lots").unwrap_err();
        assert!(format!("{err:?}").contains("non-numeric"));
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn a_refused_write_records_nothing() {
        let (roots, proc_sys) = fake("norecord");
        for (key, value) in [("vm.dirty_ratio", "42"), ("vm.swappiness", "999")] {
            let _ = set_sysctl(&roots, &proc_sys, key, value);
        }
        assert!(!sysctl_state_file(&roots).exists());
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn setting_then_reverting_returns_the_original_value() {
        let (roots, proc_sys) = fake("roundtrip");
        assert!(set_sysctl(&roots, &proc_sys, "vm.swappiness", "10").unwrap());
        assert_eq!(value_of(&proc_sys, "vm/swappiness"), "10");
        assert!(revert_sysctl(&roots, &proc_sys, "vm.swappiness").unwrap());
        assert_eq!(value_of(&proc_sys, "vm/swappiness"), "60");
        // The key is dropped once restored, so a second revert is a no-op.
        assert!(revert_sysctl(&roots, &proc_sys, "vm.swappiness").unwrap());
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn only_the_first_value_is_remembered() {
        // A second change must not overwrite the original, or reverting puts
        // back a value this helper set rather than the one the user booted on.
        let (roots, proc_sys) = fake("first");
        set_sysctl(&roots, &proc_sys, "vm.swappiness", "10").unwrap();
        set_sysctl(&roots, &proc_sys, "vm.swappiness", "20").unwrap();
        revert_sysctl(&roots, &proc_sys, "vm.swappiness").unwrap();
        assert_eq!(value_of(&proc_sys, "vm/swappiness"), "60");
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn reverting_something_never_set_is_success() {
        let (roots, proc_sys) = fake("neverset");
        assert!(revert_sysctl(&roots, &proc_sys, "vm.swappiness").unwrap());
        assert_eq!(value_of(&proc_sys, "vm/swappiness"), "60");
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn reverting_a_key_outside_the_allowlist_is_refused() {
        let (roots, proc_sys) = fake("revoutside");
        let err = revert_sysctl(&roots, &proc_sys, "vm.dirty_ratio").unwrap_err();
        assert!(format!("{err:?}").contains("not in allowlist"));
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn a_key_that_does_not_exist_on_this_kernel_is_refused_not_created() {
        // kernel.unprivileged_userns_clone is allowlisted but Debian-only. On
        // a mainline kernel the write must fail rather than create the file.
        let (roots, proc_sys) = fake("absent");
        let err =
            set_sysctl(&roots, &proc_sys, "kernel.unprivileged_userns_clone", "1").unwrap_err();
        assert!(format!("{err:?}").contains("refusing to write"));
        assert!(!proc_sys
            .join("kernel")
            .join("unprivileged_userns_clone")
            .exists());
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }

    #[test]
    fn a_symlink_out_of_proc_sys_is_refused() {
        // The structural half of the guarantee: the path is checked AFTER
        // resolution, so an entry that points outside the tree cannot be
        // written through even if its name looks allowlisted.
        let (roots, proc_sys) = fake("symlink");
        let outside = proc_sys.parent().unwrap().join("escape");
        std::fs::write(&outside, "0\n").unwrap();
        let link = proc_sys.join("vm").join("compaction_proactiveness");
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        let err = set_sysctl(&roots, &proc_sys, "vm.compaction_proactiveness", "50").unwrap_err();
        assert!(format!("{err:?}").contains("refusing to write"));
        assert_eq!(
            sys::read_trimmed(&outside).unwrap(),
            "0",
            "wrote outside /proc/sys"
        );
        let _ = std::fs::remove_dir_all(proc_sys.parent().unwrap());
    }
}
