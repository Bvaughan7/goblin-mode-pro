//! Community profile sync: the validation half.
//!
//! Profiles are fetched over HTTPS from one pinned host and previewed before
//! anything is applied. The fetching stays in Python; what is here is every
//! check that runs on what comes back, because those are the lines that decide
//! what a remote file is allowed to turn into.
//!
//! The trust model is worth stating, because it is what makes these checks
//! proportionate rather than paranoid: the host is pinned, the path is pinned
//! under it, nothing is applied without the user confirming, and the daemon
//! re-validates every field through `GameProfile` before saving. These checks
//! are the first of those layers, not the only one.

use regex::Regex;
use serde_json::{Map, Value};
use std::sync::OnceLock;

/// The only host this module will talk to.
pub const ALLOWED_HOST: &str = "raw.githubusercontent.com";

/// The only directory under it.
pub const BASE: &str = "https://raw.githubusercontent.com/Bvaughan7/goblin-mode-pro/main/profiles";

/// The fields a community profile is allowed to carry.
///
/// An allowlist, not a blocklist: a field added to `GameProfile` is not
/// shareable until somebody says so here, which is the right way round for a
/// file that arrives from outside.
pub const SHAREABLE: &[&str] = &[
    "adaptive_sync_enabled",
    "core_pin",
    "display_name",
    "exe",
    "focus_mode",
    "fps_dip_floor",
    "fps_dip_ratio",
    "fps_watchdog",
    "gamescope",
    "gamescope_enabled",
    "governor_boost",
    "gpu_tuning",
    "mangohud",
    "match_mode",
    "nice_value",
    "note",
    "notes",
    "per_game_mangohud",
    "pl1_w",
    "pl2_w",
    "power_limit_enabled",
    "renice_enabled",
    "runner_vars",
    "steam_app_id",
    "tearing_enabled",
];

/// Whether a URL is inside the one directory this module fetches from.
///
/// A prefix test on the whole base INCLUDING its trailing slash: without the
/// slash, `.../profilesomething` would pass.
pub fn allowed_url(url: &str) -> bool {
    url.starts_with(&format!("{BASE}/"))
}

/// A profile id, or an error.
///
/// Everything that is not alphanumeric or `.`, `-`, `_` is DROPPED rather than
/// rejected, and what is left has to still look like an id: not empty, no `..`
/// anywhere, and not starting with a dot or a dash. The order matters - the
/// `..` check runs on the FILTERED string, so a slug that smuggles a traversal
/// past the filter as `a/../b` is caught after the slash is gone rather than
/// before.
///
/// The character test is Python's `str.isalnum`, which is NOT Rust's
/// `char::is_alphanumeric`. Both are Unicode-aware and they are different
/// functions: `is_alphabetic` follows the derived Alphabetic property, which
/// takes in `Other_Alphabetic` - combining marks such as U+0345 and the
/// U+0363..036F block - while Python asks only for a general category of
/// `L*` or `N*`. Over the printable characters below U+3000 they disagree
/// about 771 of them, and the answer goes into a URL path.
pub fn safe_slug(slug: &str) -> Result<String, String> {
    let filtered: String = slug
        .chars()
        .filter(|c| is_alnum(*c) || matches!(c, '.' | '-' | '_'))
        .take(64)
        .collect();
    if filtered.is_empty()
        || filtered.contains("..")
        || filtered.starts_with('.')
        || filtered.starts_with('-')
    {
        return Err("bad profile id".to_string());
    }
    Ok(filtered)
}

/// The catalogue, filtered.
///
/// An entry without a slug or without an exe is SKIPPED - a catalogue is a
/// list of other people's contributions and one malformed row should not cost
/// the rest. A bad slug is different: it fails the whole fetch, because a slug
/// that cannot be made safe is the one field that becomes a URL.
pub fn index_entries(data: &Value) -> Result<Vec<Value>, String> {
    let rows = data.as_array().ok_or("index is not a list")?;
    let mut out = Vec::new();
    for entry in rows {
        let Some(entry) = entry.as_object() else {
            continue;
        };
        let slug = entry.get("slug");
        let exe = entry.get("exe");
        if !slug.is_some_and(truthy) || !exe.is_some_and(truthy) {
            continue;
        }
        let exe_text = python_str(exe.expect("checked above"));
        // `display_name or exe` picks between the RAW values, so a display
        // name that is present but empty falls back to the executable.
        let display = match entry.get("display_name") {
            Some(value) if truthy(value) => python_str(value),
            _ => exe_text.clone(),
        };
        let note = match entry.get("note") {
            Some(value) if truthy(value) => python_str(value),
            _ => String::new(),
        };
        let mut row = Map::new();
        row.insert(
            "slug".into(),
            safe_slug(&python_str(slug.expect("checked above")))?.into(),
        );
        // The caps count CHARACTERS, which is what a Python slice does.
        row.insert("exe".into(), take_chars(&exe_text, 128).into());
        row.insert("display_name".into(), take_chars(&display, 200).into());
        row.insert("note".into(), take_chars(&note, 280).into());
        out.push(Value::Object(row));
    }
    Ok(out)
}

/// One profile, filtered to the fields a community profile may carry.
pub fn shareable(data: &Value) -> Result<Value, String> {
    let object = data.as_object().ok_or("profile has no exe")?;
    if !object.get("exe").is_some_and(truthy) {
        return Err("profile has no exe".to_string());
    }
    let kept: Map<String, Value> = object
        .iter()
        .filter(|(key, _)| SHAREABLE.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    Ok(Value::Object(kept))
}

/// `str.isalnum` for one character: a general category of `L*` or `N*`.
///
/// Spelled as a pattern rather than assembled from `char` predicates because
/// that is what the rule IS - Python's `isalnum` is `isalpha` (the `L`
/// categories) or `isnumeric` (the `N` ones), and `isdecimal` and `isdigit`
/// are subsets of the second.
fn is_alnum(c: char) -> bool {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[\p{L}\p{N}]$").expect("a valid pattern"))
        .is_match(c.encode_utf8(&mut [0u8; 4]))
}

/// Python truthiness for the shapes a fetched JSON document holds.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(rows) => !rows.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `str(value)` for the scalars a catalogue entry can hold.
///
/// A container is NOT reproduced: Python would render `[1, 2]` or
/// `{'a': 1}` through `repr`, and modelling that here would be a lot of
/// machinery for a shape our own `index.json` does not contain. The parity
/// corpus records the difference rather than hiding it.
fn python_str(value: &Value) -> String {
    crate::pyfmt::scalar(value)
}

fn take_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_url_outside_the_profiles_directory_is_refused() {
        assert!(allowed_url(&format!("{BASE}/wow.json")));
        assert!(!allowed_url("https://example.com/wow.json"));
        assert!(!allowed_url(BASE), "the bare base is not inside itself");
        // Without the trailing slash in the test, a sibling directory whose
        // name merely starts the same way would pass.
        assert!(!allowed_url(&format!("{BASE}-evil/wow.json")));
    }

    #[test]
    fn an_ordinary_slug_survives_intact() {
        assert_eq!(safe_slug("wow-classic_1.2").unwrap(), "wow-classic_1.2");
    }

    #[test]
    fn what_does_not_belong_in_an_id_is_dropped_not_rejected() {
        assert_eq!(safe_slug("wow/../etc").unwrap_or_default(), "");
        assert_eq!(safe_slug("a b c").unwrap(), "abc");
        assert_eq!(safe_slug("hell%20o").unwrap(), "hell20o");
    }

    #[test]
    fn a_traversal_is_caught_after_the_slashes_are_gone() {
        // `wow/../etc` filters to `wow..etc`, which still contains `..`.
        assert!(safe_slug("wow/../etc").is_err());
        assert!(safe_slug("..").is_err());
        assert!(safe_slug("a..b").is_err());
    }

    #[test]
    fn an_id_may_not_start_with_a_dot_or_a_dash() {
        assert!(safe_slug(".hidden").is_err());
        assert!(safe_slug("-flag").is_err());
        assert!(safe_slug("_under").is_ok(), "an underscore is fine");
    }

    #[test]
    fn an_id_that_filters_away_to_nothing_is_refused() {
        assert!(safe_slug("").is_err());
        assert!(safe_slug("///").is_err());
    }

    #[test]
    fn an_id_is_capped_before_it_is_judged() {
        let long = "a".repeat(100);
        assert_eq!(safe_slug(&long).unwrap().chars().count(), 64);
    }

    #[test]
    fn an_entry_missing_a_slug_or_an_exe_is_skipped() {
        let index = serde_json::json!([
            {"slug": "a", "exe": "A.exe"},
            {"slug": "b"},
            {"exe": "C.exe"},
            {"slug": "", "exe": "D.exe"},
            "not an object",
        ]);
        let rows = index_entries(&index).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["slug"], "a");
    }

    #[test]
    fn one_unsafe_slug_costs_the_whole_catalogue() {
        // Not the same rule as a missing field. A slug that cannot be made
        // safe is the one value that becomes a URL, and a catalogue carrying
        // one is a catalogue to distrust.
        let index = serde_json::json!([
            {"slug": "fine", "exe": "A.exe"},
            {"slug": "..", "exe": "B.exe"},
        ]);
        assert!(index_entries(&index).is_err());
    }

    #[test]
    fn a_display_name_that_is_present_but_empty_falls_back_to_the_exe() {
        let index = serde_json::json!([
            {"slug": "a", "exe": "A.exe", "display_name": ""},
        ]);
        let rows = index_entries(&index).unwrap();
        assert_eq!(rows[0]["display_name"], "A.exe");
    }

    #[test]
    fn the_caps_count_characters() {
        let index = serde_json::json!([{
            "slug": "a",
            "exe": "\u{e9}".repeat(200),
            "note": "\u{e9}".repeat(400),
        }]);
        let rows = index_entries(&index).unwrap();
        assert_eq!(rows[0]["exe"].as_str().unwrap().chars().count(), 128);
        assert_eq!(rows[0]["note"].as_str().unwrap().chars().count(), 280);
    }

    #[test]
    fn a_profile_keeps_only_what_it_is_allowed_to_carry() {
        let profile = serde_json::json!({
            "exe": "Wow.exe",
            "nice_value": -5,
            "auto_created": true,
            "scx_scheduler": "lavd",
            "enabled": false,
        });
        let kept = shareable(&profile).unwrap();
        let object = kept.as_object().unwrap();
        assert!(object.contains_key("exe"));
        assert!(object.contains_key("nice_value"));
        assert!(!object.contains_key("auto_created"), "not shareable");
        assert!(!object.contains_key("scx_scheduler"), "not shareable");
        assert!(!object.contains_key("enabled"), "not shareable");
    }

    #[test]
    fn a_profile_with_no_exe_is_not_a_profile() {
        assert!(shareable(&serde_json::json!({"nice_value": -5})).is_err());
        assert!(shareable(&serde_json::json!({"exe": ""})).is_err());
        assert!(shareable(&serde_json::json!([])).is_err());
    }
}
