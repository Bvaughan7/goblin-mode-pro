//! What the daemon owns and mutates, and nothing else.
//!
//! `daemon_api.py` already draws this line: the read-and-report methods own no
//! state and their answers have moved to `gmp_core::status`, while the methods
//! that read or write state the poll loop maintains stay with the state. This
//! module is the second half of that split, kept separate from the start
//! because the Python daemon accumulated into one 880-line file and the
//! conversion plan asks explicitly that this one not repeat it.
//!
//! Everything here is in memory and rebuildable. What must survive a restart
//! lives on disk in formats `gmp_core` already reads - the settings, the
//! session history, the incident log, and `applied.json`, whose reader is
//! `gmp_core::applied`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

/// The daemon's mutable state.
///
/// Deliberately plain data with no behaviour: the loop mutates it, the API
/// reads it, and the judgement that turns it into an answer lives in
/// `gmp_core` where it can be diffed against the Python.
#[derive(Debug, Default)]
pub struct DaemonState {
    /// The cached readiness answer and when it was taken, or `None` before the
    /// first pre-flight run. Re-run at most every ten minutes.
    pub health: Option<Health>,

    /// A boost the user asked for by hand, which outlives any game.
    pub forced_boost: bool,

    /// exe -> pid for every game currently detected.
    pub active_pids: BTreeMap<String, i64>,

    /// Profiles changed since the last save. The write is debounced, so this
    /// is what a pending flush would cover.
    pub dirty_profiles: BTreeSet<String>,

    /// Incidents this run has raised, newest last. They are also appended to
    /// the log on disk; this is what makes the current run's answer immediate
    /// rather than a re-read, and it is why an empty one falls back to disk.
    pub incidents: Vec<Value>,
}

/// A readiness answer with the clock reading `gmp_core::status::health`
/// deliberately leaves out.
#[derive(Debug, Clone)]
pub struct Health {
    pub answer: Value,
    pub checked_at: std::time::SystemTime,
}

/// How long a readiness answer is served before the pre-flight is re-run.
pub const HEALTH_TTL: std::time::Duration = std::time::Duration::from_secs(600);

impl Health {
    /// Whether this answer is old enough to be worth re-taking.
    ///
    /// A clock that has gone backwards - suspend, an NTP step - makes the
    /// elapsed time unrepresentable rather than negative, and that counts as
    /// stale: re-running the pre-flight is cheap and serving an answer of
    /// unknown age is not.
    pub fn is_stale(&self, now: std::time::SystemTime) -> bool {
        match now.duration_since(self.checked_at) {
            Ok(age) => age > HEALTH_TTL,
            Err(_) => true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, SystemTime};

    fn health_at(t: SystemTime) -> Health {
        Health {
            answer: serde_json::json!({"score": 10.0}),
            checked_at: t,
        }
    }

    #[test]
    fn a_fresh_answer_is_served_and_an_old_one_is_not() {
        let taken = SystemTime::UNIX_EPOCH + Duration::from_secs(10_000);
        let health = health_at(taken);
        assert!(!health.is_stale(taken));
        assert!(!health.is_stale(taken + HEALTH_TTL));
        assert!(health.is_stale(taken + HEALTH_TTL + Duration::from_secs(1)));
    }

    #[test]
    fn a_clock_that_went_backwards_counts_as_stale() {
        // Suspend and NTP steps both do this, and an answer of unknown age is
        // worth less than the second of work it takes to retake it.
        let taken = SystemTime::UNIX_EPOCH + Duration::from_secs(10_000);
        assert!(health_at(taken).is_stale(SystemTime::UNIX_EPOCH));
    }
}
