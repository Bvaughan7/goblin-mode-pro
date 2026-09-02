//! Filesystem primitives shared by the hardware operations.
//!
//! Every sysfs access goes through here rather than being spelled out at each
//! call site, so the rules about what may be touched live in one place. Reads
//! are the only thing this module does today; the write path lands with the
//! operations that need it.

use std::io;
use std::path::{Path, PathBuf};

/// Where per-CPU cpufreq knobs live.
pub const CPU_BASE: &str = "/sys/devices/system/cpu";

/// Package 0's RAPL zone. The Python helper hardcodes `intel-rapl:0` too: a
/// machine with a second package would need more than a different path here,
/// so guessing at multi-socket support would be worse than not having it.
pub const RAPL_BASE: &str = "/sys/class/powercap/intel-rapl/intel-rapl:0";

/// tmpfs, root-only. Wiped on reboot, which is the point: a snapshot describes
/// what was true before this boot's changes, and a stale one from a previous
/// boot would restore values that are no longer meaningful.
pub const STATE_DIR: &str = "/run/goblin-mode-pro";

/// The sysctl tree. Passed to the sysctl operations explicitly so they can
/// be tested against a fake one without root.
pub const PROC_SYS: &str = "/proc/sys";

/// hwmon, where the fan controls live.
pub const HWMON_BASE: &str = "/sys/class/hwmon";

/// The filesystem roots every operation works against.
///
/// Passed in rather than reached for as globals so the operations can be
/// tested against a temporary tree instead of needing root and real hardware.
/// It is also the seam the plan asks for: a write takes a path derived from
/// one of these roots by ENUMERATION, never a path assembled from anything a
/// caller sent, so traversal is structurally impossible rather than filtered.
#[derive(Debug, Clone)]
pub struct Roots {
    pub cpu: PathBuf,
    pub rapl: PathBuf,
    pub state_dir: PathBuf,
}

impl Roots {
    /// The real machine.
    pub fn system() -> Self {
        Self {
            cpu: PathBuf::from(CPU_BASE),
            rapl: PathBuf::from(RAPL_BASE),
            state_dir: PathBuf::from(STATE_DIR),
        }
    }

    pub fn state_file(&self) -> PathBuf {
        self.state_dir.join("state.json")
    }
}

/// Write a value to a sysfs attribute.
///
/// No trailing newline, matching the Python helper's `_write`: sysfs parses
/// the value itself and some attributes reject the extra byte.
pub fn write_value(path: &Path, value: &str) -> io::Result<()> {
    std::fs::write(path, value)
}

/// Read a sysfs value, trimmed. Mirrors `_read` in the Python helper, whose
/// `.strip()` matters: sysfs values carry a trailing newline and a comparison
/// against an untrimmed read silently never matches.
pub fn read_trimmed(path: &Path) -> io::Result<String> {
    Ok(std::fs::read_to_string(path)?.trim().to_owned())
}

/// The `<cpu_base>/cpu[0-9]*/cpufreq/<leaf>` paths that exist, sorted.
///
/// Sorted as strings, which is what `sorted(glob.glob(...))` does in the
/// Python helper: that yields cpu0, cpu1, cpu10, cpu11, cpu2 rather than
/// numeric order. Nothing depends on the ordering beyond "cpu0 is first",
/// which holds either way - but reproducing the Python order means the two
/// helpers cannot disagree about which CPU is the one sampled.
pub fn cpu_leaf_paths(cpu_base: &Path, leaf: &str) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(cpu_base) else {
        return Vec::new();
    };
    let mut found: Vec<PathBuf> = entries
        .flatten()
        .filter(|e| is_cpu_dir(&e.file_name().to_string_lossy()))
        .map(|e| e.path().join("cpufreq").join(leaf))
        .filter(|p| p.exists())
        .collect();
    found.sort();
    found
}

/// The `cpu[0-9]*` half of the glob: "cpu", then a digit, then anything.
fn is_cpu_dir(name: &str) -> bool {
    name.strip_prefix("cpu")
        .and_then(|rest| rest.chars().next())
        .is_some_and(|c| c.is_ascii_digit())
}

/// A `$PATH` lookup, equivalent to Python's `shutil.which` for the way this
/// helper uses it: an absolute program name is taken as-is, anything else is
/// searched along `$PATH`, and the file must be executable.
pub fn which(program: &str) -> Option<PathBuf> {
    let candidate = Path::new(program);
    if candidate.is_absolute() {
        return is_executable(candidate).then(|| candidate.to_path_buf());
    }
    std::env::var_os("PATH")?
        .to_string_lossy()
        .split(':')
        .filter(|dir| !dir.is_empty())
        .map(|dir| Path::new(dir).join(program))
        .find(|path| is_executable(path))
}

fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_cpu_directory_is_cpu_then_a_digit() {
        assert!(is_cpu_dir("cpu0"));
        assert!(is_cpu_dir("cpu11"));
        // Real entries under /sys/devices/system/cpu that must NOT be swept in.
        assert!(!is_cpu_dir("cpuidle"));
        assert!(!is_cpu_dir("cpufreq"));
        assert!(!is_cpu_dir("cpu"));
        assert!(!is_cpu_dir("isolated"));
    }

    #[test]
    fn cpu_leaf_paths_finds_only_existing_leaves_and_sorts_them() {
        let tmp = std::env::temp_dir().join(format!("gmp-sys-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        for cpu in ["cpu0", "cpu1", "cpu10", "cpu2"] {
            std::fs::create_dir_all(tmp.join(cpu).join("cpufreq")).unwrap();
            std::fs::write(
                tmp.join(cpu).join("cpufreq").join("scaling_governor"),
                "powersave\n",
            )
            .unwrap();
        }
        // A CPU with no cpufreq at all, and a non-CPU sibling directory.
        std::fs::create_dir_all(tmp.join("cpu3")).unwrap();
        std::fs::create_dir_all(tmp.join("cpuidle").join("cpufreq")).unwrap();
        std::fs::write(
            tmp.join("cpuidle").join("cpufreq").join("scaling_governor"),
            "x",
        )
        .unwrap();

        let found = cpu_leaf_paths(&tmp, "scaling_governor");
        let names: Vec<String> = found
            .iter()
            .map(|p| p.parent().unwrap().parent().unwrap().file_name().unwrap())
            .map(|n| n.to_string_lossy().into_owned())
            .collect();
        // String order, exactly like sorted(glob.glob(...)): cpu10 before cpu2.
        assert_eq!(names, ["cpu0", "cpu1", "cpu10", "cpu2"]);
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn missing_base_is_empty_not_an_error() {
        // A machine with no cpufreq at all - get_governor returns "" there.
        assert!(cpu_leaf_paths(Path::new("/nonexistent/gmp"), "scaling_governor").is_empty());
    }

    #[test]
    fn read_trimmed_strips_the_sysfs_newline() {
        let tmp = std::env::temp_dir().join(format!("gmp-read-{}", std::process::id()));
        std::fs::write(&tmp, "performance\n").unwrap();
        assert_eq!(read_trimmed(&tmp).unwrap(), "performance");
        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn which_finds_a_real_program_and_rejects_a_directory() {
        assert!(which("sh").is_some(), "sh must be on PATH");
        assert!(which("definitely-not-a-real-program-xyzzy").is_none());
        // /tmp is executable-by-mode but is not a file; is_executable must not
        // be fooled into treating a directory as a program.
        assert!(!is_executable(Path::new("/tmp")));
    }
}
