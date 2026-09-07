//! The incident record, its on-disk log, and the payload that leaves the
//! machine.
//!
//! A port of the pure half of `src/goblinmode/incidents.py`. Two things
//! deliberately stay behind in Python for now, because neither belongs in a
//! crate defined as "no sysfs, testable from fixture strings":
//!
//! * `_system_info` probes DMI, /etc/os-release, /proc/cpuinfo and nvidia-smi.
//!   [`build_llm_payload`] takes the result as an argument instead, which also
//!   makes it testable against a fixed machine description.
//! * `copy_to_clipboard` shells out to wl-copy or xclip. That is a job for
//!   whichever layer has a user in front of it.
//!
//! The timestamp is likewise supplied by the caller rather than read from a
//! clock here, so an incident can be reconstructed exactly from a log line.

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

pub const SCHEMA: &str = "gmp.incident.v1";

/// The instruction the payload is wrapped in. Carried across verbatim - it is
/// the prompt an external model actually receives, and paraphrasing it would
/// change the answers users get back.
pub const SYSTEM_PROMPT: &str = r"You are a Linux gaming performance diagnostician. The following JSON was produced by Goblin Mode Pro during a game session (system details are in the 'system' object). Given the incident, the metric window leading up to it, the log tail and the performance tweaks that were active, identify the most likely bottleneck - thermal, power-limit (RAPL PL1/PL2), GPU driver, VRAM exhaustion / host-memory fallback, PCIe link down-training, VKD3D/DXVK pipeline caching, CPU, or I/O - and give concrete remediation steps for that distro, ordered by expected impact. Use gpu_state if present. Be concise.";

/// A single noteworthy event during gameplay.
///
/// `kind` is thermal_throttle | power_limit | gpu_throttle | gpu_fault.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Incident {
    pub kind: String,
    pub detail: String,
    #[serde(default)]
    pub game: String,
    #[serde(default)]
    pub game_pid: Option<i64>,
    #[serde(default)]
    pub ts: String,
    #[serde(default)]
    pub metrics_window: Vec<Value>,
    #[serde(default)]
    pub logs_tail: Vec<String>,
    #[serde(default)]
    pub active_tweaks: Map<String, Value>,
    #[serde(default)]
    pub gpu_state: Map<String, Value>,
    #[serde(default)]
    pub fps_trace: Vec<Value>,
}

impl Incident {
    /// The JSONL form written to the incident log.
    ///
    /// `gpu_state` and `fps_trace` are omitted when empty rather than
    /// written as `{}` and `[]`. A payload is read by a person and by a
    /// model, and an empty section is noise to both.
    pub fn as_dict(&self) -> Value {
        let mut out = Map::new();
        out.insert("kind".into(), json!(self.kind));
        out.insert("detail".into(), json!(self.detail));
        out.insert("game".into(), json!(self.game));
        out.insert("game_pid".into(), json!(self.game_pid));
        out.insert("ts".into(), json!(self.ts));
        out.insert("metrics_window".into(), json!(self.metrics_window));
        out.insert("logs_tail".into(), json!(self.logs_tail));
        out.insert("active_tweaks".into(), json!(self.active_tweaks));
        if !self.gpu_state.is_empty() {
            out.insert("gpu_state".into(), json!(self.gpu_state));
        }
        if !self.fps_trace.is_empty() {
            out.insert("fps_trace".into(), json!(self.fps_trace));
        }
        Value::Object(out)
    }
}

/// Downsample `rows` to at most `target`, always keeping the last one.
///
/// The last row is preserved explicitly because the incident happened at the
/// END of the window - dropping it would remove the moment being diagnosed.
pub fn thin(rows: &[Value], target: usize) -> Vec<Value> {
    if rows.len() <= target || target == 0 {
        return rows.to_vec();
    }
    let step = rows.len() as f64 / target as f64;
    let mut out: Vec<Value> = (0..target)
        .map(|i| rows[(i as f64 * step) as usize].clone())
        .collect();
    let last = out.len() - 1;
    out[last] = rows[rows.len() - 1].clone();
    out
}

/// How many metric samples and frame-trace points survive into a payload.
const METRICS_TARGET: usize = 20;
const FPS_TRACE_TARGET: usize = 30;
/// How many log lines from the tail are carried.
const LOG_TAIL: usize = 20;

/// Package one incident for an external model.
///
/// `system` is the machine description (see the module docs on why it is a
/// parameter). `redact` is applied to the trigger detail AND to every log
/// line, because the tail is raw Proton output and the likeliest place a
/// username appears.
pub fn build_llm_payload(
    incident: &Incident,
    system: &Value,
    model_hint: &str,
    redact: impl Fn(&str) -> String,
) -> String {
    let mut payload = Map::new();
    payload.insert("schema".into(), json!(SCHEMA));
    payload.insert("timestamp".into(), json!(incident.ts));
    payload.insert("system".into(), system.clone());
    payload.insert(
        "game".into(),
        json!({"exe": incident.game, "pid": incident.game_pid}),
    );
    payload.insert(
        "trigger".into(),
        json!({"type": incident.kind, "detail": redact(&incident.detail)}),
    );
    payload.insert(
        "metrics_window".into(),
        json!(thin(&incident.metrics_window, METRICS_TARGET)),
    );
    let tail_from = incident.logs_tail.len().saturating_sub(LOG_TAIL);
    payload.insert(
        "logs_tail".into(),
        json!(incident.logs_tail[tail_from..]
            .iter()
            .map(|line| redact(line))
            .collect::<Vec<_>>()),
    );
    payload.insert("active_tweaks".into(), json!(incident.active_tweaks));
    if !incident.gpu_state.is_empty() {
        payload.insert("gpu_state".into(), json!(incident.gpu_state));
    }
    if !incident.fps_trace.is_empty() {
        payload.insert(
            "fps_trace".into(),
            json!(thin(&incident.fps_trace, FPS_TRACE_TARGET)),
        );
    }
    if !model_hint.is_empty() {
        payload.insert("user_note".into(), json!(model_hint));
    }

    format!(
        "{SYSTEM_PROMPT}\n\n```json\n{}\n```\n",
        crate::pyjson::dumps_indented(&Value::Object(payload), 2)
    )
}

/// Keep the on-disk log bounded: trim to the newest [`IncidentLog::MAX_KEEP`]
/// once it grows past this.
const MAX_BYTES: u64 = 2 * 1024 * 1024;
const MAX_KEEP: usize = 200;

/// A bounded ring of recent incidents, appended to a JSONL file.
///
/// Persistence is BEST EFFORT and deliberately so: a read-only home or a full
/// disk must not cost the user the incident the GUI is about to show them, so
/// a write failure is logged and swallowed rather than propagated.
pub struct IncidentLog {
    ring: std::collections::VecDeque<Incident>,
    maxlen: usize,
    path: std::path::PathBuf,
}

impl IncidentLog {
    pub const MAX_BYTES: u64 = MAX_BYTES;
    pub const MAX_KEEP: usize = MAX_KEEP;

    pub fn new(path: impl Into<std::path::PathBuf>, maxlen: usize) -> Self {
        Self {
            ring: std::collections::VecDeque::new(),
            maxlen,
            path: path.into(),
        }
    }

    pub fn add(&mut self, incident: Incident) {
        if self.ring.len() == self.maxlen {
            self.ring.pop_front();
        }
        let persisted = self.persist(&incident);
        self.ring.push_back(incident);
        if let Err(err) = persisted {
            // The in-memory copy survives; see the type's docs.
            eprintln!("could not persist incident: {err}");
        }
    }

    pub fn latest(&self) -> Option<&Incident> {
        self.ring.back()
    }

    pub fn all(&self) -> Vec<Incident> {
        self.ring.iter().cloned().collect()
    }

    fn persist(&self, incident: &Incident) -> std::io::Result<()> {
        use std::io::Write;
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        self.rotate_if_big();
        let mut fh = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        writeln!(fh, "{}", incident.as_dict())
    }

    /// Trim the file to its newest entries once it grows past the cap.
    ///
    /// Failures are ignored: a log that cannot be trimmed is a log that grows,
    /// which is a smaller problem than an incident that cannot be recorded.
    fn rotate_if_big(&self) {
        let Ok(meta) = std::fs::metadata(&self.path) else {
            return;
        };
        if meta.len() < MAX_BYTES {
            return;
        }
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return;
        };
        let lines: Vec<&str> = text.lines().collect();
        let keep = lines.len().saturating_sub(MAX_KEEP);
        let _ = std::fs::write(&self.path, lines[keep..].join("\n") + "\n");
    }

    /// The newest `limit` entries on disk, as raw JSON.
    ///
    /// A line that will not parse is SKIPPED, not fatal. The file is appended
    /// to by a long-running daemon, and a truncated write should cost that one
    /// line rather than the whole history.
    pub fn load_history(&self, limit: usize) -> Vec<Value> {
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return Vec::new();
        };
        let lines: Vec<&str> = text.lines().collect();
        let from = lines.len().saturating_sub(limit);
        lines[from..]
            .iter()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect()
    }
}

/// One thing the daemon should do when an incident is raised, in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RaiseStep {
    /// Put it in the bounded log, which persists it best-effort.
    File,
    /// Tell whoever is watching the bus.
    Emit,
    /// Keep the thirty seconds around it, if something is recording.
    SaveClip { kind: String },
    Notify {
        title: String,
        body: String,
        urgency: u8,
        tag: String,
    },
}

/// The kinds worth having the thirty seconds before them on video.
const WORTH_A_CLIP: &[&str] = &["gpu_fault", "fps_dip", "thermal_throttle"];

/// The kinds worth interrupting someone mid-game for, and what to call them.
///
/// Not the same list as [`WORTH_A_CLIP`], and the two disagreeing is the
/// point. A frame-rate dip is worth watching back and is not worth a popup -
/// the user was there for it. VRAM left behind after a game exits is worth
/// saying out loud and there is nothing to watch.
const WORTH_SAYING: &[(&str, &str)] = &[
    ("gpu_fault", "GPU / driver fault"),
    ("thermal_throttle", "Thermal throttling"),
    ("vram_not_freed", "VRAM not released after exit"),
];

/// How much of the detail a notification carries.
const NOTIFY_DETAIL_CHARS: usize = 160;

/// Everything raising an incident implies, in order.
///
/// Filing and emitting happen whatever it was. The two judgements are which
/// kinds are worth a clip and which are worth a notification, and only one
/// kind in the union of those lists is critical: an actual driver fault. KDE
/// renders critical notifications as resident popups that ignore the expire
/// timeout and bypass the user's per-app mute, and routine thermal throttling
/// on a gaming laptop does not warrant that - it also made a stuck popup
/// impossible to dismiss short of restarting plasmashell.
pub fn raise_plan(kind: &str, detail: &str, clip_running: bool) -> Vec<RaiseStep> {
    let mut plan = vec![RaiseStep::File, RaiseStep::Emit];
    if clip_running && WORTH_A_CLIP.contains(&kind) {
        plan.push(RaiseStep::SaveClip {
            kind: kind.to_string(),
        });
    }
    if let Some((_, title)) = WORTH_SAYING.iter().find(|(k, _)| *k == kind) {
        plan.push(RaiseStep::Notify {
            title: (*title).to_string(),
            // Python slices a str by CODE POINT. Taking 160 BYTES would cut a
            // detail with any non-ASCII in it short, and cut it inside a
            // character it would panic.
            body: detail.chars().take(NOTIFY_DETAIL_CHARS).collect(),
            urgency: if kind == "gpu_fault" { 2 } else { 1 },
            tag: "incident".to_string(),
        });
    }
    plan
}

/// What the incident says was running: every active executable, joined.
///
/// The field is read by a person and by a model, and an empty string reads as
/// missing data where `(none)` reads as an answer.
pub fn game_label(active_exes: &[String]) -> String {
    if active_exes.is_empty() {
        return "(none)".to_string();
    }
    active_exes.join(", ")
}

/// The pid the incident is filed against: the first game that launched.
///
/// Not the game the incident is about - the daemon does not know which that
/// is. Arbitrary, but the same arbitrary choice every time. A zero is the
/// observer recording a game whose pid it never learned, and it is no pid
/// rather than process zero.
pub fn game_pid(pids: &[i64]) -> Option<i64> {
    match pids.first() {
        Some(0) | None => None,
        Some(pid) => Some(*pid),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- what raising an incident does -----------------------------------

    /// Every kind the daemon can raise. Two lists overlap without matching,
    /// which is the whole shape of this decision.
    const KINDS: &[&str] = &[
        "thermal_throttle",
        "power_limit",
        "gpu_throttle",
        "gpu_fault",
        "fps_dip",
        "fps_recovered",
        "vram_not_freed",
    ];

    #[test]
    fn every_incident_is_filed_and_emitted_whatever_it_is() {
        for kind in KINDS {
            let plan = raise_plan(kind, "something happened", false);
            assert_eq!(&plan[..2], &[RaiseStep::File, RaiseStep::Emit], "{kind}");
        }
    }

    #[test]
    fn a_clip_is_saved_for_the_three_kinds_worth_watching_back() {
        for kind in KINDS {
            let saved = raise_plan(kind, "d", true)
                .iter()
                .any(|s| matches!(s, RaiseStep::SaveClip { .. }));
            let want = matches!(*kind, "gpu_fault" | "fps_dip" | "thermal_throttle");
            assert_eq!(saved, want, "{kind}");
        }
    }

    #[test]
    fn nothing_is_saved_when_nothing_is_recording() {
        for kind in KINDS {
            assert!(
                !raise_plan(kind, "d", false)
                    .iter()
                    .any(|s| matches!(s, RaiseStep::SaveClip { .. })),
                "{kind}"
            );
        }
    }

    #[test]
    fn only_three_kinds_are_worth_interrupting_someone_for() {
        for kind in KINDS {
            let notified = raise_plan(kind, "d", true)
                .iter()
                .any(|s| matches!(s, RaiseStep::Notify { .. }));
            let want = matches!(*kind, "gpu_fault" | "thermal_throttle" | "vram_not_freed");
            assert_eq!(notified, want, "{kind}");
        }
    }

    #[test]
    fn a_dip_is_recorded_but_never_announced() {
        // The two lists are not the same list. A dip is worth having the clip
        // of and is not worth a popup; VRAM left behind is worth the popup and
        // there is nothing to watch back.
        let dip = raise_plan("fps_dip", "d", true);
        assert!(dip.contains(&RaiseStep::SaveClip {
            kind: "fps_dip".into()
        }));
        assert!(!dip.iter().any(|s| matches!(s, RaiseStep::Notify { .. })));

        let vram = raise_plan("vram_not_freed", "d", true);
        assert!(!vram.iter().any(|s| matches!(s, RaiseStep::SaveClip { .. })));
        assert!(vram.iter().any(|s| matches!(s, RaiseStep::Notify { .. })));
    }

    #[test]
    fn only_a_driver_fault_is_critical() {
        // KDE renders critical notifications as resident popups that ignore
        // the expire timeout and bypass a per-app mute. Routine throttling on
        // a gaming laptop does not warrant that.
        let urgency = |kind: &str| {
            raise_plan(kind, "d", false).iter().find_map(|s| match s {
                RaiseStep::Notify { urgency, .. } => Some(*urgency),
                _ => None,
            })
        };
        assert_eq!(urgency("gpu_fault"), Some(2));
        assert_eq!(urgency("thermal_throttle"), Some(1));
        assert_eq!(urgency("vram_not_freed"), Some(1));
    }

    #[test]
    fn the_notification_says_what_happened_in_words() {
        let title = |kind: &str| {
            raise_plan(kind, "d", false).iter().find_map(|s| match s {
                RaiseStep::Notify { title, .. } => Some(title.clone()),
                _ => None,
            })
        };
        assert_eq!(title("gpu_fault").as_deref(), Some("GPU / driver fault"));
        assert_eq!(
            title("thermal_throttle").as_deref(),
            Some("Thermal throttling")
        );
        assert_eq!(
            title("vram_not_freed").as_deref(),
            Some("VRAM not released after exit")
        );
    }

    #[test]
    fn a_long_detail_is_cut_to_160_characters_not_160_bytes() {
        // Python slices a str by CODE POINT. A detail with any non-ASCII in
        // it - a game's title, a line of Proton output - would be cut short by
        // a byte slice, and cut in the MIDDLE of a character it would panic.
        let detail = "\u{e9}".repeat(200);
        let body = raise_plan("gpu_fault", &detail, false)
            .iter()
            .find_map(|s| match s {
                RaiseStep::Notify { body, .. } => Some(body.clone()),
                _ => None,
            })
            .unwrap();
        assert_eq!(body.chars().count(), 160);
        assert_eq!(body, "\u{e9}".repeat(160));
    }

    #[test]
    fn a_short_detail_is_left_alone() {
        let body = raise_plan("gpu_fault", "brief", false)
            .iter()
            .find_map(|s| match s {
                RaiseStep::Notify { body, .. } => Some(body.clone()),
                _ => None,
            })
            .unwrap();
        assert_eq!(body, "brief");
    }

    // ---- what the incident says it happened to ---------------------------

    #[test]
    fn the_game_is_every_active_exe_joined() {
        assert_eq!(
            game_label(&["Wow.exe".to_string(), "rs2client".to_string()]),
            "Wow.exe, rs2client"
        );
    }

    #[test]
    fn nothing_running_is_the_word_none_rather_than_an_empty_field() {
        // The field is read by a human and by a model. An empty string reads
        // as missing data; "(none)" reads as an answer.
        assert_eq!(game_label(&[]), "(none)");
    }

    #[test]
    fn the_pid_is_the_first_game_that_launched() {
        // Not the game the incident is about - the daemon does not know which
        // that is. Arbitrary, but it is the same arbitrary choice every time.
        assert_eq!(game_pid(&[4242, 99]), Some(4242));
    }

    #[test]
    fn a_pid_of_zero_is_no_pid_at_all() {
        // The observer records 0 for a game whose pid it never learned, and
        // the Python turns that into None on the way out. A literal 0 in the
        // field would be read as a process.
        assert_eq!(game_pid(&[0]), None);
        assert_eq!(game_pid(&[]), None);
    }

    fn incident() -> Incident {
        Incident {
            kind: "thermal_throttle".into(),
            detail: "package hit 97C".into(),
            ts: "2026-09-03T00:00:00+00:00".into(),
            ..Default::default()
        }
    }

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("gmp-inc-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir.join("incidents.jsonl")
    }

    fn plain(text: &str) -> String {
        text.to_owned()
    }

    // ---- translated from tests/test_incidents.py -------------------------

    #[test]
    fn optional_sections_are_omitted_when_empty() {
        let d = incident().as_dict();
        assert!(d.get("gpu_state").is_none());
        assert!(d.get("fps_trace").is_none());
        assert_eq!(d["kind"], "thermal_throttle");
    }

    #[test]
    fn optional_sections_appear_when_populated() {
        let mut i = incident();
        i.gpu_state.insert("util".into(), json!(99));
        i.fps_trace.push(json!({"fps": 30}));
        let d = i.as_dict();
        assert_eq!(d["gpu_state"], json!({"util": 99}));
        assert_eq!(d["fps_trace"], json!([{"fps": 30}]));
    }

    #[test]
    fn the_ring_is_bounded_and_keeps_the_newest() {
        let path = scratch("ring");
        let mut log = IncidentLog::new(&path, 3);
        for n in 0..5 {
            let mut i = incident();
            i.detail = format!("n{n}");
            log.add(i);
        }
        let details: Vec<String> = log.all().into_iter().map(|i| i.detail).collect();
        assert_eq!(details, ["n2", "n3", "n4"]);
        assert_eq!(log.latest().unwrap().detail, "n4");
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn latest_is_none_before_anything_happens() {
        assert!(IncidentLog::new(scratch("empty"), 10).latest().is_none());
    }

    #[test]
    fn an_unwritable_log_does_not_lose_the_in_memory_incident() {
        // A read-only home must not cost the user the incident the GUI is
        // about to show them.
        let mut log = IncidentLog::new("/proc/nonexistent/incidents.jsonl", 10);
        log.add(incident());
        assert_eq!(log.all().len(), 1);
    }

    #[test]
    fn history_survives_a_corrupt_line() {
        let path = scratch("corrupt");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "{\"kind\": \"a\"}\n{not json\n{\"kind\": \"b\"}\n").unwrap();
        let kinds: Vec<String> = IncidentLog::new(&path, 10)
            .load_history(100)
            .into_iter()
            .map(|r| r["kind"].as_str().unwrap_or_default().to_owned())
            .collect();
        assert_eq!(kinds, ["a", "b"]);
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn history_is_empty_when_there_is_no_file() {
        assert!(IncidentLog::new(scratch("absent"), 10)
            .load_history(100)
            .is_empty());
    }

    #[test]
    fn history_returns_the_newest_entries() {
        let path = scratch("newest");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let body: String = (0..10)
            .map(|i| format!("{{\"kind\": \"k{i}\"}}\n"))
            .collect();
        std::fs::write(&path, body).unwrap();
        let kinds: Vec<String> = IncidentLog::new(&path, 10)
            .load_history(3)
            .into_iter()
            .map(|r| r["kind"].as_str().unwrap_or_default().to_owned())
            .collect();
        assert_eq!(kinds, ["k7", "k8", "k9"]);
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    // ---- thinning ---------------------------------------------------------

    #[test]
    fn short_input_is_returned_untouched() {
        let rows: Vec<Value> = (0..5).map(|i| json!({"i": i})).collect();
        assert_eq!(thin(&rows, 20), rows);
    }

    #[test]
    fn the_last_row_always_survives() {
        // The incident happened at the END of the window. Dropping the last
        // sample would remove the moment being diagnosed.
        let rows: Vec<Value> = (0..1000).map(|i| json!({"i": i})).collect();
        let thinned = thin(&rows, 20);
        assert_eq!(thinned.len(), 20);
        assert_eq!(thinned[19], rows[999]);
        assert_eq!(thinned[0], rows[0]);
    }

    // ---- the payload ------------------------------------------------------

    fn payload_of(i: &Incident, hint: &str) -> Value {
        let text = build_llm_payload(i, &json!({"distro": "test"}), hint, plain);
        let body = text
            .split("json\n")
            .nth(1)
            .unwrap()
            .rsplit_once('\n')
            .unwrap()
            .0;
        let body = body.trim_end_matches("```").trim_end();
        serde_json::from_str(body).expect("the payload must be valid JSON")
    }

    #[test]
    fn the_schema_is_declared() {
        assert_eq!(payload_of(&incident(), "")["schema"], SCHEMA);
    }

    #[test]
    fn redaction_is_applied_to_the_detail_and_the_log_tail() {
        let mut i = incident();
        i.detail = "crash in /home/alice/x".into();
        i.logs_tail = vec!["err /home/alice/y.dll".into()];
        let text = build_llm_payload(&i, &json!({}), "", |s| s.replace("alice", "<user>"));
        assert!(!text.contains("alice"), "{text}");
    }

    #[test]
    fn only_the_last_twenty_log_lines_are_included() {
        let mut i = incident();
        i.logs_tail = (0..50).map(|n| format!("line {n}")).collect();
        let p = payload_of(&i, "");
        assert_eq!(p["logs_tail"].as_array().unwrap().len(), 20);
        assert_eq!(p["logs_tail"][19], "line 49");
    }

    #[test]
    fn the_metric_window_is_thinned() {
        let mut i = incident();
        i.metrics_window = (0..500).map(|n| json!({"i": n})).collect();
        assert_eq!(
            payload_of(&i, "")["metrics_window"]
                .as_array()
                .unwrap()
                .len(),
            20
        );
    }

    #[test]
    fn a_user_note_is_included_only_when_given() {
        let with = build_llm_payload(&incident(), &json!({}), "stutters", plain);
        let without = build_llm_payload(&incident(), &json!({}), "", plain);
        assert!(with.contains("stutters"));
        assert!(!without.contains("user_note"));
    }

    #[test]
    fn the_payload_is_a_fenced_json_block_after_the_prompt() {
        let text = build_llm_payload(&incident(), &json!({}), "", plain);
        assert!(text.starts_with(SYSTEM_PROMPT));
        assert!(text.contains("json"));
        assert!(text.trim_end().ends_with("```"));
    }
}
