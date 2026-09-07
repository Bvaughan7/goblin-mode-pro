//! The MangoHud config file as this tool writes it, so the Python can be
//! diffed against it byte for byte.
//!
//!     echo '{"which": "apply", "existing": "...", "log_dir": "/logs",
//!            "profile": {...}}' \
//!         | cargo run -p gmp-core --example mangohud
//!
//! Compared as TEXT, not as a parsed config: the file belongs to the user and
//! the promise is that everything outside our fence comes back exactly as it
//! was, blank lines and trailing spaces included.

use std::io::Read;

use gmp_core::config::GameProfile;
use gmp_core::mangohud;

fn main() {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw).expect("stdin");
    let input: serde_json::Value = serde_json::from_str(&raw).expect("input must be JSON");
    let existing = input["existing"].as_str().unwrap_or("");

    let out = if input["which"] == "revert" {
        mangohud::revert_text(existing)
    } else {
        let mut profile: GameProfile =
            serde_json::from_value(input["profile"].clone()).expect("bad profile");
        profile.normalise().expect("the corpus uses valid profiles");
        mangohud::apply_text(existing, &profile, input["log_dir"].as_str().unwrap_or(""))
    };
    print!("{out}");
}
