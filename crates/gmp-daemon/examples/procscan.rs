//! Dump this machine's process table as the observer sees it, so psutil can be
//! diffed against it.
//!
//!     cargo run -p gmp-daemon --example procscan

fn main() {
    let procs = gmp_daemon::procscan::scan();
    let rows: Vec<serde_json::Value> = procs
        .iter()
        .map(|p| {
            serde_json::json!({
                "pid": p.pid,
                "name": p.name,
                "exe": p.exe,
                "cmdline": p.cmdline,
            })
        })
        .collect();
    println!("{}", serde_json::to_string(&rows).unwrap());
}
