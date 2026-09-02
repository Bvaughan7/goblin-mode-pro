//! The D-Bus errors this helper returns.
//!
//! These names are part of the contract even though the frozen XML says
//! nothing about them - `docs/dbus-interface-v1.xml` describes methods and
//! signatures, not failures. `tests/conformance/helper.py` matches on
//! `com.goblinmode.ProHelper.Manager.NotAuthorized` by name to tell "the
//! helper refused me" apart from "the helper broke", so a Rust helper that
//! returned the standard `org.freedesktop.DBus.Error.AccessDenied` would be
//! graded wrong by its own conformance suite while looking correct.
//!
//! The prefix reproduces `f"{IFACE}.NotAuthorized"` in `_handle_call`.

/// Errors with the same names the Python helper returns.
#[derive(Debug, zbus::DBusError)]
#[zbus(prefix = "com.goblinmode.ProHelper.Manager")]
pub enum HelperError {
    /// Transport-level failures zbus raises on its own.
    #[zbus(error)]
    ZBus(zbus::Error),

    /// polkit said no, or the caller could not be identified.
    NotAuthorized(String),

    /// The operation was attempted and did not work. Mirrors the catch-all in
    /// the Python helper's `_handle_call`.
    Failed(String),

    /// On the contract, not yet ported. TEMPORARY: this variant must have no
    /// remaining users before the Rust helper becomes the default, because a
    /// caller cannot tell "this build cannot do it" from "your machine cannot
    /// do it" - and the whole promise of the frozen interface is that it does
    /// not matter which implementation answered.
    NotImplemented(String),
}

pub type Result<T> = std::result::Result<T, HelperError>;

#[cfg(test)]
mod tests {
    use super::*;
    use zbus::DBusError as _;

    /// Pinned against the literal strings the Python helper builds from
    /// `IFACE`, because this is a contract the frozen XML does NOT cover and
    /// nothing else would catch a change to it.
    ///
    /// `tests/conformance/helper.py` compares against
    /// `com.goblinmode.ProHelper.Manager.NotAuthorized` to distinguish "the
    /// helper refused me" from "the helper broke". Return the standard
    /// `org.freedesktop.DBus.Error.AccessDenied` instead - which is what zbus
    /// gives you if you reach for `zbus::fdo::Error` - and the suite grades a
    /// correct refusal as a failure.
    #[test]
    fn error_names_match_the_python_helper() {
        assert_eq!(
            HelperError::NotAuthorized(String::new()).name().as_str(),
            "com.goblinmode.ProHelper.Manager.NotAuthorized",
        );
        assert_eq!(
            HelperError::Failed(String::new()).name().as_str(),
            "com.goblinmode.ProHelper.Manager.Failed",
        );
    }

    #[test]
    fn the_unported_error_is_distinguishable_from_a_real_failure() {
        // Deliberately NOT Failed: during the port a caller has to be able to
        // tell "this build cannot do it" from "your machine cannot do it".
        let unported = HelperError::NotImplemented(String::new());
        let failed = HelperError::Failed(String::new());
        assert_eq!(
            unported.name().as_str(),
            "com.goblinmode.ProHelper.Manager.NotImplemented"
        );
        assert_ne!(unported.name().as_str(), failed.name().as_str());
    }

    #[test]
    fn the_message_survives_onto_the_bus() {
        // The conformance suite prints these, and a refusal with no reason is
        // one somebody has to reproduce under a debugger to understand.
        assert_eq!(
            HelperError::NotAuthorized("polkit said no".into()).description(),
            Some("polkit said no"),
        );
    }
}
