//! The machine probes in [`gmp_core::capabilities`], as JSON, so the Python
//! can be diffed against them.
//!
//! Every case names its own roots, so the same run can be graded against a
//! handful of fixture trees and against this machine's real `/sys` and
//! `/proc` - which is the only way one machine tests more than one shape.
//!
//!     echo '{"cases": [{"cpuinfo": "...", "drm_root": "/sys/class/drm"}]}' \
//!         | cargo run -p gmp-core --example capabilities_probe

use std::io::Read;
use std::path::Path;

use gmp_core::capabilities;
use serde_json::{json, Value};

fn text(case: &Value, key: &str) -> String {
    case[key].as_str().unwrap_or("").to_string()
}

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
                    let cpuinfo = text(case, "cpuinfo");
                    let os_release = text(case, "os_release");
                    json!({
                        "cpu_vendor": capabilities::cpu_vendor(&cpuinfo),
                        "cpu_model": capabilities::cpu_model(&cpuinfo),
                        "cpufreq_driver": capabilities::cpufreq_driver(
                            Path::new(&text(case, "cpu_root"))),
                        "gpu_vendors": capabilities::gpu_vendors(
                            Path::new(&text(case, "drm_root")),
                            case["nvidia_smi"].as_bool().unwrap_or(false)),
                        "handheld": capabilities::handheld(
                            Path::new(&text(case, "dmi_root"))),
                        "distro_id": capabilities::distro_id(&os_release),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    println!("{}", serde_json::to_string(&out).unwrap());
}
