//! The D-Bus interface this daemon serves, as frozen in
//! `docs/dbus-daemon-interface-v1.xml`.
//!
//! Every method is a stub that refuses with `NotImplemented`, and that is the
//! whole deliverable for this block. The helper port did the same thing in the
//! same order and it paid: serving the exact contract first means the freeze
//! check and the conformance suite can grade this binary before it does
//! anything, so the shape is right before the behaviour arrives.
//!
//! A refusal rather than an empty reply is the point. `{}` from `GetStatus`
//! looks to the GUI exactly like a machine with nothing running, and a
//! half-ported daemon that quietly answered nothing would be worse than no
//! Rust daemon at all.
//!
//! Two things here are contract and easy to get wrong:
//!
//! * **zbus derives PascalCase from the Rust method name.** `get_status`
//!   serves `GetStatus` correctly, but the acronyms do not survive the round
//!   trip - `set_nvidia_modeset` would serve `SetNvidiaModeset`, and the
//!   frozen file says `SetNvidiaModeset`, so that one happens to agree. Every
//!   name is pinned by a test against the frozen file rather than trusted.
//! * **The properties are read-only identity.** Nothing may branch on
//!   `Implementation`; the moment behaviour depends on it the two daemons stop
//!   being interchangeable and the freeze is a fiction.

use std::sync::Arc;

use tokio::sync::Mutex;
use zbus::interface;

use crate::error::{not_implemented, DaemonError, Result};
use crate::state::DaemonState;
use crate::store::Store;

/// The frozen contract's identity. These three strings are the whole
/// compatibility surface between the Python and Rust daemons.
pub const BUS_NAME: &str = "com.goblinmode.Pro.Daemon";
pub const OBJECT_PATH: &str = "/com/goblinmode/Pro/Daemon";
pub const INTERFACE: &str = "com.goblinmode.Pro.Daemon";

/// Stays 1 for the whole conversion - see the frozen file.
const INTERFACE_VERSION: u32 = 1;

/// Which implementation is answering. For bug reports ONLY.
const IMPLEMENTATION: &str = "rust";

pub struct Api {
    #[allow(dead_code)] // the loop that mutates it arrives with the next block
    state: Arc<Mutex<DaemonState>>,
    store: Store,
}

impl Api {
    pub fn new(state: Arc<Mutex<DaemonState>>) -> Self {
        Self {
            state,
            store: Store::from_env(),
        }
    }

    /// For tests, which need a tree that is not this machine's.
    pub fn with_store(state: Arc<Mutex<DaemonState>>, store: Store) -> Self {
        Self { state, store }
    }
}

/// A reply body, or the error the bridge would have turned an exception into.
fn as_json(rows: &[serde_json::Value]) -> Result<String> {
    serde_json::to_string(rows).map_err(|err| DaemonError::Failed(err.to_string()))
}

// The stub bodies ignore their arguments, but the NAMES are contract - they
// are the in-argument names in the frozen file, and zbus takes them straight
// from the Rust parameter list.
#[allow(unused_variables)]
#[interface(name = "com.goblinmode.Pro.Daemon")]
impl Api {
    // -- live readings -----------------------------------------------------
    #[zbus(out_args("json"))]
    async fn get_status(&self) -> Result<String> {
        Err(not_implemented("GetStatus"))
    }

    #[zbus(out_args("json"))]
    async fn get_metrics(&self) -> Result<String> {
        Err(not_implemented("GetMetrics"))
    }

    /// The live incidents this run has raised, or the history from disk when
    /// it has raised none. A daemon that has just started always answers from
    /// disk, which is why this one can be served before the loop exists.
    #[zbus(out_args("json"))]
    async fn get_incidents(&self) -> Result<String> {
        let live = self.state.lock().await.incidents.clone();
        if !live.is_empty() {
            return as_json(&live);
        }
        as_json(&self.store.incidents()?)
    }

    #[zbus(out_args("json"))]
    async fn get_sessions(&self) -> Result<String> {
        as_json(&self.store.sessions("")?)
    }

    /// An empty `exe` means every game, which is what the GUI sends for "all".
    #[zbus(out_args("json"))]
    async fn get_session_history(&self, exe: &str) -> Result<String> {
        as_json(&self.store.sessions(exe)?)
    }

    #[zbus(out_args("json"))]
    async fn get_system_info(&self) -> Result<String> {
        Err(not_implemented("GetSystemInfo"))
    }

    #[zbus(out_args("json"))]
    async fn get_health(&self) -> Result<String> {
        Err(not_implemented("GetHealth"))
    }

    // -- pre-flight ---------------------------------------------------------
    #[zbus(out_args("json"))]
    async fn run_preflight(&self) -> Result<String> {
        Err(not_implemented("RunPreflight"))
    }

    #[zbus(out_args("json"))]
    async fn apply_preflight_fixes(&self) -> Result<String> {
        Err(not_implemented("ApplyPreflightFixes"))
    }

    #[zbus(out_args("json"))]
    async fn revert_preflight_fix(&self, key: &str) -> Result<String> {
        Err(not_implemented("RevertPreflightFix"))
    }

    // -- profiles and the master switch --------------------------------------
    #[zbus(out_args("ok"))]
    async fn set_profile(&self, json: &str) -> Result<bool> {
        Err(not_implemented("SetProfile"))
    }

    #[zbus(out_args("ok"))]
    async fn remove_profile(&self, exe: &str) -> Result<bool> {
        Err(not_implemented("RemoveProfile"))
    }

    #[zbus(out_args("ok"))]
    async fn set_master_enabled(&self, enabled: bool) -> Result<bool> {
        Err(not_implemented("SetMasterEnabled"))
    }

    #[zbus(out_args("ok"))]
    async fn set_auto_detect(&self, enabled: bool) -> Result<bool> {
        Err(not_implemented("SetAutoDetect"))
    }

    #[zbus(out_args("ok"))]
    async fn force_boost(&self, on: bool) -> Result<bool> {
        Err(not_implemented("ForceBoost"))
    }

    // -- the detected-game verbs ----------------------------------------------
    //
    // `IgnoreGame` and `UnignoreGame` are inverses; `KeepGame` is NOT the
    // inverse of either - it clears `auto_created` on a profile. The frozen
    // interface gained `UnignoreGame` because the conformance suite found
    // that ignoring a game could not be undone anywhere in the project.
    #[zbus(out_args("ok"))]
    async fn ignore_game(&self, exe: &str) -> Result<bool> {
        Err(not_implemented("IgnoreGame"))
    }

    #[zbus(out_args("ok"))]
    async fn unignore_game(&self, exe: &str) -> Result<bool> {
        Err(not_implemented("UnignoreGame"))
    }

    #[zbus(out_args("ok"))]
    async fn keep_game(&self, exe: &str) -> Result<bool> {
        Err(not_implemented("KeepGame"))
    }

    #[zbus(out_args("ok"))]
    async fn arm_benchmark(&self, exe: &str) -> Result<bool> {
        Err(not_implemented("ArmBenchmark"))
    }

    // -- reports and exports ----------------------------------------------------
    #[zbus(out_args("markdown"))]
    async fn build_report(&self, note: &str) -> Result<String> {
        Err(not_implemented("BuildReport"))
    }

    #[zbus(out_args("json"))]
    async fn build_works_for_me(&self, exe: &str, note: &str) -> Result<String> {
        Err(not_implemented("BuildWorksForMe"))
    }

    #[zbus(out_args("markdown"))]
    async fn export_setup(&self) -> Result<String> {
        Err(not_implemented("ExportSetup"))
    }

    #[zbus(out_args("payload"))]
    async fn export_last_incident(&self) -> Result<String> {
        Err(not_implemented("ExportLastIncident"))
    }

    /// What the newest Wine/Proton log says went wrong.
    ///
    /// The Steam app id sharpens a couple of rules, and it comes from the
    /// profile of a game that is currently running - so a daemon with nothing
    /// running passes an empty one, which is what the Python does too when no
    /// matched game has an app id.
    #[zbus(out_args("json"))]
    async fn analyze_log(&self) -> Result<String> {
        let Some(text) = self.store.newest_log()? else {
            return Ok("[]".to_string());
        };
        let app_id = self.state.lock().await.steam_app_id.clone();
        let findings = gmp_core::logrules::analyze_text(&text, &app_id);
        serde_json::to_string(&findings).map_err(|err| DaemonError::Failed(err.to_string()))
    }

    #[zbus(out_args("path"))]
    async fn write_wrapper(&self) -> Result<String> {
        Err(not_implemented("WriteWrapper"))
    }

    // -- Proton and the NVIDIA module ----------------------------------------------
    #[zbus(out_args("json"))]
    async fn get_proton_info(&self) -> Result<String> {
        Err(not_implemented("GetProtonInfo"))
    }

    #[zbus(out_args("json"))]
    async fn clear_shader_cache(&self, path: &str) -> Result<String> {
        Err(not_implemented("ClearShaderCache"))
    }

    #[zbus(out_args("json"))]
    async fn get_nvidia_module_state(&self) -> Result<String> {
        Err(not_implemented("GetNvidiaModuleState"))
    }

    #[zbus(out_args("ok"))]
    async fn set_nvidia_modeset(&self, enabled: bool) -> Result<bool> {
        Err(not_implemented("SetNvidiaModeset"))
    }

    // -- signals ---------------------------------------------------------------------
    //
    // Declared here so the served interface matches the frozen file today.
    // Nothing emits them until the poll loop exists.
    #[zbus(signal)]
    pub async fn status_changed(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        json: &str,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    pub async fn metrics_updated(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        json: &str,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    pub async fn incident_logged(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        json: &str,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    pub async fn game_detected(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        json: &str,
    ) -> zbus::Result<()>;

    #[zbus(signal)]
    pub async fn session_logged(
        emitter: &zbus::object_server::SignalEmitter<'_>,
        json: &str,
    ) -> zbus::Result<()>;

    // -- read-only identity ------------------------------------------------------------
    #[zbus(property)]
    async fn version(&self) -> String {
        env!("CARGO_PKG_VERSION").to_string()
    }

    #[zbus(property)]
    async fn interface_version(&self) -> u32 {
        INTERFACE_VERSION
    }

    #[zbus(property)]
    async fn implementation(&self) -> String {
        IMPLEMENTATION.to_string()
    }
}

/// This binary's own introspection XML, produced without touching a bus.
///
/// `gmp-daemon --introspect` prints this so the EXISTING Python freeze test
/// grades the Rust daemon with the same canonicalizer it already uses on the
/// Python one. Reimplementing `tests/_dbusxml.py` in Rust would create a
/// second canonicalizer, and two canonicalizers that drift apart is how a
/// freeze check starts passing for the wrong reason.
pub fn introspection_xml() -> String {
    use zbus::object_server::Interface;
    let mut out = String::from("<node>\n");
    Api::new(Arc::new(Mutex::new(DaemonState::default()))).introspect_to_writer(&mut out, 2);
    out.push_str("</node>\n");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The frozen contract, read at compile time. The byte-for-byte canonical
    /// comparison lives in tests/test_daemon_interface_freeze.py, which owns
    /// the canonicalizer; this checks the SETS so a rename fails here first.
    const FROZEN: &str = include_str!("../../../docs/dbus-daemon-interface-v1.xml");

    fn names(xml: &str, tag: &str) -> Vec<String> {
        let open = format!("<{tag} name=\"");
        let mut found: Vec<String> = xml
            .split(&open)
            .skip(1)
            .filter_map(|rest| rest.split('"').next().map(str::to_string))
            .collect();
        found.sort();
        found
    }

    #[test]
    fn every_frozen_method_is_served_and_no_others() {
        assert_eq!(
            names(&introspection_xml(), "method"),
            names(FROZEN, "method")
        );
    }

    #[test]
    fn every_frozen_signal_is_declared_and_no_others() {
        assert_eq!(
            names(&introspection_xml(), "signal"),
            names(FROZEN, "signal")
        );
    }

    #[test]
    fn every_frozen_property_is_served_and_no_others() {
        // zbus derives these names too: `interface_version` has to come out
        // as `InterfaceVersion` and nothing checks that but this.
        assert_eq!(
            names(&introspection_xml(), "property"),
            names(FROZEN, "property")
        );
    }

    #[test]
    fn the_surface_is_the_size_the_freeze_says() {
        let xml = introspection_xml();
        assert_eq!(names(&xml, "method").len(), 29);
        assert_eq!(names(&xml, "signal").len(), 5);
        assert_eq!(names(&xml, "property").len(), 3);
    }

    #[test]
    fn the_interface_is_named_on_the_served_xml() {
        assert!(introspection_xml().contains(INTERFACE));
    }

    #[test]
    fn the_interface_version_never_moves() {
        // A v2 is a new file and a new bus name, not a bumped integer here.
        assert_eq!(INTERFACE_VERSION, 1);
    }

    fn api_over(dir: &std::path::Path) -> Api {
        use gmp_core::paths;
        let mut resolved = paths::resolve(&paths::Env {
            home: dir.to_string_lossy().into_owned(),
            ..Default::default()
        });
        resolved.session_file = dir.join("sessions.jsonl").to_string_lossy().into_owned();
        resolved.incident_file = dir.join("incidents.jsonl").to_string_lossy().into_owned();
        resolved.game_log_dir = dir.join("logs").to_string_lossy().into_owned();
        Api::with_store(
            Arc::new(Mutex::new(DaemonState::default())),
            Store::at(resolved),
        )
    }

    fn tempdir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "gmp-api-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[tokio::test]
    async fn the_disk_backed_methods_answer_rather_than_refusing() {
        let dir = tempdir();
        let api = api_over(&dir);
        std::fs::write(
            &api.store.paths().session_file,
            "{\"exe\":\"a\"}\n{\"exe\":\"b\"}\n",
        )
        .unwrap();
        std::fs::write(&api.store.paths().incident_file, "{\"kind\":\"thermal\"}\n").unwrap();

        assert_eq!(
            api.get_sessions().await.unwrap(),
            r#"[{"exe":"a"},{"exe":"b"}]"#
        );
        assert_eq!(
            api.get_session_history("a").await.unwrap(),
            r#"[{"exe":"a"}]"#
        );
        // An empty exe is "every game", which is what the GUI sends for all.
        assert_eq!(
            api.get_session_history("").await.unwrap(),
            r#"[{"exe":"a"},{"exe":"b"}]"#
        );
        assert_eq!(
            api.get_incidents().await.unwrap(),
            r#"[{"kind":"thermal"}]"#
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn analyze_log_reads_the_newest_log_and_reports_findings() {
        let dir = tempdir();
        let api = api_over(&dir);
        let logs = std::path::Path::new(api.store.paths().game_log_dir.as_str());
        std::fs::create_dir_all(logs).unwrap();
        std::fs::write(
            logs.join("game.log"),
            // A line the shipped rules really match - `vram_oom`.
            "wine: VK_ERROR_OUT_OF_DEVICE_MEMORY allocating swapchain\n",
        )
        .unwrap();

        let reply = api.analyze_log().await.unwrap();
        let findings: serde_json::Value = serde_json::from_str(&reply).unwrap();
        assert!(!findings.as_array().unwrap().is_empty(), "{reply}");
        // The reply is a list of the dataclass's own fields, in its order.
        let first = &findings[0];
        for key in [
            "rule_id", "label", "category", "cause", "fix", "severity", "count", "sample",
            "fix_cmd",
        ] {
            assert!(first.get(key).is_some(), "{key} missing from {reply}");
        }
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn analyze_log_with_no_log_is_an_empty_list_not_an_error() {
        let dir = tempdir();
        let api = api_over(&dir);
        assert_eq!(api.analyze_log().await.unwrap(), "[]");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn a_fresh_install_answers_with_empty_lists() {
        let dir = tempdir();
        let api = api_over(&dir);
        assert_eq!(api.get_sessions().await.unwrap(), "[]");
        assert_eq!(api.get_incidents().await.unwrap(), "[]");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn this_runs_incidents_win_over_the_ones_on_disk() {
        let dir = tempdir();
        let api = api_over(&dir);
        std::fs::write(&api.store.paths().incident_file, "{\"kind\":\"old\"}\n").unwrap();
        api.state
            .lock()
            .await
            .incidents
            .push(serde_json::json!({"kind": "live"}));
        assert_eq!(api.get_incidents().await.unwrap(), r#"[{"kind":"live"}]"#);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_stub_names_the_method_it_refused() {
        // A conformance run has to be able to say WHICH method is unfinished.
        let error = not_implemented("GetStatus");
        assert!(format!("{error:?}").contains("GetStatus"));
    }
}
