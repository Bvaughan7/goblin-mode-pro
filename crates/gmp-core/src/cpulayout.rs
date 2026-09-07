//! Reading the CPU's shape out of sysfs.
//!
//! The first PROBE ported rather than the decision made from one. What it
//! produces feeds [`crate::cpuset::target_cpus`], which decides where a game's
//! threads are pinned - so a layout read wrongly does not fail, it pins a game
//! to the wrong half of the CPU and says nothing.
//!
//! Everything here takes the sysfs root as an argument, which is what makes it
//! testable against a tree that is not this machine's. One machine can only
//! ever confirm one shape.

use serde_json::{Map, Value};

use crate::capabilities::parse_cpu_list;

/// A P-core has to be at least this share of the fastest core's maximum
/// frequency, when the kernel has not classified the cores itself.
///
/// Fuzzy on purpose: the fast cores of a hybrid part do not all report the
/// same maximum, and a strict equality would split a P-core cluster in half.
const HYBRID_FREQ_SHARE: f64 = 0.92;

fn read(root: &std::path::Path, relative: &str) -> Option<String> {
    let text = std::fs::read_to_string(root.join(relative)).ok()?;
    let trimmed = text.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_string())
}

/// Every online cpu.
///
/// The kernel's own list when it offers one; otherwise the `cpuN` directories,
/// which is the answer on a kernel too old to publish `online`.
pub fn online_cpus(root: &std::path::Path) -> Vec<u32> {
    if let Some(spec) = read(root, "online") {
        return parse_cpu_list(&spec);
    }
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut out: Vec<u32> = entries
        .flatten()
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            let digits = name.strip_prefix("cpu")?;
            // `str.isdigit`, not `is_ascii_digit` - see `pyfmt::is_digit`.
            // sysfs never spells a cpu number in Arabic-Indic numerals, but
            // the two implementations should not differ about whether it
            // could.
            (!digits.is_empty() && digits.chars().all(crate::pyfmt::is_digit))
                .then(|| digits.parse::<u32>().ok())?
        })
        .collect();
    out.sort_unstable();
    out
}

/// The fast cores of a hybrid CPU, or nothing when every core is the same.
///
/// The kernel's classification first, under either of the two names it has
/// gone by. Only when neither is there does this fall back to comparing
/// maximum frequencies, and that fallback needs MORE THAN ONE distinct
/// frequency to say anything - a machine where every core reports the same
/// maximum is not a hybrid one, it is an ordinary one.
pub fn performance_cores(root: &std::path::Path, online: &[u32]) -> Vec<u32> {
    if let Some(mask) =
        read(root, "types/intel_core/cpumap").or_else(|| read(root, "types/intel_core/cpus"))
    {
        return parse_cpu_list(&mask);
    }
    let mut freqs: Vec<(u32, i64)> = Vec::new();
    for cpu in online {
        let Some(text) = read(root, &format!("cpu{cpu}/cpufreq/cpuinfo_max_freq")) else {
            continue;
        };
        if text.chars().all(crate::pyfmt::is_digit) {
            if let Ok(freq) = text.parse::<i64>() {
                freqs.push((*cpu, freq));
            }
        }
    }
    let distinct: std::collections::BTreeSet<i64> = freqs.iter().map(|(_, f)| *f).collect();
    if freqs.is_empty() || distinct.len() <= 1 {
        return Vec::new();
    }
    let top = *distinct.iter().next_back().expect("not empty") as f64;
    let mut fast: Vec<u32> = freqs
        .iter()
        .filter(|(_, freq)| *freq as f64 >= top * HYBRID_FREQ_SHARE)
        .map(|(cpu, _)| *cpu)
        .collect();
    fast.sort_unstable();
    fast
}

/// The groups of cores that share an L3 slice - one CCD each, on a chiplet
/// Ryzen.
///
/// Deduplicated by exact membership and kept in the order the cpus were
/// walked, so the FIRST group is the one holding the lowest-numbered cpu.
/// [`crate::cpuset::target_cpus`] pins `cache0` to that one.
pub fn cache_groups(root: &std::path::Path, online: &[u32]) -> Vec<Vec<u32>> {
    let mut groups: Vec<Vec<u32>> = Vec::new();
    for cpu in online {
        let Some(list) = read(root, &format!("cpu{cpu}/cache/index3/shared_cpu_list")) else {
            continue;
        };
        let members = parse_cpu_list(&list);
        if !members.is_empty() && !groups.contains(&members) {
            groups.push(members);
        }
    }
    groups
}

/// The whole layout, as the profile editor and the pinner read it.
///
/// Two absences are meaningful and neither is an empty value. `performance` is
/// left out unless the fast cores are a PROPER subset of the online ones -
/// every core being fast is the same machine as no core being fast, and
/// naming them all would offer a pinning that pins to everything. And
/// `cache_groups` is left out unless there is more than one, because there is
/// nothing to choose between when there is only one.
pub fn core_layout(root: &std::path::Path) -> Map<String, Value> {
    let online = online_cpus(root);
    let mut layout = Map::new();
    layout.insert(
        "online".to_string(),
        Value::Array(online.iter().map(|c| Value::from(*c)).collect()),
    );

    let fast = performance_cores(root, &online);
    if !fast.is_empty() && fast.len() < online.len() {
        layout.insert(
            "performance".to_string(),
            Value::Array(fast.iter().map(|c| Value::from(*c)).collect()),
        );
    }

    let groups = cache_groups(root, &online);
    if groups.len() > 1 {
        layout.insert(
            "cache_groups".to_string(),
            Value::Array(
                groups
                    .iter()
                    .map(|g| Value::Array(g.iter().map(|c| Value::from(*c)).collect()))
                    .collect(),
            ),
        );
    }
    layout
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A sysfs tree: (relative path, contents).
    fn sysfs(files: &[(&str, &str)]) -> std::path::PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "gmp-cpu-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        for (path, contents) in files {
            let full = root.join(path);
            std::fs::create_dir_all(full.parent().expect("has a parent")).unwrap();
            std::fs::write(full, contents).unwrap();
        }
        root
    }

    #[test]
    fn the_online_list_comes_from_the_kernel_when_it_offers_one() {
        let root = sysfs(&[("online", "0-3\n")]);
        assert_eq!(online_cpus(&root), vec![0, 1, 2, 3]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn without_one_the_directories_are_counted() {
        let root = sysfs(&[
            ("cpu0/x", ""),
            ("cpu1/x", ""),
            ("cpu10/x", ""),
            ("cpufreq/x", ""),
            ("cpuidle/x", ""),
        ]);
        assert_eq!(online_cpus(&root), vec![0, 1, 10], "cpufreq is not a cpu");
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn the_kernels_own_hybrid_classification_wins() {
        let root = sysfs(&[
            ("online", "0-7"),
            ("types/intel_core/cpumap", "0-3"),
            // Frequencies that would say something different, to prove they
            // are not consulted.
            ("cpu0/cpufreq/cpuinfo_max_freq", "1000000"),
            ("cpu7/cpufreq/cpuinfo_max_freq", "5000000"),
        ]);
        assert_eq!(
            performance_cores(&root, &[0, 1, 2, 3, 4, 5, 6, 7]),
            vec![0, 1, 2, 3]
        );
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn the_older_spelling_of_that_file_is_read_too() {
        let root = sysfs(&[("types/intel_core/cpus", "0,1")]);
        assert_eq!(performance_cores(&root, &[0, 1, 2, 3]), vec![0, 1]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn frequencies_are_the_fallback_and_need_a_difference_to_speak() {
        // Every core the same maximum: an ordinary CPU, not a hybrid one.
        let same: Vec<(String, String)> = (0..4)
            .map(|c| {
                (
                    format!("cpu{c}/cpufreq/cpuinfo_max_freq"),
                    "4000000".to_string(),
                )
            })
            .collect();
        let refs: Vec<(&str, &str)> = same.iter().map(|(a, b)| (a.as_str(), b.as_str())).collect();
        let root = sysfs(&refs);
        assert_eq!(performance_cores(&root, &[0, 1, 2, 3]), Vec::<u32>::new());
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_hybrid_cpu_without_the_kernels_help_is_found_by_frequency() {
        let root = sysfs(&[
            ("cpu0/cpufreq/cpuinfo_max_freq", "5000000"),
            ("cpu1/cpufreq/cpuinfo_max_freq", "4700000"), // 94% - a P-core
            ("cpu2/cpufreq/cpuinfo_max_freq", "3800000"), // 76% - an E-core
            ("cpu3/cpufreq/cpuinfo_max_freq", "3800000"),
        ]);
        assert_eq!(performance_cores(&root, &[0, 1, 2, 3]), vec![0, 1]);
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn every_core_being_fast_is_no_hybrid_split_at_all() {
        // A proper subset or nothing: naming them all would offer a pinning
        // that pins to everything.
        let root = sysfs(&[("online", "0-3"), ("types/intel_core/cpumap", "0-3")]);
        let layout = core_layout(&root);
        assert!(!layout.contains_key("performance"));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn one_cache_group_is_nothing_to_choose_between() {
        let root = sysfs(&[
            ("online", "0-3"),
            ("cpu0/cache/index3/shared_cpu_list", "0-3"),
            ("cpu1/cache/index3/shared_cpu_list", "0-3"),
        ]);
        assert!(!core_layout(&root).contains_key("cache_groups"));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn two_ccds_are_two_groups_lowest_cpu_first() {
        let root = sysfs(&[
            ("online", "0-3"),
            ("cpu0/cache/index3/shared_cpu_list", "0-1"),
            ("cpu1/cache/index3/shared_cpu_list", "0-1"),
            ("cpu2/cache/index3/shared_cpu_list", "2-3"),
            ("cpu3/cache/index3/shared_cpu_list", "2-3"),
        ]);
        let layout = core_layout(&root);
        assert_eq!(layout["cache_groups"], serde_json::json!([[0, 1], [2, 3]]));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn a_machine_with_nothing_to_say_still_reports_its_cpus() {
        let root = sysfs(&[("online", "0-11")]);
        let layout = core_layout(&root);
        assert_eq!(layout.len(), 1);
        assert_eq!(
            layout["online"],
            serde_json::json!((0..12).collect::<Vec<u32>>())
        );
        std::fs::remove_dir_all(&root).ok();
    }
}
