//! What `applied.json` records, and what undoing it would mean.
//!
//! The daemon keeps its apply/revert bookkeeping in memory, which is fine
//! until the process holding it is gone. Two paths need the record on disk
//! instead: `--revert`, the systemd `ExecStop` and crash-recovery hook; and a
//! daemon starting up that finds a state file a previous instance left behind
//! after being killed without reverting.
//!
//! Both are recovery paths, which sets the tone for everything here: a state
//! file that cannot be understood must never be a reason to raise. The one
//! thing this code must not do is need recovering itself. So every reader
//! below treats an unusable file, and an unusable field within a usable file,
//! as an absence rather than an error - which is what the Python loader was
//! already written to do, and did not quite manage.
//!
//! The doing stays in Python: this module says what is recorded and which
//! steps that calls for, not how to talk to a compositor.

use serde_json::{Map, Value};

use crate::config::truthy;
pub use crate::pyfmt::{name, names};

/// The state a `--revert` would look at. `None` is "nothing usable to read".
pub type State = Map<String, Value>;

/// The compositor tweaks that outlive the daemon, and so have to be undone
/// from the file rather than from memory.
///
/// Named once because both "is there anything to undo" and the revert itself
/// ask for it, and a drift between those two is a revert that reports work it
/// does not do.
pub const COMPOSITOR_KEYS: &[&str] = &[
    "tearing_active",
    "vrr_active",
    "refresh_active",
    "x11_suspended",
];

/// Everything outside the compositor record whose presence means "applied".
const APPLIED_KEYS: &[&str] = &[
    "governor_applied",
    "power_applied",
    "tearing_applied",
    "adaptive_sync_applied",
    "refresh_cap_applied",
    "focus_mode",
    "scx_applied",
];

/// Read the state file's contents, or `None` when there is nothing usable.
///
/// A file that parses as JSON but is not an object is exactly as unusable as
/// one that does not parse, and means the same thing to every caller: no
/// record. Both are absences, not errors.
pub fn parse(raw: &str) -> Option<State> {
    match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(map)) => Some(map),
        _ => None,
    }
}

/// The compositor sub-record, which a hand-edited file may have replaced with
/// something that is not a mapping.
pub fn compositor_state(data: &State) -> Map<String, Value> {
    match data.get("compositor") {
        Some(Value::Object(map)) => map.clone(),
        _ => Map::new(),
    }
}

pub fn compositor_needs_restore(comp: &Map<String, Value>) -> bool {
    COMPOSITOR_KEYS
        .iter()
        .any(|key| comp.get(*key).is_some_and(truthy))
}

/// True when the file records anything actually applied - meaning a previous
/// daemon exited without reverting.
///
/// A clean shutdown leaves the file present with everything cleared, which is
/// **not** dirty.
pub fn is_dirty(state: Option<&State>) -> bool {
    // Python has an `if not data` early return here that an empty object also
    // takes. It is not reproduced, because it is not a rule: an object with no
    // keys falls through every check below to exactly the same answer. The
    // test for a cleared file pins that.
    let Some(data) = state else {
        return false;
    };
    if ["active", "reniced"]
        .iter()
        .any(|key| data.get(*key).is_some_and(truthy))
    {
        return true;
    }
    if APPLIED_KEYS
        .iter()
        .any(|key| data.get(*key).is_some_and(truthy))
    {
        return true;
    }
    compositor_needs_restore(&compositor_state(data))
}

/// What `--revert` would undo, as plain lines.
///
/// Reads the state and changes nothing, so it is safe at any time, and it is
/// what makes the state-driven revert inspectable in a bug report. Describes
/// the state file only: the helper's own root-owned snapshot in `/run` drives
/// an unconditional idempotent `RevertAll` that this process cannot read, so
/// it is reported as the fixed step it is rather than guessed at.
pub fn describe(state: Option<&State>, path: &str) -> Vec<String> {
    let mut lines = Vec::new();

    match state {
        None => lines.push(format!("no applied state at {path} - nothing recorded")),
        Some(data) if !is_dirty(Some(data)) => lines.push(format!(
            "{path} is present but clean (the last daemon shut down properly) - nothing to undo"
        )),
        Some(data) => {
            if data.get("active").is_some_and(truthy) {
                lines.push(format!("active games: {}", name(&data["active"])));
            }
            if data.get("reniced").is_some_and(truthy) {
                lines.push(format!(
                    "restore priority for pid(s): {}",
                    name(&data["reniced"])
                ));
            }
            for (key, text) in [
                ("governor_applied", "restore the CPU governor / EPP"),
                ("power_applied", "reset the CPU power limits"),
                ("tearing_applied", "turn tearing back off"),
                ("adaptive_sync_applied", "restore adaptive sync / VRR"),
                ("refresh_cap_applied", "restore the panel refresh rate"),
                (
                    "focus_mode",
                    "leave focus mode (indexer, DND, screen blanking)",
                ),
            ] {
                if data.get(key).is_some_and(truthy) {
                    lines.push(text.to_string());
                }
            }
            if data.get("scx_applied").is_some_and(truthy) {
                let previous = data.get("scx_previous").unwrap_or(&Value::Null);
                let tail = if truthy(previous) {
                    format!("switch back to scx_{}", name(previous))
                } else {
                    "return to the kernel's own scheduler".to_string()
                };
                lines.push(format!(
                    "CPU scheduler: stop scx_{} and {tail}",
                    name(&data["scx_applied"])
                ));
            }
            let comp = compositor_state(data);
            for (key, text) in [
                ("tearing_active", "compositor: tearing"),
                ("vrr_active", "compositor: VRR"),
                ("refresh_active", "compositor: refresh cap"),
                ("x11_suspended", "compositor: X11 compositing suspended"),
            ] {
                if comp.get(key).is_some_and(truthy) {
                    lines.push(format!("{text} -> restore recorded value"));
                }
            }
            if data.get("power_backend").is_some_and(truthy) {
                lines.push(format!(
                    "power backend in use: {}",
                    name(&data["power_backend"])
                ));
            }
        }
    }

    lines.push(
        "always: helper RevertAll (governor/EPP/RAPL/TDP/fans from \
         the helper's own /run snapshot - idempotent)"
            .to_string(),
    );
    lines
}

/// Which cold-restore steps the recorded state calls for.
///
/// Separated from the doing so the decisions can be checked without a
/// compositor, a session bus or a scheduler. This is the path that runs when
/// the machine is already in a bad way, and it is the one that has been wrong
/// before. The helper's `RevertAll` is absent because it is not a decision: it
/// runs unconditionally, off a snapshot this process cannot read, and it is
/// idempotent.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct RevertPlan {
    pub compositor: bool,
    pub compositor_state: Map<String, Value>,
    pub focus_mode: bool,
    pub scx: bool,
    pub scx_previous: Value,
}

pub fn revert_plan(state: Option<&State>) -> RevertPlan {
    static EMPTY: std::sync::LazyLock<State> = std::sync::LazyLock::new(Map::new);
    let data = state.unwrap_or(&EMPTY);
    let compositor_state = compositor_state(data);
    RevertPlan {
        compositor: compositor_needs_restore(&compositor_state),
        compositor_state,
        focus_mode: data.get("focus_mode").is_some_and(truthy),
        scx: data.get("scx_applied").is_some_and(truthy),
        scx_previous: data.get("scx_previous").cloned().unwrap_or(Value::Null),
    }
}

/// The record a running daemon leaves behind, so that a `--revert` from a
/// DIFFERENT process can undo what it did.
///
/// The reader half of this module has always existed; this is the writer, and
/// it matters for the same reason the reader does. The file is the only thing
/// standing between a daemon that dies badly and a machine left on the
/// performance governor with a compositor tearing hint set. During a cutover
/// the two implementations will be writing and reading each other's copies of
/// it, so this renders the document CPython renders - key order included -
/// rather than an equivalent one.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Record {
    pub active: Vec<String>,
    pub governor_applied: bool,
    pub power_applied: bool,
    pub power_backend: Option<String>,
    pub tearing_applied: bool,
    pub adaptive_sync_applied: bool,
    pub refresh_cap_applied: bool,
    pub focus_mode: bool,
    pub scx_applied: Option<String>,
    pub scx_previous: Option<String>,
    /// Executable to pid.
    pub reniced: Map<String, Value>,
    /// What the cold path needs to undo the compositor without the daemon's
    /// in-memory state.
    pub compositor: Map<String, Value>,
}

fn optional(value: &Option<String>) -> Value {
    match value {
        Some(text) => Value::String(text.clone()),
        None => Value::Null,
    }
}

/// The record as the file holds it: `json.dumps(..., indent=2)`, no trailing
/// newline.
///
/// The key order is the order the Python writes them in and is reproduced
/// rather than sorted. Nothing reads this file positionally, so the order does
/// not change what a revert does - but during the cutover the two
/// implementations' files should differ only where the machine differed, and a
/// re-ordered document is a diff that has to be explained every time.
pub fn render(record: &Record) -> String {
    let mut out = Map::new();
    out.insert(
        "active".into(),
        Value::Array(record.active.iter().cloned().map(Value::String).collect()),
    );
    out.insert("governor_applied".into(), record.governor_applied.into());
    out.insert("power_applied".into(), record.power_applied.into());
    out.insert("power_backend".into(), optional(&record.power_backend));
    out.insert("tearing_applied".into(), record.tearing_applied.into());
    out.insert(
        "adaptive_sync_applied".into(),
        record.adaptive_sync_applied.into(),
    );
    out.insert(
        "refresh_cap_applied".into(),
        record.refresh_cap_applied.into(),
    );
    out.insert("focus_mode".into(), record.focus_mode.into());
    out.insert("scx_applied".into(), optional(&record.scx_applied));
    out.insert("scx_previous".into(), optional(&record.scx_previous));
    out.insert("reniced".into(), Value::Object(record.reniced.clone()));
    out.insert(
        "compositor".into(),
        Value::Object(record.compositor.clone()),
    );
    crate::pyjson::dumps_indented(&Value::Object(out), 2)
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- the record a running daemon leaves behind ------------------------

    #[test]
    fn a_clean_record_reads_back_as_not_dirty() {
        // The round trip that matters: what this writer produces for a daemon
        // holding nothing must be what the reader calls clean. A mismatch
        // between the two halves is a revert that runs on every start, or one
        // that never runs at all.
        let text = render(&Record::default());
        let state = parse(&text).expect("the writer produces a readable file");
        assert!(!is_dirty(Some(&state)));
    }

    #[test]
    fn a_record_with_work_in_it_reads_back_as_dirty() {
        let text = render(&Record {
            governor_applied: true,
            ..Record::default()
        });
        assert!(is_dirty(parse(&text).as_ref()));
    }

    #[test]
    fn the_compositor_record_survives_the_round_trip() {
        // It is the half that cannot be rebuilt from memory by the process
        // doing the reverting, which is the whole reason it is written down.
        let mut comp = Map::new();
        comp.insert("tearing_active".into(), Value::Bool(true));
        comp.insert("refresh_active".into(), Value::from(60));
        comp.insert("saved_refresh_hz".into(), Value::from(144));
        let text = render(&Record {
            compositor: comp.clone(),
            ..Record::default()
        });
        let state = parse(&text).expect("readable");
        assert_eq!(compositor_state(&state), comp);
        assert!(compositor_needs_restore(&compositor_state(&state)));
    }

    #[test]
    fn nothing_applied_is_written_as_null_rather_than_left_out() {
        // A key that is absent and a key that is null mean the same thing to
        // the reader, and only one of them tells someone reading the file that
        // the daemon considered it.
        let text = render(&Record::default());
        assert!(text.contains("\"power_backend\": null"), "{text}");
        assert!(text.contains("\"scx_applied\": null"), "{text}");
        assert!(text.contains("\"scx_previous\": null"), "{text}");
    }

    #[test]
    fn an_empty_record_still_names_every_field() {
        let text = render(&Record::default());
        for key in [
            "active",
            "governor_applied",
            "power_applied",
            "power_backend",
            "tearing_applied",
            "adaptive_sync_applied",
            "refresh_cap_applied",
            "focus_mode",
            "scx_applied",
            "scx_previous",
            "reniced",
            "compositor",
        ] {
            assert!(
                text.contains(&format!("\"{key}\"")),
                "{key} missing from {text}"
            );
        }
    }

    fn state(raw: &str) -> Option<State> {
        parse(raw)
    }

    #[test]
    fn a_file_that_is_not_an_object_is_an_absence_not_an_error() {
        // Every one of these took down the daemon at startup before the fix,
        // through a path whose entire job is recovering from the last crash.
        for raw in ["[1, 2]", "\"hello\"", "5", "true", "null"] {
            assert_eq!(parse(raw), None, "{raw}");
            assert!(!is_dirty(parse(raw).as_ref()), "{raw}");
        }
    }

    #[test]
    fn unparseable_json_is_the_same_absence() {
        assert_eq!(parse("{\"active\": [\"Wow.exe\""), None);
        assert_eq!(parse(""), None);
    }

    #[test]
    fn a_compositor_record_that_is_not_a_mapping_is_ignored() {
        for raw in [
            r#"{"compositor": "yes"}"#,
            r#"{"compositor": [1]}"#,
            r#"{"compositor": 3}"#,
            r#"{"compositor": true}"#,
        ] {
            assert!(!is_dirty(state(raw).as_ref()), "{raw}");
        }
    }

    #[test]
    fn an_empty_or_cleared_file_is_clean() {
        assert!(!is_dirty(state("{}").as_ref()));
        assert!(!is_dirty(
            state(r#"{"active": [], "reniced": {}}"#).as_ref()
        ));
        assert!(!is_dirty(
            state(r#"{"governor_applied": false, "compositor": {}}"#).as_ref()
        ));
    }

    #[test]
    fn anything_recorded_makes_it_dirty() {
        assert!(is_dirty(state(r#"{"active": ["Wow.exe"]}"#).as_ref()));
        assert!(is_dirty(state(r#"{"reniced": {"123": -5}}"#).as_ref()));
        assert!(is_dirty(state(r#"{"focus_mode": true}"#).as_ref()));
        assert!(is_dirty(
            state(r#"{"compositor": {"vrr_active": true}}"#).as_ref()
        ));
    }

    #[test]
    fn a_string_field_is_one_name_not_its_characters() {
        assert_eq!(names(&Value::String("Wow".into())), vec!["Wow"]);
    }

    #[test]
    fn a_mapping_lists_its_keys() {
        let value: Value = serde_json::from_str(r#"{"123": -5, "456": -5}"#).unwrap();
        assert_eq!(names(&value), vec!["123", "456"]);
    }

    #[test]
    fn a_scalar_is_a_single_name() {
        assert_eq!(names(&serde_json::json!(5)), vec!["5"]);
        assert_eq!(names(&serde_json::json!(5.0)), vec!["5.0"]);
        assert_eq!(names(&serde_json::json!(true)), vec!["True"]);
    }

    #[test]
    fn a_falsy_field_names_nothing() {
        for raw in ["null", "false", "0", "\"\"", "[]", "{}"] {
            let value: Value = serde_json::from_str(raw).unwrap();
            assert!(names(&value).is_empty(), "{raw}");
        }
    }

    #[test]
    fn the_always_line_is_there_whatever_the_file_says() {
        for raw in ["{}", r#"{"focus_mode": true}"#] {
            let lines = describe(state(raw).as_ref(), "/x/applied.json");
            assert!(lines
                .last()
                .unwrap()
                .starts_with("always: helper RevertAll"));
        }
        let lines = describe(None, "/x/applied.json");
        assert_eq!(
            lines[0],
            "no applied state at /x/applied.json - nothing recorded"
        );
    }

    #[test]
    fn the_plan_reads_the_same_compositor_rule_as_the_dirty_check() {
        // These two used to spell the four-key check out separately. If they
        // drift, --revert reports compositor work it then does not do.
        let raw = r#"{"compositor": {"refresh_active": true}}"#;
        assert!(is_dirty(state(raw).as_ref()));
        assert!(revert_plan(state(raw).as_ref()).compositor);
    }

    #[test]
    fn a_plan_off_no_state_asks_for_nothing() {
        let plan = revert_plan(None);
        assert!(!plan.compositor && !plan.focus_mode && !plan.scx);
        assert_eq!(plan.scx_previous, Value::Null);
    }
}
