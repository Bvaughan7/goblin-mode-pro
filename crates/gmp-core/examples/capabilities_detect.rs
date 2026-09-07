//! The whole capability snapshot, as JSON, so the Python's `detect()` can be
//! diffed against it.
//!
//! This is the map that goes onto the daemon status and that the GUI hides
//! features from, so a difference here is a feature that appears or vanishes
//! rather than a number that is slightly off.
//!
//!     echo '{"cases": [{...}]}' \
//!         | cargo run -p gmp-core --example capabilities_detect

use std::io::Read;
use std::path::{Path, PathBuf};

use gmp_core::capabilities::{self, Machine};
use serde_json::Value;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: Value = serde_json::from_str(&raw).expect("input must be JSON");

    let out: Vec<Value> = input["cases"]
        .as_array()
        .map(|cases| {
            cases
                .iter()
                .map(|case| {
                    let s = |key: &str| case[key].as_str().unwrap_or("");
                    let env = |name: &str| case["env"][name].as_str().unwrap_or("").to_string();
                    let have = |tool: &str| {
                        case["have"]
                            .as_array()
                            .is_some_and(|v| v.iter().any(|t| t.as_str() == Some(tool)))
                    };
                    let dirs: Vec<PathBuf> = case["scx_bin_dirs"]
                        .as_array()
                        .map(|v| {
                            v.iter()
                                .filter_map(|d| d.as_str())
                                .map(PathBuf::from)
                                .collect()
                        })
                        .unwrap_or_default();
                    let machine = Machine {
                        cpuinfo: s("cpuinfo"),
                        os_release: s("os_release"),
                        kernel_release: s("kernel_release"),
                        cpu_root: Path::new(s("cpu_root")),
                        dmi_root: Path::new(s("dmi_root")),
                        drm_root: Path::new(s("drm_root")),
                        hwmon_root: Path::new(s("hwmon_root")),
                        powercap_root: Path::new(s("powercap_root")),
                        sched_ext_sysfs: Path::new(s("sched_ext_sysfs")),
                        vkbasalt_layer: Path::new(s("vkbasalt_layer")),
                        scx_bin_dirs: &dirs,
                        have: &have,
                        env: &env,
                    };
                    Value::Object(capabilities::detect(&machine))
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
