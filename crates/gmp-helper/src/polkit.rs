//! polkit authorization, and the caller-identity lookup it depends on.
//!
//! This is the most security-critical code in the binary, which is why it is
//! written out by hand against the D-Bus API rather than pulled from a crate.
//! There is no polkit crate worth using, and the ~60 lines here are ones you
//! want to be able to read in one sitting before installing the thing.
//!
//! It is a direct port of `_check_authorized` and `_caller_uid` in
//! `helper/goblin_helper.py`. The behaviour is deliberately identical,
//! including the details that look like accidents and are not - see
//! `caller_uid` in particular.

use std::collections::HashMap;

use zbus::zvariant::{OwnedValue, Value};
use zbus::Connection;

/// Tune CPU governor, process priority and power limits. `allow_active=yes`
/// in the policy: free for an active local session, no prompt.
pub const ACTION_PERF: &str = "com.goblinmode.pro.manage-performance";
/// Persistent kernel configuration. `auth_admin_keep` - prompts once.
pub const ACTION_KERNEL: &str = "com.goblinmode.pro.manage-kernel-tunables";
/// Taking a fan off the EC curve. `auth_admin_keep` - prompts once.
pub const ACTION_THERMAL: &str = "com.goblinmode.pro.manage-hardware-thermal";

/// Methods gated behind the stricter "persistent system config" action.
const KERNEL_ACTION_METHODS: &[&str] = &["SetSysctl", "RevertSysctl", "SetNvidiaModeset"];

/// Switching a fan channel out of EC control is a thermal-safety operation
/// that persists after the caller dies, so it gets its own action - a user may
/// reasonably allow fan spin-up but not sysctl writes, or the other way round.
///
/// `ResetFans` is deliberately NOT here: handing control back to the embedded
/// controller must always be possible without a prompt, or a user with no
/// polkit agent running is stuck with whatever duty was last set.
const THERMAL_ACTION_METHODS: &[&str] = &["SpinUpFans"];

/// Every method that changes the machine. Everything else on the interface is
/// read-only and is not authorized at all.
pub const MUTATING: &[&str] = &[
    "SetGovernor",
    "SetEPP",
    "Renice",
    "SetPowerLimits",
    "ResetPowerLimits",
    "SetTDP",
    "ResetTDP",
    "RevertAll",
    "SetSysctl",
    "RevertSysctl",
    "ApplyUndervolt",
    "ApplyAmdUndervolt",
    "SetNvidiaModeset",
    "SpinUpFans",
    "ResetFans",
];

/// Which polkit action a method demands.
///
/// `tests/conformance/helper.py --polkit-routing` asserts this mapping at
/// runtime by eavesdropping the `CheckAuthorization` call, against whichever
/// implementation is on the bus. That is the check that would have caught
/// `SpinUpFans` sitting on the permissive action.
pub fn action_for(method: &str) -> &'static str {
    if KERNEL_ACTION_METHODS.contains(&method) {
        ACTION_KERNEL
    } else if THERMAL_ACTION_METHODS.contains(&method) {
        ACTION_THERMAL
    } else {
        ACTION_PERF
    }
}

pub fn is_mutating(method: &str) -> bool {
    MUTATING.contains(&method)
}

/// The Unix uid behind a D-Bus sender name, or `None` if it cannot be resolved.
///
/// FAIL CLOSED. `None` means "the lookup failed", and every caller must treat
/// it as untrusted - never as uid 0. The Python helper had exactly this bug:
/// an unresolvable uid was allowed to stand in for root, which handed the
/// ownership check in `Renice` to anyone who could make the lookup fail. It is
/// called out here because the obvious translation of an `Option` into a
/// default is what reintroduces it.
pub async fn caller_uid(conn: &Connection, sender: &str) -> Option<u32> {
    let reply = conn
        .call_method(
            Some("org.freedesktop.DBus"),
            "/org/freedesktop/DBus",
            Some("org.freedesktop.DBus"),
            "GetConnectionUnixUser",
            &(sender,),
        )
        .await
        .inspect_err(|err| tracing::warn!("could not resolve caller uid: {err}"))
        .ok()?;
    reply
        .body()
        .deserialize::<u32>()
        .inspect_err(|err| tracing::warn!("malformed GetConnectionUnixUser reply: {err}"))
        .ok()
}

/// Ask polkit whether `sender` may perform `action`.
///
/// Returns `false` on any error. A polkit that cannot be reached, replies with
/// the wrong type, or times out is not permission to proceed.
pub async fn check_authorized(conn: &Connection, sender: &str, action: &str) -> bool {
    match query_authority(conn, sender, action).await {
        Ok(authorized) => authorized,
        Err(err) => {
            tracing::error!("polkit check failed: {err}");
            false
        }
    }
}

async fn query_authority(conn: &Connection, sender: &str, action: &str) -> zbus::Result<bool> {
    // Subject is ("system-bus-name", {"name": <sender>}) - polkit resolves the
    // pid and session from the bus name itself, which is what makes this
    // immune to a caller lying about its own pid.
    let mut subject_details: HashMap<&str, Value<'_>> = HashMap::new();
    subject_details.insert("name", Value::from(sender));
    let subject = ("system-bus-name", subject_details);
    let details: HashMap<&str, &str> = HashMap::new();
    // 1 = AllowUserInteraction. The two auth_admin_keep actions raise a
    // password dialog on the user's desktop because of this flag, and the
    // CALLER cannot suppress it - see tests/conformance/helper.py, which has
    // to gate those checks behind --prompts for that reason.
    const ALLOW_USER_INTERACTION: u32 = 1;

    let reply = conn
        .call_method(
            Some("org.freedesktop.PolicyKit1"),
            "/org/freedesktop/PolicyKit1/Authority",
            Some("org.freedesktop.PolicyKit1.Authority"),
            "CheckAuthorization",
            &(subject, action, details, ALLOW_USER_INTERACTION, ""),
        )
        .await?;

    // (bba{ss}): is_authorized, is_challenge, details
    let (is_authorized, _is_challenge, _details): (bool, bool, HashMap<String, String>) =
        reply.body().deserialize()?;
    Ok(is_authorized)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The routing table is a privilege boundary, so it is pinned method by
    /// method rather than by re-deriving it from the same sets it is built
    /// from - a test that reimplements the code under test proves nothing.
    #[test]
    fn every_mutating_method_routes_to_the_documented_action() {
        let expected: &[(&str, &str)] = &[
            ("SetGovernor", ACTION_PERF),
            ("SetEPP", ACTION_PERF),
            ("Renice", ACTION_PERF),
            ("SetPowerLimits", ACTION_PERF),
            ("ResetPowerLimits", ACTION_PERF),
            ("SetTDP", ACTION_PERF),
            ("ResetTDP", ACTION_PERF),
            ("RevertAll", ACTION_PERF),
            ("ApplyUndervolt", ACTION_PERF),
            ("ApplyAmdUndervolt", ACTION_PERF),
            ("ResetFans", ACTION_PERF),
            ("SetSysctl", ACTION_KERNEL),
            ("RevertSysctl", ACTION_KERNEL),
            ("SetNvidiaModeset", ACTION_KERNEL),
            ("SpinUpFans", ACTION_THERMAL),
        ];
        for (method, action) in expected {
            assert_eq!(action_for(method), *action, "{method} routes to the wrong action");
        }
        assert_eq!(expected.len(), MUTATING.len(), "a mutating method is unpinned");
    }

    #[test]
    fn reset_fans_never_prompts() {
        // The asymmetry is the point: SpinUpFans takes a channel off the EC
        // curve and prompts; handing it back must not, or a user without a
        // polkit agent cannot restore their own fan curve.
        assert_eq!(action_for("SpinUpFans"), ACTION_THERMAL);
        assert_eq!(action_for("ResetFans"), ACTION_PERF);
    }

    #[test]
    fn read_only_methods_are_not_mutating() {
        for method in ["GetGovernor", "GetPowerLimits", "HasTDPControl", "ReadUndervolt"] {
            assert!(!is_mutating(method), "{method} must not be authorized");
        }
    }

    #[test]
    fn an_unknown_method_defaults_to_the_least_privileged_path() {
        // action_for is total, so a method added without thought lands on
        // ACTION_PERF. That is the safe default only because is_mutating gates
        // it first - an unlisted method is not authorized at all.
        assert!(!is_mutating("SomethingNew"));
    }
}
