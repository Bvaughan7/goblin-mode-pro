//! The filesystem half of reading what the Python wrote.
//!
//! [`gmp_core::store`] does the parsing and takes text, which is what keeps it
//! diffable against the Python from fixtures. This opens the files, and that
//! is all it does - the split is deliberate, so the interesting decisions stay
//! where they can be graded.
//!
//! One rule worth stating: a file that is not there is an empty history, and
//! any other read failure is an error the caller sees. The Python does the
//! same - its readers check `exists()` and let an `OSError` escape into the
//! bridge, which turns it into `com.goblinmode.Pro.Daemon.Failed`. A missing
//! history is the ordinary state of a fresh install; an unreadable one is
//! worth telling somebody about.

use gmp_core::config::Settings;
use gmp_core::paths::{self, Paths};
use gmp_core::store as parse;
use serde_json::Value;

use crate::error::{DaemonError, Result};

/// How much history each reply carries. Both are what the Python daemon's
/// API layer passes, not the readers' own defaults.
pub const SESSION_LIMIT: i64 = 60;
pub const INCIDENT_LIMIT: i64 = 100;

pub struct Store {
    paths: Paths,
}

impl Default for Store {
    fn default() -> Self {
        Self::from_env()
    }
}

impl Store {
    pub fn from_env() -> Self {
        Self {
            paths: paths::resolve(&paths::Env::from_process()),
        }
    }

    /// Only for tests, which need a tree that is not this machine's.
    pub fn at(paths: Paths) -> Self {
        Self { paths }
    }

    pub fn paths(&self) -> &Paths {
        &self.paths
    }

    /// The file's contents, or an empty string when it is not there.
    fn read(&self, path: &str) -> Result<String> {
        match std::fs::read_to_string(path) {
            Ok(text) => Ok(text),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
            Err(err) => Err(DaemonError::Failed(format!("could not read {path}: {err}"))),
        }
    }

    /// The session history, newest last, optionally for one executable.
    ///
    /// An EMPTY `exe` means every game, not a game whose name is empty - the
    /// Python passes `exe or None`, and the GUI sends an empty string for
    /// "all". A profile cannot have an empty exe anyway, so nothing is lost.
    pub fn sessions(&self, exe: &str) -> Result<Vec<Value>> {
        let text = self.read(&self.paths.session_file)?;
        let filter = if exe.is_empty() { None } else { Some(exe) };
        Ok(parse::sessions_history(&text, filter, SESSION_LIMIT))
    }

    /// The incident history from disk. The daemon's in-memory log takes
    /// precedence when it has anything, which is the caller's decision.
    pub fn incidents(&self) -> Result<Vec<Value>> {
        let text = self.read(&self.paths.incident_file)?;
        Ok(parse::incidents_history(&text, INCIDENT_LIMIT))
    }

    /// The settings, or the defaults when there is no usable file.
    pub fn settings(&self) -> Result<Settings> {
        Ok(parse::settings_from_text(
            &self.read(&self.paths.config_file)?,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store_at(dir: &std::path::Path) -> Store {
        let mut paths = paths::resolve(&paths::Env {
            home: dir.to_string_lossy().into_owned(),
            ..Default::default()
        });
        paths.session_file = dir.join("sessions.jsonl").to_string_lossy().into_owned();
        paths.incident_file = dir.join("incidents.jsonl").to_string_lossy().into_owned();
        paths.config_file = dir.join("config.json").to_string_lossy().into_owned();
        Store::at(paths)
    }

    fn tempdir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "gmp-store-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn the_reply_sizes_are_the_ones_the_python_api_passes() {
        // These are the API layer's numbers, not the readers' own defaults -
        // `daemon_api.py` passes 60 and 100 explicitly. Held in two languages
        // with nothing generating them, so they are asserted on both sides;
        // the conformance suite is what would catch them drifting apart on a
        // live daemon.
        assert_eq!(SESSION_LIMIT, 60);
        assert_eq!(INCIDENT_LIMIT, 100);
    }

    #[test]
    fn the_session_reply_is_capped() {
        let dir = tempdir();
        let store = store_at(&dir);
        let body: String = (0..200)
            .map(|i| format!("{{\"exe\":\"a\",\"i\":{i}}}\n"))
            .collect();
        std::fs::write(&store.paths().session_file, body).unwrap();
        let rows = store.sessions("a").unwrap();
        assert_eq!(rows.len(), SESSION_LIMIT as usize);
        // The tail, not the head: the newest sessions are the interesting ones.
        assert_eq!(rows[rows.len() - 1]["i"], serde_json::json!(199));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_fresh_install_has_an_empty_history_rather_than_an_error() {
        let dir = tempdir();
        let store = store_at(&dir);
        assert!(store.sessions("").unwrap().is_empty());
        assert!(store.incidents().unwrap().is_empty());
        // And the settings are the defaults, not an error.
        assert!(!store.settings().unwrap().profiles.is_empty());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn an_empty_exe_means_every_game() {
        // The GUI sends "" for "all", and the Python turns that into None.
        let dir = tempdir();
        let store = store_at(&dir);
        std::fs::write(
            &store.paths().session_file,
            "{\"exe\":\"a\"}\n{\"exe\":\"b\"}\n",
        )
        .unwrap();
        assert_eq!(store.sessions("").unwrap().len(), 2);
        assert_eq!(store.sessions("a").unwrap().len(), 1);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_damaged_line_does_not_take_the_history_with_it() {
        let dir = tempdir();
        let store = store_at(&dir);
        std::fs::write(
            &store.paths().session_file,
            "{\"exe\":\"a\"}\n5\nnot json\n{\"exe\":\"a\"}\n",
        )
        .unwrap();
        assert_eq!(store.sessions("a").unwrap().len(), 2);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_directory_where_a_file_belongs_is_an_error_not_an_empty_history() {
        // Distinguishable from "not there", which is the whole point: a fresh
        // install is normal and an unreadable history is worth reporting.
        let dir = tempdir();
        let store = store_at(&dir);
        std::fs::create_dir_all(&store.paths().session_file).unwrap();
        assert!(store.sessions("").is_err());
        std::fs::remove_dir_all(&dir).ok();
    }
}
