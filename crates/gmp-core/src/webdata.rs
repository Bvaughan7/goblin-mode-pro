//! Read-only lookups against two public Linux-gaming datasets.
//!
//! ProtonDB's compatibility tier, and whether AreWeAntiCheatYet has heard of
//! a game's anti-cheat. Both are anonymous GETs from a fixed host allowlist,
//! run in the GUI rather than the daemon, size-capped and cached.
//!
//! The fetching and the caching stay in Python. What is here is every check
//! and every projection that runs on what comes back - the parts that decide
//! what a remote document is allowed to turn into, and the parts a person
//! then reads.

use serde_json::{Map, Value};

use crate::pyfmt::is_digit;

/// The only hosts this module will talk to.
pub const ALLOWED_HOSTS: &[&str] = &["www.protondb.com", "raw.githubusercontent.com"];

/// The fields kept from a ProtonDB summary, in the order they are kept.
pub const PROTONDB_FIELDS: &[&str] = &[
    "tier",
    "score",
    "total",
    "confidence",
    "trendingTier",
    "bestReportedTier",
];

/// Whether a URL is one this module may fetch.
///
/// The host is taken as everything between the second and third slash, which
/// is deliberately literal: a URL carrying userinfo or a port renders a host
/// that is not in the allowlist and is refused rather than parsed into
/// something friendlier. `https://` is required separately, so an allowed host
/// over plain HTTP is still refused.
pub fn allowed_url(url: &str) -> bool {
    // The Python splits with maxsplit=3 and takes index 2, and the limit
    // makes no difference at that index - the third field is the same either
    // way.
    let host = match url.split('/').nth(2) {
        Some(host) if url.contains("://") => host,
        _ => "",
    };
    ALLOWED_HOSTS.contains(&host) && url.starts_with("https://")
}

/// A Steam AppID, reduced to its digits.
///
/// Everything that is not a digit is dropped rather than refused, because the
/// field it comes from is one a person types into. `limit` is the two callers'
/// difference and not an accident: the ProtonDB lookup caps the result at
/// twelve characters because it goes into a URL, and the anti-cheat lookup
/// does not because it is only ever compared against another string.
///
/// A digit is Python's digit - see [`crate::pyfmt::is_digit`] - which takes in
/// Arabic-Indic numerals and the circled forms and leaves out fractions. It is
/// not `is_ascii_digit`, and a Steam AppID that survives this filter is still
/// not guaranteed to be one.
pub fn steam_appid(value: &Value, limit: Option<usize>) -> String {
    let text = crate::pyfmt::scalar(value);
    let digits = text.chars().filter(|c| is_digit(*c));
    match limit {
        Some(limit) => digits.take(limit).collect(),
        None => digits.collect(),
    }
}

/// The fields a ProtonDB summary is reduced to.
///
/// A reply without a `tier` is not a game ProtonDB knows, whatever else it
/// holds. Every other field is taken as it comes, ABSENT BECOMING NULL rather
/// than being dropped, so the shape a caller reads is the same six keys every
/// time.
pub fn protondb_projection(data: &Value) -> Result<Value, String> {
    let object = data.as_object().ok_or("game not on ProtonDB")?;
    if !object.contains_key("tier") {
        return Err("game not on ProtonDB".to_string());
    }
    let mut out = Map::new();
    for field in PROTONDB_FIELDS {
        out.insert(
            (*field).to_string(),
            object.get(*field).cloned().unwrap_or(Value::Null),
        );
    }
    Ok(Value::Object(out))
}

/// Whether a cached file is still worth reading.
pub fn cache_is_fresh(mtime: f64, now: f64, ttl: f64) -> bool {
    now - mtime < ttl
}

/// The first anti-cheat entry matching an AppID or a name.
///
/// The AppID is compared against `storeIds.steam`, both stripped; the name is
/// compared lowercased and stripped. EITHER matches - a game found by name
/// when its AppID is unknown is the ordinary case for a non-Steam install.
///
/// The first match wins rather than the best one. The dataset holds one entry
/// per game, and a second entry matching would mean the dataset disagrees with
/// itself, which is not something to arbitrate here.
pub fn anticheat_match(db: &Value, name: &str, app_id: &str) -> Option<Value> {
    let name_lower = name.trim().to_lowercase();
    if app_id.is_empty() && name_lower.is_empty() {
        return None;
    }
    for game in db.as_array()? {
        let Some(game) = game.as_object() else {
            continue;
        };
        let store_id = game
            .get("storeIds")
            .and_then(Value::as_object)
            .map(|ids| crate::pyfmt::scalar(ids.get("steam").unwrap_or(&Value::Null)))
            .unwrap_or_default();
        let by_id = !app_id.is_empty() && store_id.trim() == app_id;
        let entry_name = crate::pyfmt::scalar(game.get("name").unwrap_or(&Value::Null));
        let by_name = !name_lower.is_empty() && entry_name.trim().to_lowercase() == name_lower;
        if !by_id && !by_name {
            continue;
        }
        let anticheats: Vec<Value> = game
            .get("anticheats")
            .and_then(Value::as_array)
            .map(|rows| {
                rows.iter()
                    .filter(|row| row.is_string())
                    .take(6)
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        let mut out = Map::new();
        out.insert(
            "name".into(),
            match game.get("name") {
                Some(value) if truthy(value) => value.clone(),
                _ => Value::String(name.to_string()),
            },
        );
        out.insert(
            "status".into(),
            Value::String(match game.get("status") {
                Some(value) if truthy(value) => crate::pyfmt::scalar(value),
                _ => "Unknown".to_string(),
            }),
        );
        out.insert("anticheats".into(), Value::Array(anticheats));
        out.insert(
            "reference".into(),
            match game.get("reference") {
                Some(value) if truthy(value) => value.clone(),
                _ => Value::String(String::new()),
            },
        );
        return Some(Value::Object(out));
    }
    None
}

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_two_hosts_over_https_are_fetchable() {
        assert!(allowed_url("https://www.protondb.com/api/v1/x.json"));
        assert!(allowed_url("https://raw.githubusercontent.com/a/b"));
        assert!(!allowed_url("http://www.protondb.com/x"), "https only");
        assert!(!allowed_url("https://protondb.com/x"), "the www matters");
        assert!(!allowed_url("https://evil.example/x"));
        assert!(!allowed_url(""));
    }

    #[test]
    fn a_host_dressed_up_to_look_allowed_is_not() {
        // Userinfo and a port both render a host string that is not in the
        // list, which is the point of comparing the literal text.
        assert!(!allowed_url("https://www.protondb.com@evil.example/x"));
        assert!(!allowed_url("https://evil.example/www.protondb.com/x"));
        assert!(!allowed_url("https://www.protondb.com:443/x"));
    }

    #[test]
    fn an_app_id_keeps_its_digits_and_loses_everything_else() {
        assert_eq!(
            steam_appid(&serde_json::json!("1091500"), Some(12)),
            "1091500"
        );
        assert_eq!(
            steam_appid(&serde_json::json!(" 109 1500 "), Some(12)),
            "1091500"
        );
        assert_eq!(
            steam_appid(&serde_json::json!("app/1091500/"), Some(12)),
            "1091500"
        );
        assert_eq!(steam_appid(&serde_json::json!("none"), Some(12)), "");
    }

    #[test]
    fn an_app_id_is_capped_only_where_it_becomes_a_url() {
        let long = serde_json::json!("1234567890123456");
        assert_eq!(steam_appid(&long, Some(12)), "123456789012");
        assert_eq!(steam_appid(&long, None), "1234567890123456");
    }

    #[test]
    fn a_number_is_an_app_id_too() {
        assert_eq!(
            steam_appid(&serde_json::json!(1091500), Some(12)),
            "1091500"
        );
    }

    #[test]
    fn a_reply_with_no_tier_is_not_a_game_protondb_knows() {
        assert!(protondb_projection(&serde_json::json!({"score": 9})).is_err());
        assert!(protondb_projection(&serde_json::json!([])).is_err());
        assert!(protondb_projection(&serde_json::json!({"tier": "gold"})).is_ok());
    }

    #[test]
    fn the_projection_is_always_the_same_six_keys() {
        let out = protondb_projection(&serde_json::json!({"tier": "gold", "extra": 1})).unwrap();
        let object = out.as_object().unwrap();
        assert_eq!(object.len(), PROTONDB_FIELDS.len());
        assert_eq!(object["tier"], "gold");
        assert_eq!(object["score"], Value::Null, "absent is null, not missing");
        assert!(
            !object.contains_key("extra"),
            "and nothing else comes along"
        );
    }

    #[test]
    fn a_cache_is_fresh_until_its_ttl_and_not_after() {
        assert!(cache_is_fresh(100.0, 150.0, 60.0));
        assert!(
            !cache_is_fresh(100.0, 160.0, 60.0),
            "exactly the ttl is stale"
        );
        assert!(!cache_is_fresh(100.0, 200.0, 60.0));
    }

    fn db() -> Value {
        serde_json::json!([
            {"name": "Apex Legends", "status": "Denied",
             "storeIds": {"steam": "1172470"},
             "anticheats": ["Easy Anti-Cheat"], "reference": "https://x"},
            {"name": "Other Game", "status": "Supported", "storeIds": {}},
        ])
    }

    #[test]
    fn a_game_is_found_by_its_app_id() {
        let hit = anticheat_match(&db(), "", "1172470").unwrap();
        assert_eq!(hit["name"], "Apex Legends");
        assert_eq!(hit["status"], "Denied");
    }

    #[test]
    fn a_game_is_found_by_its_name_whatever_its_case() {
        let hit = anticheat_match(&db(), "  APEX legends ", "").unwrap();
        assert_eq!(hit["name"], "Apex Legends");
    }

    #[test]
    fn a_game_nobody_asked_about_is_not_found() {
        assert!(anticheat_match(&db(), "", "").is_none());
        assert!(anticheat_match(&db(), "Nothing", "").is_none());
        assert!(anticheat_match(&db(), "", "999").is_none());
    }

    #[test]
    fn an_entry_missing_its_fields_still_answers_something() {
        let sparse = serde_json::json!([{"name": "Bare", "storeIds": {"steam": "5"}}]);
        let hit = anticheat_match(&sparse, "", "5").unwrap();
        assert_eq!(hit["status"], "Unknown");
        assert_eq!(hit["anticheats"], serde_json::json!([]));
        assert_eq!(hit["reference"], "");
    }

    #[test]
    fn only_the_first_six_anticheats_and_only_the_strings() {
        let noisy = serde_json::json!([{
            "name": "N", "storeIds": {"steam": "5"},
            "anticheats": ["a", 1, "b", null, "c", "d", "e", "f", "g", "h"],
        }]);
        let hit = anticheat_match(&noisy, "", "5").unwrap();
        assert_eq!(
            hit["anticheats"],
            serde_json::json!(["a", "b", "c", "d", "e", "f"])
        );
    }
}
