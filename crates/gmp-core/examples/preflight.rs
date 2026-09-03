//! Answer the preflight decisions for fixture readings, as JSON, so the Python
//! implementation can be diffed against it.

use std::io::Read;

use gmp_core::preflight as pf;

fn r(c: pf::CheckResult) -> serde_json::Value {
    serde_json::json!([c.status, c.value, c.detail])
}

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let i: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");

    let rows: Vec<pf::Row> = i["rows"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|v| pf::Row {
                    id: v["id"].as_str().unwrap_or_default().to_owned(),
                    status: v["status"].as_str().unwrap_or_default().to_owned(),
                    sysctl: v["sysctl"].as_array().map(|s| {
                        (
                            s[0].as_str().unwrap_or_default().to_owned(),
                            s[1].as_str().unwrap_or_default().to_owned(),
                        )
                    }),
                })
                .collect()
        })
        .unwrap_or_default();

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "max_map_count": r(pf::max_map_count(i["max_map_count"].as_i64())),
            "nofile": r(pf::nofile(i["nofile"].as_i64())),
            "split_lock": r(pf::split_lock(i["split_lock"].as_str())),
            "compaction": r(pf::compaction(i["compaction"].as_i64())),
            "swappiness": r(pf::swappiness(i["swappiness"].as_i64())),
            "fsync": r(pf::fsync(
                i["kernel_major"].as_u64().unwrap_or(0) as u32,
                i["kernel_minor"].as_u64().unwrap_or(0) as u32)),
            "capped": pf::apply_severity(
                i["status"].as_str().unwrap_or(""), i["severity"].as_str().unwrap_or("")),
            "summary": pf::summary(&rows),
            "pending_sysctls": pf::pending_sysctls(&rows),
            "dropin": pf::sysctl_dropin_text(&rows),
        }))
        .unwrap()
    );
}
