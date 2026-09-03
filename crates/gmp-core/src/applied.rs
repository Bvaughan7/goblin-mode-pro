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

#[cfg(test)]
mod tests {
    use super::*;

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
