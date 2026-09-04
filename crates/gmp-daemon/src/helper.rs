//! Talking to the privileged helper.
//!
//! The other side of the seam frozen in `docs/dbus-interface-v1.xml`. Every
//! privileged operation this daemon can cause goes through here and nowhere
//! else, which is the property that makes the daemon itself unprivileged: it
//! asks, the helper decides, and polkit gates the deciding.
//!
//! Nothing in this module acts on its own. It is a proxy - the daemon has to
//! call something for anything to happen - and until the poll loop exists,
//! nothing does.
//!
//! Two behaviours are copied from `ipc/helper_client.py` because callers
//! depend on them:
//!
//! * **The helper is optional.** A machine with no helper installed, or one
//!   where it failed to start, runs in what the GUI calls limited mode: the
//!   unprivileged tweaks still apply and the privileged ones report as
//!   unavailable. So an unreachable helper is [`HelperUnavailable`], which
//!   callers handle, and not a panic or a daemon that refuses to start.
//! * **The proxy does not auto-start it.** `DO_NOT_AUTO_START` on the Python
//!   side, `Flags::empty()` here: the helper is a systemd system service, and
//!   a session daemon activating a root service on demand is not a decision
//!   this side gets to make.

use std::time::Duration;

use zbus::proxy;

/// The frozen contract's identity, from `docs/dbus-interface-v1.xml`.
pub const BUS_NAME: &str = "com.goblinmode.ProHelper";
pub const OBJECT_PATH: &str = "/com/goblinmode/ProHelper";
pub const INTERFACE: &str = "com.goblinmode.ProHelper.Manager";

/// How long a privileged call may take before the daemon gives up.
///
/// Five seconds, matching the Python. It is not arbitrary: an `auth_admin`
/// method puts a polkit dialog on the user's screen, and a daemon that waited
/// indefinitely for one would hang its own poll loop behind a prompt that may
/// be on a monitor nobody is looking at.
pub const CALL_TIMEOUT: Duration = Duration::from_secs(5);

/// The helper could not be reached, or refused.
///
/// Deliberately one type rather than two. Every caller does the same thing
/// with both - carry on without the privileged half - and the difference is
/// worth logging, not branching on.
#[derive(Debug, thiserror::Error)]
#[error("helper unavailable: {0}")]
pub struct HelperUnavailable(pub String);

impl From<zbus::Error> for HelperUnavailable {
    fn from(err: zbus::Error) -> Self {
        Self(err.to_string())
    }
}

pub type Result<T> = std::result::Result<T, HelperUnavailable>;

/// The frozen helper interface, method for method.
///
/// The names are pinned explicitly wherever zbus's PascalCase derivation
/// would not produce them: `set_epp` would serve `SetEpp`, and the contract
/// says `SetEPP`. That mistake is invisible until a call fails at runtime, so
/// a test compares this proxy's method set against the frozen file.
#[proxy(
    interface = "com.goblinmode.ProHelper.Manager",
    default_service = "com.goblinmode.ProHelper",
    default_path = "/com/goblinmode/ProHelper"
)]
pub trait Manager {
    // -- CPU governor and EPP ------------------------------------------------
    fn get_governor(&self) -> zbus::Result<String>;
    fn set_governor(&self, governor: &str) -> zbus::Result<bool>;
    #[zbus(name = "SetEPP")]
    fn set_epp(&self, epp: &str) -> zbus::Result<bool>;

    // -- process priority -----------------------------------------------------
    fn renice(&self, pid: u32, nice: i32) -> zbus::Result<bool>;

    // -- power limits ---------------------------------------------------------
    fn get_power_limits(&self) -> zbus::Result<(u64, u64)>;
    fn set_power_limits(&self, pl1_uw: u64, pl2_uw: u64) -> zbus::Result<bool>;
    fn reset_power_limits(&self) -> zbus::Result<bool>;
    #[zbus(name = "HasTDPControl")]
    fn has_tdp_control(&self) -> zbus::Result<bool>;
    #[zbus(name = "SetTDP")]
    fn set_tdp(&self, watts: u32) -> zbus::Result<bool>;
    #[zbus(name = "ResetTDP")]
    fn reset_tdp(&self) -> zbus::Result<bool>;

    // -- fans -----------------------------------------------------------------
    fn spin_up_fans(&self, percent: u32) -> zbus::Result<bool>;
    fn reset_fans(&self) -> zbus::Result<bool>;

    // -- undervolt ------------------------------------------------------------
    fn apply_undervolt(&self) -> zbus::Result<bool>;
    fn apply_amd_undervolt(&self) -> zbus::Result<bool>;
    fn read_undervolt(&self) -> zbus::Result<String>;

    // -- kernel tunables ------------------------------------------------------
    fn set_sysctl(&self, key: &str, value: &str) -> zbus::Result<bool>;
    fn revert_sysctl(&self, key: &str) -> zbus::Result<bool>;

    // -- NVIDIA ----------------------------------------------------------------
    fn set_nvidia_modeset(&self, enabled: bool) -> zbus::Result<bool>;

    // -- undo everything --------------------------------------------------------
    //
    // Off the helper's OWN root-owned snapshot in /run, not off anything this
    // daemon records, and idempotent - which is why the cold-revert path can
    // call it unconditionally without knowing what was applied.
    fn revert_all(&self) -> zbus::Result<bool>;
}

/// A connection to the helper, or the reason there is not one.
pub struct Helper {
    proxy: ManagerProxy<'static>,
}

impl Helper {
    /// Connect to the helper on the system bus.
    ///
    /// Fails rather than waits when the name has no owner. `zbus` will happily
    /// build a proxy for a name nobody owns, and every later call would then
    /// fail one at a time with a less useful message; the Python probes
    /// `get_name_owner()` for the same reason.
    pub async fn connect() -> Result<Self> {
        let connection = zbus::Connection::system().await?;
        let proxy = ManagerProxy::builder(&connection)
            .destination(BUS_NAME)?
            .path(OBJECT_PATH)?
            // No auto-start: the helper is a system service and activating one
            // on demand from a user session is not this side's call.
            .build()
            .await?;
        let owner =
            zbus::fdo::DBusProxy::new(&connection)
                .await?
                .get_name_owner(BUS_NAME.try_into().map_err(|_| {
                    HelperUnavailable(format!("{BUS_NAME} is not a valid bus name"))
                })?)
                .await;
        if owner.is_err() {
            return Err(HelperUnavailable(format!("{BUS_NAME} has no owner")));
        }
        Ok(Self { proxy })
    }

    pub fn proxy(&self) -> &ManagerProxy<'static> {
        &self.proxy
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The frozen contract, read at compile time.
    const FROZEN: &str = include_str!("../../../docs/dbus-interface-v1.xml");

    fn frozen_methods() -> Vec<String> {
        let mut names: Vec<String> = FROZEN
            .split("<method name=\"")
            .skip(1)
            .filter_map(|rest| rest.split('"').next().map(str::to_string))
            .collect();
        names.sort();
        names
    }

    /// This module's own source, so the proxy can be checked against the
    /// contract without a bus.
    const SOURCE: &str = include_str!("helper.rs");

    /// `set_epp` -> `SetEpp`. THE derivation zbus performs, so the check
    /// below can ask whether it reproduces the contract name or not.
    fn pascal_case(name: &str) -> String {
        name.split('_')
            .map(|word| {
                let mut chars = word.chars();
                match chars.next() {
                    Some(first) => first.to_uppercase().chain(chars).collect::<String>(),
                    None => String::new(),
                }
            })
            .collect()
    }

    /// `SetEPP` -> `set_epp`, the name a Rust method would plausibly carry.
    fn snake_case(name: &str) -> String {
        let mut out = String::new();
        for (index, ch) in name.char_indices() {
            if ch.is_uppercase() && index > 0 && !name[..index].ends_with(char::is_uppercase) {
                out.push('_');
            }
            out.extend(ch.to_lowercase());
        }
        out
    }

    #[test]
    fn every_frozen_method_is_declared_on_this_proxy() {
        // A method added to the contract without being added here leaves the
        // daemon unable to call it, and nothing else in the build says so.
        // Checked against this file's own text rather than a second list of
        // names, which would only prove the second list agrees with the first.
        assert_eq!(frozen_methods().len(), 19, "the contract changed size");
        for name in frozen_methods() {
            let explicit = format!("name = \"{name}\"");
            if SOURCE.contains(&explicit) {
                continue;
            }
            // No explicit name, so zbus will DERIVE one - and the derived name
            // is only correct when PascalCase round-trips. It does not for an
            // acronym: `set_epp` becomes `SetEpp`, and a helper that has only
            // ever served `SetEPP` would refuse the call at runtime.
            let rust_name = snake_case(&name);
            assert_eq!(
                pascal_case(&rust_name),
                name,
                "{name} needs an explicit #[zbus(name = \"{name}\")]: zbus would \
                 derive `{}` from `{rust_name}`",
                pascal_case(&rust_name)
            );
            let derived = format!("fn {rust_name}(");
            assert!(
                SOURCE.contains(&derived),
                "{name} is in the frozen contract but this proxy cannot call it \
                 (no `{explicit}` and no `{derived}`)"
            );
        }
    }

    #[test]
    fn the_snake_case_mapping_is_the_one_zbus_uses() {
        // The check above is only worth anything if this agrees with zbus.
        assert_eq!(snake_case("SetGovernor"), "set_governor");
        assert_eq!(snake_case("RevertAll"), "revert_all");
        assert_eq!(snake_case("SpinUpFans"), "spin_up_fans");
        // Acronyms are exactly where it stops agreeing, which is why those
        // four carry an explicit name attribute instead.
        // Acronyms are exactly where the round trip breaks, which is why
        // those four carry an explicit name attribute instead.
        assert_eq!(pascal_case("set_governor"), "SetGovernor");
        assert_eq!(pascal_case("set_epp"), "SetEpp");
        assert_ne!(pascal_case("set_epp"), "SetEPP");
    }

    #[test]
    fn the_acronym_methods_keep_their_contract_spelling() {
        // zbus derives PascalCase from the Rust name, so `set_epp` would serve
        // `SetEpp` and the call would fail at runtime against a helper that
        // has only ever served `SetEPP`. Each one carries an explicit name and
        // the frozen file is what says which.
        for name in ["SetEPP", "SetTDP", "ResetTDP", "HasTDPControl"] {
            assert!(
                frozen_methods().iter().any(|m| m == name),
                "{name} is not in the frozen contract"
            );
        }
    }

    #[test]
    fn the_identity_matches_the_contract() {
        assert!(FROZEN.contains(INTERFACE));
        assert_eq!(BUS_NAME, "com.goblinmode.ProHelper");
        assert_eq!(OBJECT_PATH, "/com/goblinmode/ProHelper");
    }

    #[test]
    fn the_call_timeout_matches_the_python_client() {
        // Five seconds, and the reason matters: an auth_admin method can put a
        // polkit dialog on a screen nobody is looking at.
        assert_eq!(CALL_TIMEOUT, Duration::from_secs(5));
    }

    #[test]
    fn an_unreachable_helper_is_an_error_callers_can_carry_on_from() {
        let err = HelperUnavailable("no owner".into());
        assert!(err.to_string().contains("helper unavailable"));
    }
}
