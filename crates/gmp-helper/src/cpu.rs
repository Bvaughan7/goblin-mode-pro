//! CPU frequency governor and energy-performance preference.

use std::path::Path;

use crate::sys;

/// The governor the first CPU is running, or `""` if the machine has no
/// cpufreq at all.
///
/// The empty string is a real answer, not an error: a VM or a machine with
/// `intel_pstate=disable` and no driver has no governor to report, and the
/// Python helper returns `""` there rather than failing. A caller that got an
/// error instead would show "the helper is broken" on a working machine.
pub fn get_governor(cpu_base: &Path) -> std::io::Result<String> {
    match sys::cpu_leaf_paths(cpu_base, "scaling_governor").first() {
        Some(path) => sys::read_trimmed(path),
        None => Ok(String::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-cpu-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn reads_the_first_cpus_governor() {
        let base = scratch("gov");
        for (cpu, gov) in [("cpu0", "powersave"), ("cpu1", "performance")] {
            std::fs::create_dir_all(base.join(cpu).join("cpufreq")).unwrap();
            std::fs::write(
                base.join(cpu).join("cpufreq").join("scaling_governor"),
                format!("{gov}\n"),
            )
            .unwrap();
        }
        assert_eq!(get_governor(&base).unwrap(), "powersave");
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn no_cpufreq_is_an_empty_string_not_an_error() {
        assert_eq!(get_governor(Path::new("/nonexistent/gmp")).unwrap(), "");
    }
}
