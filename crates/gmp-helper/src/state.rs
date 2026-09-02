//! The pre-change snapshot in `/run/goblin-mode-pro/state.json`.
//!
//! Both implementations read and write this file, and either may find one
//! written by the other: a user who rolls back from the Rust helper to the
//! Python one mid-session must not lose the baseline their machine gets
//! restored to. That makes the on-disk format a compatibility surface, not an
//! implementation detail.
//!
//! Python's `json` module is permissive and `serde` is strict by default, and
//! that asymmetry is where a silent break lives. Three rules follow from it,
//! and all three are load-bearing:
//!
//! * `#[serde(default)]` everywhere - a file written by an OLDER version that
//!   lacks a field must load, not error.
//! * never `deny_unknown_fields` - a file written by a NEWER version that has
//!   extra fields must load too. That is the rollback path.
//! * unknown keys are preserved on rewrite, so a Rust helper cannot silently
//!   strip a field a newer Python helper added.
//!
//! Numbers get the same treatment. Python writes `4` and `4.0`
//! interchangeably depending on how a value was computed, so anything that
//! might have been through a float is parsed permissively rather than
//! demanding an integer token.

use std::collections::BTreeMap;
use std::path::Path;

use crate::sys;

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

/// What the helper recorded before it changed anything.
///
/// Field names are the wire format. They match `_snapshot()` and
/// `_snapshot_tdp()` in `helper/goblin_helper.py` exactly; renaming one here
/// silently breaks every machine that has a snapshot on disk.
#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct Snapshot {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub governor: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub epp: Option<String>,

    #[serde(
        default,
        deserialize_with = "lenient_u64",
        skip_serializing_if = "Option::is_none"
    )]
    pub pl1_uw: Option<u64>,

    #[serde(
        default,
        deserialize_with = "lenient_u64",
        skip_serializing_if = "Option::is_none"
    )]
    pub pl2_uw: Option<u64>,

    /// Every ryzenadj limit, each to be restored to its OWN original value.
    /// Restoring them all to STAPM would clamp the burst limit down to the
    /// sustained one and quietly cost headroom the machine shipped with.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ryzenadj_limits_mw: Option<BTreeMap<String, i64>>,

    /// Kept for a helper upgraded under a running daemon: an older snapshot
    /// recorded only STAPM, and `ResetTDP` still falls back to it.
    #[serde(
        default,
        deserialize_with = "lenient_i64",
        skip_serializing_if = "Option::is_none"
    )]
    pub ryzenadj_stapm_mw: Option<i64>,

    /// Anything this version does not know about, carried through untouched.
    /// Without this, a Rust helper reading and rewriting a snapshot written by
    /// a newer Python helper would drop the fields it did not recognise, and
    /// the user would lose whatever they described.
    #[serde(flatten)]
    pub unknown: BTreeMap<String, Value>,
}

impl Snapshot {
    /// The read side of the format. Written snapshots already flow through
    /// `to_json` below; these land when `RevertAll` is ported and is the
    /// thing that reads a baseline back.
    #[allow(dead_code, reason = "consumed by RevertAll")]
    pub fn from_json(text: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(text)
    }

    /// Serialize the way the Python helper does: `json.dumps(data, indent=2)`.
    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string_pretty(self)
    }

    pub fn load(path: &Path) -> Option<Self> {
        let text = std::fs::read_to_string(path).ok()?;
        Self::from_json(&text)
            .inspect_err(|err| tracing::warn!("{} is not valid state JSON: {err}", path.display()))
            .ok()
    }
}

/// Accept an integer, a float, or JSON null for a `u64` field.
///
/// `get_power_limits()` returns Python ints today, but a value that has been
/// through any arithmetic on the Python side can serialize as `107000000.0`,
/// and a strict `u64` deserializer rejects that with an error that surfaces to
/// the user as "the snapshot is corrupt" when nothing is wrong with it.
fn lenient_u64<'de, D: Deserializer<'de>>(de: D) -> Result<Option<u64>, D::Error> {
    Ok(match Option::<Value>::deserialize(de)? {
        None | Some(Value::Null) => None,
        Some(Value::Number(n)) => n.as_u64().or_else(|| {
            n.as_f64()
                .filter(|f| *f >= 0.0 && f.is_finite())
                .map(|f| f as u64)
        }),
        Some(_) => None,
    })
}

fn lenient_i64<'de, D: Deserializer<'de>>(de: D) -> Result<Option<i64>, D::Error> {
    Ok(match Option::<Value>::deserialize(de)? {
        None | Some(Value::Null) => None,
        Some(Value::Number(n)) => n
            .as_i64()
            .or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f as i64)),
        Some(_) => None,
    })
}

/// Write a snapshot back, creating the state directory if needed.
///
/// Used when a later operation adds to an existing baseline - the AMD limits,
/// which are only discoverable by running ryzenadj and so cannot be captured
/// in the general snapshot.
pub fn save(roots: &sys::Roots, snapshot: &Snapshot) -> std::io::Result<()> {
    std::fs::create_dir_all(&roots.state_dir)?;
    let json = snapshot
        .to_json()
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    std::fs::write(roots.state_file(), json)
}

/// Record the machine's baseline, unless one is already recorded.
///
/// Early-returns when the file exists, exactly like the Python `_snapshot()`.
/// That is what makes the baseline describe the state before the FIRST change
/// of the session rather than before the most recent one - `RevertAll` has to
/// restore what the user had, not what they had a moment ago.
///
/// It must be called only AFTER a request has been validated. Calling it first
/// is the bug the conformance suite found in the Python helper: a refused call
/// still wrote the file, and because of the early return above, the next
/// legitimate change then never recorded its own baseline.
pub fn capture_if_absent(roots: &sys::Roots) -> std::io::Result<()> {
    let file = roots.state_file();
    if file.exists() {
        return Ok(());
    }
    std::fs::create_dir_all(&roots.state_dir)?;

    let mut snap = Snapshot::default();
    // An empty governor is STORED, not omitted: get_governor answers "" on a
    // machine with no cpufreq and the Python helper records that answer.
    if let Ok(governor) = crate::cpu::get_governor(&roots.cpu) {
        snap.governor = Some(governor);
    }
    if let Some(path) = sys::cpu_leaf_paths(&roots.cpu, "energy_performance_preference").first() {
        if let Ok(epp) = sys::read_trimmed(path) {
            snap.epp = Some(epp);
        }
    }
    // Both limits or neither - half a power baseline cannot be restored.
    if let Ok((pl1, pl2)) = crate::power::get_power_limits(&roots.rapl) {
        snap.pl1_uw = Some(pl1);
        snap.pl2_uw = Some(pl2);
    }

    let json = snap
        .to_json()
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    std::fs::write(&file, json)?;
    tracing::info!("snapshot saved to {}", file.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Captured from the Python helper. See tests/fixtures/.
    const REAL: &str = include_str!("../../../tests/fixtures/state.python.json");

    /// Captured from the Rust writer. Its pair is state.python.json; the two
    /// describe the same machine and differ only in key order.
    const OURS: &str = include_str!("../../../tests/fixtures/state.rust.json");

    #[test]
    fn the_two_implementations_snapshots_are_semantically_equal() {
        // Key ORDER differs - Python preserves insertion order, this sorts -
        // and that is fine. Parsing both into the same struct is the check
        // that matters, because that is what either helper actually does with
        // a file the other wrote.
        assert_eq!(
            Snapshot::from_json(REAL).unwrap(),
            Snapshot::from_json(OURS).unwrap(),
            "the two helpers disagree about what they recorded"
        );
    }

    #[test]
    fn a_python_written_snapshot_survives_a_rust_rewrite_intact() {
        // THE ROLLBACK PATH, on real captured bytes rather than a literal: read
        // what Python wrote, write it back out, and lose nothing on the way.
        let original = Snapshot::from_json(REAL).unwrap();
        let round_tripped = Snapshot::from_json(&original.to_json().unwrap()).unwrap();
        assert_eq!(round_tripped, original);
        assert_eq!(
            round_tripped.ryzenadj_limits_mw,
            original.ryzenadj_limits_mw
        );
    }

    #[test]
    fn the_amd_limits_keep_their_own_values_across_the_two_writers() {
        // f33c437: restoring them all to STAPM costs the burst headroom the
        // machine shipped with. If either fixture ever collapses them, this
        // says so.
        for (label, text) in [("python", REAL), ("rust", OURS)] {
            let limits = Snapshot::from_json(text)
                .unwrap()
                .ryzenadj_limits_mw
                .unwrap();
            assert_eq!(limits.get("stapm-limit"), Some(&25_000), "{label}");
            assert_eq!(limits.get("fast-limit"), Some(&33_000), "{label}");
            assert_ne!(
                limits.get("fast-limit"),
                limits.get("stapm-limit"),
                "{label}"
            );
        }
    }

    #[test]
    fn an_unknown_key_in_a_real_python_file_is_carried_through() {
        // A NEWER Python helper adds a field; this one reads the file,
        // rewrites it, and must not drop what it did not understand.
        let mut value: serde_json::Value = serde_json::from_str(REAL).unwrap();
        value["future_knob"] = serde_json::json!({"added": "later"});
        let text = serde_json::to_string_pretty(&value).unwrap();
        let snap = Snapshot::from_json(&text).unwrap();
        assert_eq!(snap.governor.as_deref(), Some("powersave"));
        let rewritten = snap.to_json().unwrap();
        assert!(rewritten.contains("future_knob"), "{rewritten}");
        assert!(rewritten.contains("\"added\""), "{rewritten}");
    }

    #[test]
    fn reads_a_snapshot_written_by_the_python_helper() {
        let snap = Snapshot::from_json(REAL).expect("the real fixture must load");
        assert_eq!(snap.governor.as_deref(), Some("powersave"));
        assert_eq!(snap.pl1_uw, Some(107_000_000));
        assert_eq!(snap.pl2_uw, Some(107_000_000));
    }

    #[test]
    fn a_missing_field_is_not_an_error() {
        // An older helper wrote no EPP because the machine had no EPP files.
        let snap = Snapshot::from_json(r#"{"governor": "performance"}"#).unwrap();
        assert_eq!(snap.governor.as_deref(), Some("performance"));
        assert_eq!(snap.epp, None);
        assert_eq!(snap.pl1_uw, None);
    }

    #[test]
    fn an_empty_object_loads() {
        // _snapshot() writes {} on a machine with no cpufreq and no RAPL.
        assert_eq!(Snapshot::from_json("{}").unwrap(), Snapshot::default());
    }

    #[test]
    fn an_unknown_field_loads_and_survives_a_rewrite() {
        // THE ROLLBACK PATH. A newer Python helper adds a key; this one reads
        // the file, rewrites it, and must not drop what it did not understand.
        let text = r#"{"governor": "powersave", "future_knob": {"a": 1}}"#;
        let snap = Snapshot::from_json(text).unwrap();
        assert_eq!(snap.unknown.len(), 1);
        let round_tripped = Snapshot::from_json(&snap.to_json().unwrap()).unwrap();
        assert_eq!(round_tripped, snap);
        assert!(snap.to_json().unwrap().contains("future_knob"));
    }

    #[test]
    fn numbers_may_arrive_as_ints_or_floats() {
        // Python writes both, depending on how the value was computed.
        let ints = Snapshot::from_json(r#"{"pl1_uw": 4, "ryzenadj_stapm_mw": 12000}"#).unwrap();
        let floats =
            Snapshot::from_json(r#"{"pl1_uw": 4.0, "ryzenadj_stapm_mw": 12000.0}"#).unwrap();
        assert_eq!(ints.pl1_uw, Some(4));
        assert_eq!(floats.pl1_uw, Some(4));
        assert_eq!(ints.ryzenadj_stapm_mw, floats.ryzenadj_stapm_mw);
    }

    #[test]
    fn ryzenadj_limits_round_trip() {
        let text = r#"{"ryzenadj_limits_mw": {"stapm-limit": 25000, "fast-limit": 33000}}"#;
        let snap = Snapshot::from_json(text).unwrap();
        let limits = snap.ryzenadj_limits_mw.as_ref().unwrap();
        assert_eq!(limits.get("stapm-limit"), Some(&25_000));
        assert_eq!(limits.get("fast-limit"), Some(&33_000));
        assert_eq!(Snapshot::from_json(&snap.to_json().unwrap()).unwrap(), snap);
    }

    #[test]
    fn absent_fields_are_not_written_back_as_null() {
        // Python omits keys it has no value for; writing `"epp": null` instead
        // would be a format change the other implementation has to tolerate
        // for no reason.
        let json = Snapshot {
            governor: Some("powersave".into()),
            ..Default::default()
        }
        .to_json()
        .unwrap();
        assert!(!json.contains("null"), "{json}");
        assert!(!json.contains("epp"), "{json}");
    }

    /// Every committed fixture loads with the reader that owns its format.
    ///
    /// Enumerated at runtime rather than pinned with include_str!, so adding a
    /// fixture cannot silently escape this: a file nobody can read is not a
    /// compatibility record, it is a decoration.
    #[test]
    fn every_committed_fixture_loads() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures");
        let mut seen = 0;
        for entry in std::fs::read_dir(&dir).expect("the fixture directory must exist") {
            let path = entry.unwrap().path();
            let Some(name) = path.file_name().map(|n| n.to_string_lossy().into_owned()) else {
                continue;
            };
            if !name.ends_with(".json") {
                continue;
            }
            let text = std::fs::read_to_string(&path).unwrap();
            assert!(!text.trim().is_empty(), "{name} is empty");
            if name.starts_with("state.") {
                Snapshot::from_json(&text).unwrap_or_else(|e| panic!("{name}: {e}"));
            } else if name.starts_with("fans.") {
                serde_json::from_str::<serde_json::Value>(&text)
                    .unwrap_or_else(|e| panic!("{name}: {e}"));
            } else if name.starts_with("sysctls.") {
                serde_json::from_str::<BTreeMap<String, String>>(&text)
                    .unwrap_or_else(|e| panic!("{name}: {e}"));
            } else {
                panic!("{name} matches no reader; name it state.*, fans.* or sysctls.*");
            }
            seen += 1;
        }
        assert!(
            seen >= 6,
            "expected the six captured fixtures, found {seen}"
        );
    }
}
