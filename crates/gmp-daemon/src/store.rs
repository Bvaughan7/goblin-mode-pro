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

/// How much of a game log is analysed. Bytes, and the tail.
pub const LOG_TAIL_BYTES: u64 = 200_000;

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

    /// The newest game log, decoded, or `None` when there is not one.
    ///
    /// Only the tail is read - the last [`LOG_TAIL_BYTES`] BYTES, not
    /// characters. The Python seeks to `tell() - 200_000` on a text handle
    /// opened with `errors="replace"`, so a multi-byte character straddling
    /// the cut becomes a replacement character on both sides rather than an
    /// error. A Proton log can be hundreds of megabytes after a long session,
    /// and the useful failures are all at the end.
    pub fn newest_log(&self) -> Result<Option<String>> {
        let Some(path) = self.newest_log_path()? else {
            return Ok(None);
        };
        // A log that cannot be read is not an error here: the Python catches
        // OSError around exactly this and answers with no findings, because
        // "the log is unreadable" is not something a findings list can say.
        let Ok(mut file) = std::fs::File::open(&path) else {
            return Ok(None);
        };
        let length = file.metadata().map(|m| m.len()).unwrap_or(0);
        let start = length.saturating_sub(LOG_TAIL_BYTES);
        use std::io::{Read, Seek};
        if file.seek(std::io::SeekFrom::Start(start)).is_err() {
            return Ok(None);
        }
        let mut bytes = Vec::new();
        if file.read_to_end(&mut bytes).is_err() {
            return Ok(None);
        }
        Ok(Some(String::from_utf8_lossy(&bytes).into_owned()))
    }

    /// The most recently modified `*.log` in the game log directory.
    ///
    /// Newest by MODIFICATION TIME, not by name: the wrapper names logs after
    /// the game, so the alphabetical newest is somebody else's game.
    fn newest_log_path(&self) -> Result<Option<std::path::PathBuf>> {
        let entries = match std::fs::read_dir(&self.paths.game_log_dir) {
            Ok(entries) => entries,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(err) => {
                return Err(DaemonError::Failed(format!(
                    "could not list {}: {err}",
                    self.paths.game_log_dir
                )))
            }
        };
        let mut newest: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("log") {
                continue;
            }
            let Ok(modified) = entry.metadata().and_then(|m| m.modified()) else {
                continue;
            };
            if newest.as_ref().is_none_or(|(seen, _)| modified > *seen) {
                newest = Some((modified, path));
            }
        }
        Ok(newest.map(|(_, path)| path))
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

    fn log_store(dir: &std::path::Path) -> Store {
        let mut paths = paths::resolve(&paths::Env {
            home: dir.to_string_lossy().into_owned(),
            ..Default::default()
        });
        std::fs::create_dir_all(dir.join("logs")).unwrap();
        paths.game_log_dir = dir.join("logs").to_string_lossy().into_owned();
        Store::at(paths)
    }

    fn write_log(store: &Store, name: &str, body: &[u8], age_secs: u64) {
        let path = std::path::Path::new(store.paths().game_log_dir.as_str()).join(name);
        std::fs::write(&path, body).unwrap();
        // Modification times decide which log is newest, so they are set
        // rather than left to the order the files happened to be created in.
        let when = std::time::SystemTime::now() - std::time::Duration::from_secs(age_secs);
        filetime_set(&path, when);
    }

    fn filetime_set(path: &std::path::Path, when: std::time::SystemTime) {
        let file = std::fs::OpenOptions::new().write(true).open(path).unwrap();
        file.set_modified(when).unwrap();
    }

    #[test]
    fn no_log_directory_is_no_log_rather_than_an_error() {
        let dir = tempdir();
        let store = store_at(&dir); // its game_log_dir does not exist
        assert!(store.newest_log().unwrap().is_none());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_newest_log_is_the_one_most_recently_written() {
        // By modification time, NOT by name: the wrapper names logs after the
        // game, so the alphabetically last one is somebody else's game.
        let dir = tempdir();
        let store = log_store(&dir);
        write_log(&store, "zzz-old.log", b"old\n", 600);
        write_log(&store, "aaa-new.log", b"new\n", 1);
        assert_eq!(store.newest_log().unwrap().as_deref(), Some("new\n"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn files_that_are_not_logs_are_ignored() {
        let dir = tempdir();
        let store = log_store(&dir);
        write_log(&store, "game.log", b"kept\n", 600);
        write_log(&store, "notes.txt", b"ignored\n", 1);
        write_log(&store, "noextension", b"ignored\n", 1);
        assert_eq!(store.newest_log().unwrap().as_deref(), Some("kept\n"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn only_the_tail_is_read_and_the_cut_is_in_bytes() {
        let dir = tempdir();
        let store = log_store(&dir);
        let mut body = vec![b'x'; LOG_TAIL_BYTES as usize];
        body.extend_from_slice(b"THE-END");
        write_log(&store, "big.log", &body, 1);
        let text = store.newest_log().unwrap().unwrap();
        assert_eq!(text.len(), LOG_TAIL_BYTES as usize);
        assert!(text.ends_with("THE-END"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_character_split_by_the_cut_becomes_a_replacement_not_an_error() {
        // The Python opens the log with errors="replace" for exactly this: a
        // multi-byte character straddling the 200_000-byte boundary must not
        // turn a whole analysis into nothing.
        let dir = tempdir();
        let store = log_store(&dir);
        // Positioned so the tail begins ONE BYTE INTO the first character:
        // the suffix is eight bytes shorter than the window, and the three
        // characters plus a ten-byte prefix make up the difference.
        let mut body = vec![b'a'; 10];
        body.extend_from_slice("ゲーム".as_bytes()); // bytes 10..19, 'ゲ' at 10..13
        body.extend(std::iter::repeat_n(b'x', LOG_TAIL_BYTES as usize - 8));
        write_log(&store, "utf8.log", &body, 1);

        let text = store.newest_log().unwrap().unwrap();
        // Cross-checked against CPython on the identical file: two
        // replacement characters, then "ーム", then the run of x - 199_996
        // characters from a 200_000-byte window, because the two replaced
        // bytes are one character each and the surviving pair are three bytes
        // each.
        assert_eq!(text.chars().count(), 199_996);
        assert!(
            text.starts_with("\u{fffd}\u{fffd}ーム"),
            "{:?}",
            &text[..12]
        );
        assert!(
            text.starts_with('\u{fffd}'),
            "the split character was replaced"
        );
        assert!(text.contains("ーム"), "the characters after it survived");
        assert!(!text.contains('a'), "the prefix was outside the window");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_short_log_is_read_whole() {
        let dir = tempdir();
        let store = log_store(&dir);
        write_log(&store, "small.log", b"all of it\n", 1);
        assert_eq!(store.newest_log().unwrap().as_deref(), Some("all of it\n"));
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
