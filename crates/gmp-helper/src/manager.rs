//! The D-Bus interface the helper serves, as frozen in
//! `docs/dbus-interface-v1.xml`.
//!
//! Every method here is a stub that refuses with `NotSupported`. That is the
//! whole deliverable for this block: hardware access is ported group by group
//! afterwards, and a half-ported helper that silently did nothing would be far
//! worse than one that says so.
//!
//! What is NOT stubbed is authorization. A mutating method runs the real
//! polkit check first and only then refuses, which means the routing is
//! observable from outside: `tests/conformance/helper.py --polkit-routing`
//! eavesdrops the `CheckAuthorization` call and can grade this binary today,
//! before it can change a single sysfs file. Wiring the security path first
//! and the hardware second is the point of doing the port in this order.

use zbus::message::Header;
use zbus::object_server::Interface;
use zbus::{interface, Connection};

use crate::error::{HelperError, Result};
use std::path::Path;

use crate::{cpu, fans, polkit, power, renice, sys, sysctl, undervolt};

/// The frozen contract. These three strings are the whole compatibility
/// surface between the Python and Rust helpers, and the conversion plan gets
/// them wrong - it specifies `com.goblinmode.pro.Helper1`, which is a bus
/// nobody in this project talks to. They come from the daemon's client, not
/// from the plan.
pub const BUS_NAME: &str = "com.goblinmode.ProHelper";
pub const OBJECT_PATH: &str = "/com/goblinmode/ProHelper";
pub const INTERFACE: &str = "com.goblinmode.ProHelper.Manager";

pub struct Manager {
    roots: sys::Roots,
}

impl Manager {
    pub fn new(roots: sys::Roots) -> Self {
        Self { roots }
    }
}

/// Refuse a method that exists on the contract but has not been ported.
///
/// `NotSupported` rather than a bespoke error: a caller written against the
/// Python helper already has to handle standard D-Bus errors, and inventing a
/// new error name would be a change to the interface the freeze exists to
/// prevent.
fn unported(method: &str) -> HelperError {
    HelperError::NotImplemented(format!(
        "{method} is not implemented in the Rust helper yet; \
         install the Python helper for this operation"
    ))
}

/// Authorize the call described by `hdr`, or return `AccessDenied`.
///
/// FAILS CLOSED at every step. A message with no sender, a uid that cannot be
/// resolved, an unreachable polkit - none of them are permission to proceed.
/// Read-only methods are not authorized at all, matching the Python helper:
/// they are gated by nothing because they change nothing.
async fn authorize(conn: &Connection, hdr: &Header<'_>) -> Result<()> {
    let method = hdr.member().map(|m| m.as_str()).unwrap_or_default();
    if !polkit::is_mutating(method) {
        return Ok(());
    }
    // No sender means the message did not come through a bus that tracks
    // identity, so there is nobody to authorize. Deny.
    let Some(sender) = hdr.sender().map(|s| s.as_str().to_owned()) else {
        tracing::warn!("denying {method}: the message carries no sender");
        return Err(HelperError::NotAuthorized(
            "could not determine the calling user - refusing".into(),
        ));
    };
    let action = polkit::action_for(method);
    if polkit::check_authorized(conn, &sender, action).await {
        tracing::info!("{sender} authorized for {method} via {action}");
        Ok(())
    } else {
        tracing::warn!("{sender} refused {method} ({action})");
        Err(HelperError::NotAuthorized(format!(
            "polkit authorization denied for {action}"
        )))
    }
}

/// Method bodies are deliberately uniform: authorize, then refuse. Resist the
/// urge to collapse them into a macro - the next block replaces them one at a
/// time, and a macro would have to be unpicked before any of that can start.
#[interface(name = "com.goblinmode.ProHelper.Manager")]
impl Manager {
    // ---- read-only: never authorized, because they change nothing ----

    #[zbus(out_args("governor"))]
    async fn get_governor(&self) -> Result<String> {
        cpu::get_governor(&self.roots.cpu)
            .map_err(|err| HelperError::Failed(format!("could not read the governor: {err}")))
    }

    #[zbus(out_args("pl1_uw", "pl2_uw"))]
    async fn get_power_limits(&self) -> Result<(u64, u64)> {
        power::get_power_limits(&self.roots.rapl)
            .map_err(|err| HelperError::Failed(format!("could not read the power limits: {err}")))
    }

    #[zbus(name = "HasTDPControl", out_args("available"))]
    async fn has_tdp_control(&self) -> Result<bool> {
        Ok(power::has_tdp_control())
    }

    #[zbus(out_args("text"))]
    async fn read_undervolt(&self) -> Result<String> {
        Ok(undervolt::read_undervolt().await)
    }

    // ---- manage-performance ----

    #[zbus(out_args("ok"))]
    async fn set_governor(
        &self,
        governor: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        cpu::set_governor(&self.roots, governor)
    }

    #[zbus(name = "SetEPP", out_args("ok"))]
    async fn set_epp(
        &self,
        epp: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        cpu::set_epp(&self.roots, epp)
    }

    #[zbus(out_args("ok"))]
    async fn renice(
        &self,
        pid: u32,
        nice: i32,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        // Renice is the only method that needs to know WHO is calling, not
        // merely that they are allowed to call. An unresolvable uid is refused
        // here rather than passed down as "unknown", matching the Python
        // dispatch - and renice() fails closed on it a second time anyway.
        let sender = hdr.sender().map(|s| s.as_str().to_owned());
        let uid = match &sender {
            Some(sender) => polkit::caller_uid(conn, sender).await,
            None => None,
        };
        let Some(uid) = uid else {
            return Err(HelperError::NotAuthorized(
                "could not determine the calling user's uid - refusing".into(),
            ));
        };
        renice::renice(pid, nice, Some(uid))
    }

    #[zbus(out_args("ok"))]
    async fn set_power_limits(
        &self,
        pl1_uw: u64,
        pl2_uw: u64,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        power::set_power_limits(&self.roots, pl1_uw, pl2_uw)
    }

    #[zbus(out_args("ok"))]
    async fn reset_power_limits(
        &self,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        power::reset_power_limits(&self.roots)
    }

    #[zbus(name = "SetTDP", out_args("ok"))]
    async fn set_tdp(
        &self,
        watts: u32,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        power::set_tdp(&self.roots, watts).await
    }

    #[zbus(name = "ResetTDP", out_args("ok"))]
    async fn reset_tdp(
        &self,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        power::reset_tdp(&self.roots).await
    }

    #[zbus(out_args("ok"))]
    async fn revert_all(
        &self,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        Err(unported("RevertAll"))
    }

    #[zbus(out_args("ok"))]
    async fn apply_undervolt(
        &self,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        Err(unported("ApplyUndervolt"))
    }

    #[zbus(out_args("ok"))]
    async fn apply_amd_undervolt(
        &self,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        Err(unported("ApplyAmdUndervolt"))
    }

    /// Handing a fan back to the embedded controller is on the permissive
    /// action ON PURPOSE. See `polkit::THERMAL_ACTION_METHODS`.
    #[zbus(out_args("ok"))]
    async fn reset_fans(
        &self,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        fans::reset_fans(&self.roots)
    }

    // ---- manage-kernel-tunables: persistent system configuration ----

    #[zbus(out_args("ok"))]
    async fn set_sysctl(
        &self,
        key: &str,
        value: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        sysctl::set_sysctl(&self.roots, Path::new(sys::PROC_SYS), key, value)
    }

    #[zbus(out_args("ok"))]
    async fn revert_sysctl(
        &self,
        key: &str,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        sysctl::revert_sysctl(&self.roots, Path::new(sys::PROC_SYS), key)
    }

    #[zbus(out_args("ok"))]
    async fn set_nvidia_modeset(
        &self,
        enabled: bool,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        let _ = enabled;
        Err(unported("SetNvidiaModeset"))
    }

    // ---- manage-hardware-thermal ----

    #[zbus(out_args("ok"))]
    async fn spin_up_fans(
        &self,
        percent: u32,
        #[zbus(connection)] conn: &Connection,
        #[zbus(header)] hdr: Header<'_>,
    ) -> Result<bool> {
        authorize(conn, &hdr).await?;
        fans::spin_up_fans(&self.roots, Path::new(sys::HWMON_BASE), percent)
    }
}

/// This binary's own introspection XML, produced without touching a bus.
///
/// `gmp-helper --introspect` prints this so the EXISTING Python freeze test
/// can grade the Rust helper with the same canonicalizer it already uses on
/// the Python one. Reimplementing `tests/_dbusxml.py` in Rust would create a
/// second canonicalizer, and two canonicalizers that drift apart is exactly
/// how a freeze check starts passing for the wrong reason.
pub fn introspection_xml() -> String {
    let mut out = String::from("<node>\n");
    // Introspection reads no files, so the real roots are safe here and this
    // stays runnable in CI and in a container.
    Manager::new(sys::Roots::system()).introspect_to_writer(&mut out, 2);
    out.push_str("</node>\n");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The frozen contract, read at compile time. The Rust side checks the
    /// method SET against it; the byte-for-byte canonical comparison lives in
    /// tests/test_dbus_interface_freeze.py, which owns the canonicalizer.
    const FROZEN: &str = include_str!("../../../docs/dbus-interface-v1.xml");

    fn method_names(xml: &str) -> Vec<String> {
        let mut names: Vec<String> = xml
            .split("<method name=\"")
            .skip(1)
            .filter_map(|rest| rest.split('"').next())
            .map(str::to_owned)
            .collect();
        names.sort();
        names
    }

    #[test]
    fn serves_exactly_the_frozen_method_set() {
        let served = method_names(&introspection_xml());
        let frozen = method_names(FROZEN);
        assert_eq!(frozen.len(), 19, "the frozen contract has 19 methods");
        assert_eq!(
            served, frozen,
            "the Rust helper does not serve the frozen contract"
        );
    }

    #[test]
    fn serves_the_interface_the_daemon_actually_talks_to() {
        // The conversion plan says com.goblinmode.pro.Helper1. Building to
        // that would claim a bus name no caller in this project uses, and the
        // helper would look healthy while nothing could reach it.
        assert!(introspection_xml().contains(INTERFACE));
        assert_eq!(INTERFACE, "com.goblinmode.ProHelper.Manager");
        assert_eq!(BUS_NAME, "com.goblinmode.ProHelper");
        assert_eq!(OBJECT_PATH, "/com/goblinmode/ProHelper");
    }

    #[test]
    fn acronym_methods_keep_their_shouty_names() {
        // zbus derives PascalCase from the Rust name, which would silently
        // serve SetEpp / SetTdp / ResetTdp / HasTdpControl - four methods the
        // daemon would call and never reach. Each carries an explicit name.
        let xml = introspection_xml();
        for method in ["SetEPP", "SetTDP", "ResetTDP", "HasTDPControl"] {
            assert!(
                xml.contains(&format!("name=\"{method}\"")),
                "{method} is misnamed"
            );
        }
    }
}
