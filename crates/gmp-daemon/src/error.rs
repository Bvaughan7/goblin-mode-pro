//! The D-Bus errors this daemon returns.
//!
//! As on the helper's interface, these names are contract even though the
//! frozen XML says nothing about them - it describes methods and signatures,
//! not failures. A caller has to be able to tell "the daemon refused" from
//! "the daemon broke", and the standard `org.freedesktop.DBus.Error.*` names
//! cannot say which.
//!
//! `NotImplemented` exists for this block and should disappear with it. It is
//! deliberately a hard error rather than an empty JSON reply: a half-ported
//! daemon that answered `{}` would look to the GUI exactly like a machine with
//! nothing to report, and the whole point of stubbing first is that the gap is
//! visible from outside.

/// Errors this daemon returns on the session bus.
#[derive(Debug, zbus::DBusError)]
#[zbus(prefix = "com.goblinmode.Pro.Daemon")]
pub enum DaemonError {
    /// Transport-level failures zbus raises on its own.
    #[zbus(error)]
    ZBus(zbus::Error),

    /// Served, but not yet ported. Temporary, and named so that a conformance
    /// run reports it as an unfinished method rather than as a broken one.
    NotImplemented(String),

    /// The call was understood and did not work.
    Failed(String),
}

pub type Result<T> = std::result::Result<T, DaemonError>;

/// The refusal every stub returns, naming itself so a conformance run says
/// which method it was.
pub fn not_implemented(method: &str) -> DaemonError {
    DaemonError::NotImplemented(format!(
        "{method} is served by the Rust daemon but not yet ported - \
         run the Python daemon for this"
    ))
}
