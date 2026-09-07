//! Desktop notifications.
//!
//! The bus call stays with the caller; what is here is what goes into it. Two
//! of those fields decide whether a person is interrupted or merely informed,
//! and one decides whether they get one bubble or twenty.

/// The application name a notification is attributed to.
pub const APP: &str = "Goblin Mode Pro";

/// The icon, which is also the `desktop-entry` hint - that is what lets a
/// desktop group these under the application rather than showing them
/// unattributed.
pub const ICON: &str = "com.goblinmode.Pro";

/// How long a bubble stays up, in milliseconds.
pub const TIMEOUT_MS: i32 = 6000;

/// Everything one `Notify` call carries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Notification {
    pub app: String,
    /// The id to overwrite, or 0 for a new bubble.
    pub replaces_id: u32,
    pub icon: String,
    pub title: String,
    pub body: String,
    /// Always empty. These are informational; nothing here has a button.
    pub actions: Vec<String>,
    /// 0 low, 1 normal, 2 critical.
    pub urgency: u8,
    pub desktop_entry: String,
    pub timeout_ms: i32,
}

/// What to send, given what has been sent under this tag before.
///
/// The tag is what keeps a stream of notifications to one bubble: a second
/// message with the same tag replaces the first, and a different tag never
/// overwrites another. Status, incidents and session summaries each have their
/// own, so a thermal warning does not eat the benchmark result that was on
/// screen.
///
/// A tag nothing has been sent under yet, and `replace` turned off, both mean
/// a NEW bubble - which is spelled 0, the id no notification has.
///
/// Urgency is clamped rather than trusted. Above 2 a desktop may refuse the
/// call outright, and the value reaching this point has come through a D-Bus
/// method where anything can arrive.
pub fn notification(
    title: &str,
    body: &str,
    replace: bool,
    urgency: i64,
    tag: &str,
    sent: &std::collections::HashMap<String, u32>,
) -> Notification {
    Notification {
        app: APP.to_string(),
        replaces_id: if replace {
            sent.get(tag).copied().unwrap_or(0)
        } else {
            0
        },
        icon: ICON.to_string(),
        title: title.to_string(),
        body: body.to_string(),
        actions: Vec::new(),
        urgency: urgency.clamp(0, 2) as u8,
        desktop_entry: ICON.to_string(),
        timeout_ms: TIMEOUT_MS,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn nothing_sent() -> HashMap<String, u32> {
        HashMap::new()
    }

    #[test]
    fn a_first_notification_asks_for_a_new_bubble() {
        let n = notification("Hot", "97C", true, 1, "status", &nothing_sent());
        assert_eq!(n.replaces_id, 0);
        assert_eq!(n.app, APP);
        assert_eq!(n.icon, ICON);
        assert_eq!(n.desktop_entry, ICON);
        assert_eq!(n.timeout_ms, 6000);
        assert!(n.actions.is_empty());
    }

    #[test]
    fn a_second_notification_under_the_same_tag_replaces_the_first() {
        let mut sent = nothing_sent();
        sent.insert("status".to_string(), 42);
        let n = notification("Hot", "", true, 1, "status", &sent);
        assert_eq!(n.replaces_id, 42);
    }

    #[test]
    fn a_different_tag_never_overwrites_another() {
        // A thermal warning must not eat the benchmark result on screen.
        let mut sent = nothing_sent();
        sent.insert("status".to_string(), 42);
        let n = notification("Benchmark", "", true, 1, "session", &sent);
        assert_eq!(n.replaces_id, 0);
    }

    #[test]
    fn asking_not_to_replace_asks_for_a_new_bubble() {
        let mut sent = nothing_sent();
        sent.insert("status".to_string(), 42);
        let n = notification("Hot", "", false, 1, "status", &sent);
        assert_eq!(n.replaces_id, 0);
    }

    #[test]
    fn urgency_is_clamped_rather_than_trusted() {
        // It arrives through a D-Bus method, where anything can. Above 2 a
        // desktop may refuse the call rather than clamp it for us.
        let u = |value| notification("t", "", true, value, "status", &nothing_sent()).urgency;
        assert_eq!(u(0), 0);
        assert_eq!(u(1), 1);
        assert_eq!(u(2), 2);
        assert_eq!(u(3), 2);
        assert_eq!(u(9999), 2);
        assert_eq!(u(-1), 0);
        assert_eq!(u(i64::MIN), 0);
    }

    #[test]
    fn the_body_may_be_empty_and_the_title_may_not_be_touched() {
        let n = notification(
            "Performance mode on \u{2014} WoW",
            "",
            true,
            0,
            "status",
            &nothing_sent(),
        );
        assert_eq!(n.title, "Performance mode on \u{2014} WoW");
        assert_eq!(n.body, "");
    }
}
