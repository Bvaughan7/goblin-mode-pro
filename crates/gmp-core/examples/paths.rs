//! Resolve every path for one environment, as JSON, so the Python
//! implementation can be diffed against it.
//!
//!     echo '{"HOME": "/home/u", "XDG_CONFIG_HOME": "/tmp/cfg"}' \
//!         | cargo run -p gmp-core --example paths

use std::io::Read;

use gmp_core::paths;

fn main() {
    let mut buffer = String::new();
    std::io::stdin().read_to_string(&mut buffer).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&buffer).expect("input must be JSON");

    // A key that is absent means the variable is unset; a key holding a string
    // means it is set to that, empty string included. The two are different
    // and the difference is the point.
    let var = |name: &str| input.get(name).and_then(|v| v.as_str()).map(str::to_string);

    let env = paths::Env {
        home: var("HOME").unwrap_or_default(),
        config_home: var("XDG_CONFIG_HOME"),
        state_home: var("XDG_STATE_HOME"),
        data_home: var("XDG_DATA_HOME"),
        cache_home: var("XDG_CACHE_HOME"),
    };

    println!(
        "{}",
        serde_json::to_string_pretty(&paths::resolve(&env)).unwrap()
    );
}
