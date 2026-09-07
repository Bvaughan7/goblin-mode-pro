//! What should be applied, given everything currently running.
//!
//! The tweaks split in two. Per-game ones - renice, core pinning - belong to
//! one process and are applied and undone with it. GLOBAL ones - the governor,
//! power limits, tearing, VRR, the refresh cap, focus mode - belong to the
//! machine, and with two games running they are shared. So they are
//! recomputed from the whole active set every time it changes, and the answer
//! is the union of what everyone wants.
//!
//! That is the part worth porting and the part hardest to check by hand: the
//! daemon's own conformance suite says so, because grading it needs two real
//! games running at once and it cannot arrange that. Here it is a function of
//! its inputs, so two games is a two-element list.
//!
//! Deciding only. Nothing here talks to the helper, the compositor or the
//! session - the caller does that with the answer.

use serde_json::Value;

use crate::config::{truthy, GameProfile};
use crate::pyfmt::names;
use crate::round::half_even;

/// How the power limits are actually set on this machine.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PowerBackend {
    /// Intel RAPL, in microwatts, as a PL1/PL2 pair.
    Rapl,
    /// AMD `ryzenadj`, which takes a single wattage.
    Ryzenadj,
}

/// A power-limit request, in the units the backend takes.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Power {
    pub backend: PowerBackend,
    /// Microwatts for RAPL, whole watts for ryzenadj.
    pub first: i64,
    /// The PL2 half. Always zero for ryzenadj, which has one number.
    pub second: i64,
}

/// Everything the machine should be doing for the games currently running.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Wanted {
    pub governor: bool,
    pub power: Option<Power>,
    pub fan_spinup: bool,
    /// Whether the privileged helper is needed at all. Anything false here
    /// means the helper's tweaks get restored instead.
    pub helper: bool,
    pub tearing: bool,
    pub adaptive_sync: bool,
    /// Which outputs VRR is restricted to, or `None` for all of them.
    pub vrr_outputs: Option<Vec<String>>,
    /// The refresh cap in Hz, or `None` to leave the panel alone.
    pub refresh_cap: Option<i64>,
    pub focus_mode: bool,
}

/// A profile field as a whole number, or zero when it is not one.
fn watts(value: &Value) -> i64 {
    value.as_i64().unwrap_or_else(|| {
        value
            .as_f64()
            .map(|f| half_even(f) as i64)
            .unwrap_or_default()
    })
}

/// The PL1 this profile asks for, in watts.
///
/// On battery a profile's `battery_pl1_w` replaces its AC one - a handheld's
/// lower on-battery preset - but only when it is actually set. Zero means "no
/// opinion", not "zero watts", so it falls through to the AC value rather than
/// silently capping the machine at nothing.
fn pl1(profile: &GameProfile, on_battery: bool) -> i64 {
    if on_battery && truthy(&profile.battery_pl1_w) {
        watts(&profile.battery_pl1_w)
    } else {
        watts(&profile.pl1_w)
    }
}

fn pl2(profile: &GameProfile, on_battery: bool) -> i64 {
    if on_battery && truthy(&profile.battery_pl2_w) {
        watts(&profile.battery_pl2_w)
    } else {
        watts(&profile.pl2_w)
    }
}

/// The highest PL1/PL2 any active profile asks for, in MICROWATTS.
///
/// Highest, not lowest: these are ceilings being raised, and a second game
/// wanting more headroom should get it rather than being held to the first
/// game's number.
pub fn desired_power_limits_uw(active: &[GameProfile], on_battery: bool) -> (i64, i64) {
    let wanting: Vec<&GameProfile> = active
        .iter()
        // The second half of this test cannot change the answer - adding a
        // profile that asks for zero to a `max` of zeros leaves it at zero -
        // but it is what the Python says, and it states the intent: a profile
        // with the switch on and no numbers is not asking for anything.
        .filter(|p| {
            truthy(&p.power_limit_enabled) && (pl1(p, on_battery) != 0 || pl2(p, on_battery) != 0)
        })
        .collect();
    let highest = |f: fn(&GameProfile, bool) -> i64| {
        wanting.iter().map(|p| f(p, on_battery)).max().unwrap_or(0)
    };
    (highest(pl1) * 1_000_000, highest(pl2) * 1_000_000)
}

/// Recompute every global tweak from the whole active set.
pub fn wanted(active: &[GameProfile], on_battery: bool, tdp_backend: Option<&str>) -> Wanted {
    let any = |field: fn(&GameProfile) -> &Value| active.iter().any(|p| truthy(field(p)));

    let governor = any(|p| &p.governor_boost);
    let (pl1_uw, pl2_uw) = desired_power_limits_uw(active, on_battery);
    let want_power = pl1_uw != 0 || pl2_uw != 0;
    let fan_spinup = any(|p| &p.fan_spinup_enabled);

    let power = want_power.then(|| {
        if tdp_backend == Some("ryzenadj") {
            // One number, in whole watts, and the HIGHER of the pair - the
            // tool has no separate sustained and burst limit to set.
            let first = half_even(pl1_uw as f64 / 1_000_000.0) as i64;
            let second = half_even(pl2_uw as f64 / 1_000_000.0) as i64;
            Power {
                backend: PowerBackend::Ryzenadj,
                first: first.max(second),
                second: 0,
            }
        } else {
            Power {
                backend: PowerBackend::Rapl,
                first: pl1_uw,
                second: pl2_uw,
            }
        }
    });

    // VRR outputs are the union of what every wanting profile named - but a
    // profile that names NONE means "all outputs", and that is the broader
    // ask, so it wins over the others' restrictions.
    let vrr_wanting: Vec<&GameProfile> = active
        .iter()
        .filter(|p| truthy(&p.adaptive_sync_enabled))
        .collect();
    let vrr_outputs =
        if !vrr_wanting.is_empty() && vrr_wanting.iter().all(|p| truthy(&p.vrr_outputs)) {
            let mut union: Vec<String> = vrr_wanting
                .iter()
                .flat_map(|p| names(&p.vrr_outputs))
                .collect();
            union.sort();
            union.dedup();
            Some(union)
        } else {
            None
        };

    // The LOWEST cap wins, unlike the power limits: a cap is a ceiling on the
    // panel, and honouring the highest would ignore whoever asked for less.
    let refresh_cap = active
        .iter()
        .filter(|p| truthy(&p.refresh_rate_hz))
        .map(|p| watts(&p.refresh_rate_hz))
        .min();

    Wanted {
        governor,
        power,
        fan_spinup,
        helper: governor || want_power || fan_spinup,
        tearing: any(|p| &p.tearing_enabled),
        adaptive_sync: !vrr_wanting.is_empty(),
        vrr_outputs,
        refresh_cap,
        focus_mode: any(|p| &p.focus_mode),
    }
}

/// What the governor and EPP are set to for a game.
pub const PERFORMANCE_GOVERNOR: &str = "performance";
pub const PERFORMANCE_EPP: &str = "performance";

/// The privileged tweaks currently in force, as the caller records them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HelperState {
    /// Whether the helper has been asked for anything at all that still
    /// stands. What decides between re-applying and putting it all back.
    pub tweaks_applied: bool,
    pub power_applied: bool,
    /// `"rapl"` or `"ryzenadj"`, whichever set the limit that is in force.
    pub power_backend: Option<String>,
    /// Exactly what was last asked for, in that backend's units.
    pub power_values: Option<(i64, i64)>,
    pub fan_spinup_applied: bool,
}

/// One call to the privileged helper.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "call", rename_all = "PascalCase")]
pub enum HelperStep {
    SetGovernor {
        governor: String,
    },
    #[serde(rename = "SetEPP")]
    SetEpp {
        epp: String,
    },
    ResetPowerLimits,
    #[serde(rename = "ResetTDP")]
    ResetTdp,
    SetPowerLimits {
        pl1_uw: i64,
        pl2_uw: i64,
    },
    #[serde(rename = "SetTDP")]
    SetTdp {
        watts: i64,
    },
    SpinUpFans {
        percent: i64,
    },
    /// Undo the lot, off the helper's own root-owned snapshot.
    RevertAll,
}

/// How hard the fans are asked to spin. All of it - this is a game starting.
pub const FAN_SPINUP_PERCENT: i64 = 100;

/// The privileged calls this recompute implies, in order.
///
/// A plan rather than a sequence of calls, so the ORDER and the conditions can
/// be diffed against the Python without a helper, a bus or a machine.
///
/// Three arms, not two, and the third is easy to miss because it is an `elif`
/// six lines below the other in the Python. When NOTHING wants the helper any
/// more, what happens is not "an apply with nothing in it" - it is
/// `RevertAll`, which undoes the governor and the limits together off the
/// helper's own snapshot. An earlier draft of this function modelled only the
/// apply, and with a stale state it answered `ResetPowerLimits` where the
/// Python reverts everything.
///
/// Within the apply, the rule worth stating is the reset. A power limit that
/// is applied and is no longer what is wanted - a different backend, different
/// numbers, or dropped entirely because the game that asked for it exited - is
/// undone FIRST. Without that a raised TDP leaks until the LAST game exits,
/// which on a laptop means it stays raised through everything the user does
/// next.
///
/// The governor is deliberately NOT reset alongside it: another game may still
/// want it, and the last one leaving is what `RevertAll` is for.
pub fn helper_plan(wanted: &Wanted, state: &HelperState) -> Vec<HelperStep> {
    if !wanted.helper {
        return if state.tweaks_applied {
            vec![HelperStep::RevertAll]
        } else {
            Vec::new()
        };
    }

    let mut plan = Vec::new();
    if wanted.governor {
        plan.push(HelperStep::SetGovernor {
            governor: PERFORMANCE_GOVERNOR.to_string(),
        });
        plan.push(HelperStep::SetEpp {
            epp: PERFORMANCE_EPP.to_string(),
        });
    }

    let want = wanted.power.as_ref().map(|power| {
        let backend = match power.backend {
            PowerBackend::Rapl => "rapl",
            PowerBackend::Ryzenadj => "ryzenadj",
        };
        (backend.to_string(), (power.first, power.second))
    });
    let have = state
        .power_applied
        .then(|| (state.power_backend.clone(), state.power_values));
    let have_matches = match (&have, &want) {
        (Some((backend, values)), Some((wanted_backend, wanted_values))) => {
            backend.as_deref() == Some(wanted_backend.as_str())
                && values.as_ref() == Some(wanted_values)
        }
        _ => false,
    };
    if state.power_applied && !have_matches {
        plan.push(match state.power_backend.as_deref() {
            Some("ryzenadj") => HelperStep::ResetTdp,
            _ => HelperStep::ResetPowerLimits,
        });
    }

    if let Some(power) = &wanted.power {
        match power.backend {
            // One number, and zero watts is not a request - asking for it
            // would cap the machine at nothing.
            PowerBackend::Ryzenadj if power.first != 0 => {
                plan.push(HelperStep::SetTdp { watts: power.first });
            }
            PowerBackend::Ryzenadj => {}
            PowerBackend::Rapl => plan.push(HelperStep::SetPowerLimits {
                pl1_uw: power.first,
                pl2_uw: power.second,
            }),
        }
    }

    // Asked for once. A second request while the fans are already spun up is
    // a call that changes nothing.
    if wanted.fan_spinup && !state.fan_spinup_applied {
        plan.push(HelperStep::SpinUpFans {
            percent: FAN_SPINUP_PERCENT,
        });
    }
    plan
}

/// What the scheduler needs doing, given what is running and what is applied.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum ScxAction {
    /// Already on the right scheduler, or nobody wants one and none is set.
    Nothing,
    /// Nobody wants one any more. Put the machine back.
    Restore,
    Switch {
        scheduler: String,
        mode: String,
        /// Whether to record what is running FIRST. Only on the way in from
        /// nothing: a game that died with a sched_ext scheduler loaded leaves
        /// the WHOLE MACHINE on it, so the revert has to restore what was
        /// there rather than guess, and overwriting the record on a
        /// scheduler-to-scheduler switch would lose the original.
        remember_previous: bool,
    },
}

/// The scheduler the active set asks for, and the mode to run it in.
///
/// Refcounted like the rest, with one extra rule: if two games disagree, the
/// first when sorted wins. That is arbitrary but it is DETERMINISTIC, and the
/// alternative is the pair flapping the machine's scheduler between them for
/// as long as both are running.
pub fn scx_choice(active: &[GameProfile]) -> Option<(String, String)> {
    let mut wanting: Vec<(String, String)> = active
        .iter()
        .filter(|p| truthy(&p.scx_scheduler))
        .map(|p| {
            (
                p.scx_scheduler.as_str().unwrap_or_default().to_string(),
                // The fallback is unreachable in practice and kept for the
                // shape: the config layer normalises `scx_mode` to one of the
                // valid choices on both sides, so anything invalid - a
                // number, a null, a name nobody recognises - has already
                // become "gaming" before it arrives here.
                match p.scx_mode.as_str() {
                    Some(mode) => mode.to_string(),
                    None => "gaming".to_string(),
                },
            )
        })
        .collect();
    wanting.sort();
    wanting.into_iter().next()
}

/// What to do about the scheduler this recompute.
pub fn scx_action(active: &[GameProfile], applied: Option<&str>) -> ScxAction {
    let Some((scheduler, mode)) = scx_choice(active) else {
        // Nothing to put back if nothing was ever switched. `_restore_scx`
        // returns immediately on that, so asking the scheduler manager to
        // restore would be a call the Python never makes.
        return if applied.is_some() {
            ScxAction::Restore
        } else {
            ScxAction::Nothing
        };
    };
    if applied == Some(scheduler.as_str()) {
        return ScxAction::Nothing;
    }
    ScxAction::Switch {
        remember_previous: applied.is_none(),
        scheduler,
        mode,
    }
}

/// The reserved name force-boost applies under.
///
/// It is not a game and there is no process behind it, and the payload checks
/// for it by name in two places rather than trusting the profile's fields -
/// see [`forced_profile`].
pub const FORCED_EXE: &str = "__forced__";

/// One thing a switch that is not a game makes the daemon do.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SwitchStep {
    /// Hand back every tweak, for every profile at once.
    RevertAll,
    /// Forget every live pid. Nothing is being tuned any more.
    ForgetEveryPid,
    StopDiagnostics,
    /// Apply [`forced_profile`].
    ApplyForced,
    EnsureDiagnostics,
    /// Undo it. Only the reserved name is read on the way out.
    RevertForced,
    BroadcastStatus,
}

/// The profile force-boost applies: a DEFAULT profile under the reserved name.
///
/// Worth saying out loud, because it means the switch has no settings of its
/// own. What it does to the machine is whatever a fresh profile asks for -
/// the governor, the tearing hint, the power limits - so changing a profile
/// default changes what this switch does.
///
/// Three fields are set explicitly and none of them is the defence. Renice and
/// core-pinning are already refused for want of a pid, and MangoHud already
/// refuses the reserved name. They are belt and braces, and neither layer is
/// redundant enough to drop: with the name check gone, an overlay switched
/// "off" still means a MangoHud config file WRITTEN with the overlay hidden,
/// not one left alone.
pub fn forced_profile() -> GameProfile {
    let mut profile = GameProfile {
        exe: FORCED_EXE.to_string(),
        display_name: "Forced performance".to_string(),
        renice_enabled: serde_json::json!(false),
        per_game_mangohud: serde_json::json!(false),
        mangohud: match serde_json::json!({"enabled": false}) {
            serde_json::Value::Object(map) => map,
            _ => unreachable!(),
        },
        ..GameProfile::default()
    };
    // `__post_init__` runs on construction in the Python, and it is not
    // cosmetic here: the overlay map passed in holds ONE key, and the fill-in
    // adds the rest. A forced profile that skipped it would carry a different
    // overlay config from every other profile in the tool.
    profile
        .normalise()
        .expect("the reserved name is a valid one");
    profile
}

/// What the master switch does when it is thrown.
///
/// Turning the tool ON does nothing but say so. The observer's next sweep
/// finds whatever is running and starts it through the ordinary path; applying
/// here as well would race that and apply twice.
///
/// Turning it OFF hands everything back at once. It does not wait for the
/// observer, even though the observer will also report every running game as
/// exited on its next tick - with nothing enabled, nothing is found, so
/// everything running gets an exit event. That second teardown is what clears
/// the per-game state this one does not touch, and this one is what makes the
/// switch immediate rather than up to a poll interval late.
///
/// The sampler is the exception, for the same reason it is everywhere else: a
/// forced boost is the user holding the machine up by hand, and the sampler is
/// what watches it. The tweaks still go back - the switch means what it says.
pub fn master_plan(enabled: bool, forced_boost: bool) -> Vec<SwitchStep> {
    let mut plan = Vec::new();
    if !enabled {
        plan.push(SwitchStep::RevertAll);
        plan.push(SwitchStep::ForgetEveryPid);
        if !forced_boost {
            plan.push(SwitchStep::StopDiagnostics);
        }
    }
    plan.push(SwitchStep::BroadcastStatus);
    plan
}

/// What force-boost does when it is thrown.
///
/// On, the sampler starts whether or not anything is being played: the point
/// of the switch is to hold the machine up outside a game, and a boost nothing
/// is watching is a boost nobody can see the effect of.
///
/// Off, it stops only when nothing is being played. A game that is still
/// running still wants its diagnostics, and taking them away because a switch
/// the user threw for something else went back would be the same mistake as
/// one game's exit tearing down another's.
pub fn force_boost_plan(on: bool, games_running: bool) -> Vec<SwitchStep> {
    let mut plan = Vec::new();
    if on {
        plan.push(SwitchStep::ApplyForced);
        plan.push(SwitchStep::EnsureDiagnostics);
    } else {
        plan.push(SwitchStep::RevertForced);
        if !games_running {
            plan.push(SwitchStep::StopDiagnostics);
        }
    }
    plan.push(SwitchStep::BroadcastStatus);
    plan
}

/// Everything the payload currently has in force.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Applied {
    pub helper: HelperState,
    pub tearing: bool,
    pub adaptive_sync: bool,
    pub refresh_cap: bool,
    pub scx: Option<String>,
    pub focus_mode: bool,
}

/// One thing a recompute implies, in the order it should happen.
#[derive(Debug, Clone, PartialEq)]
pub enum RecomputeStep {
    Helper(HelperStep),
    EnableTearing,
    RestoreTearing,
    EnableAdaptiveSync { outputs: Option<Vec<String>> },
    RestoreAdaptiveSync,
    EnableRefreshCap { hz: i64 },
    RestoreRefreshCap,
    Scx(ScxAction),
    EnterFocus,
    ExitFocus,
}

/// Everything one recompute implies, in order.
///
/// The helper half is [`helper_plan`] and the scheduler half is
/// [`scx_action`]; this is the whole of `_recompute_global`, which is the only
/// place their order relative to each other and to the display is decided.
///
/// The display and focus steps are EDGE-TRIGGERED where the helper's are not.
/// The helper is re-asked on every recompute so that a changed profile set is
/// picked up; the compositor is asked only on the transition, because each of
/// those calls is a round trip to a compositor that may be busy drawing a
/// game.
///
/// That has a consequence worth knowing before somebody reports it as a
/// compositor bug: the trigger is "is one applied", not "is THIS one applied".
/// A second game asking for a different refresh cap, or for VRR on different
/// outputs, gets the first game's - the change is not noticed until everything
/// stops wanting it and the tweak goes back. Reproduced rather than fixed,
/// because fixing it is a behaviour change to argue for on its own.
///
/// Whether each `Enable` actually worked is the caller's to record. A
/// compositor that refuses leaves the flag false and the next recompute asks
/// again, which is deliberate - a refusal is often temporary.
pub fn recompute_plan(
    wanted: &Wanted,
    active: &[GameProfile],
    state: &Applied,
) -> Vec<RecomputeStep> {
    let mut plan: Vec<RecomputeStep> = helper_plan(wanted, &state.helper)
        .into_iter()
        .map(RecomputeStep::Helper)
        .collect();

    if wanted.tearing && !state.tearing {
        plan.push(RecomputeStep::EnableTearing);
    } else if !wanted.tearing && state.tearing {
        plan.push(RecomputeStep::RestoreTearing);
    }

    if wanted.adaptive_sync && !state.adaptive_sync {
        plan.push(RecomputeStep::EnableAdaptiveSync {
            outputs: wanted.vrr_outputs.clone(),
        });
    } else if !wanted.adaptive_sync && state.adaptive_sync {
        plan.push(RecomputeStep::RestoreAdaptiveSync);
    }

    match (wanted.refresh_cap, state.refresh_cap) {
        (Some(hz), false) => plan.push(RecomputeStep::EnableRefreshCap { hz }),
        (None, true) => plan.push(RecomputeStep::RestoreRefreshCap),
        _ => {}
    }

    let scx = scx_action(active, state.scx.as_deref());
    if scx != ScxAction::Nothing {
        plan.push(RecomputeStep::Scx(scx));
    }

    if wanted.focus_mode && !state.focus_mode {
        plan.push(RecomputeStep::EnterFocus);
    } else if !wanted.focus_mode && state.focus_mode {
        plan.push(RecomputeStep::ExitFocus);
    }
    plan
}

/// Put everything back, whatever is running.
///
/// Not `recompute_plan` with nothing wanted, and the difference is the ORDER:
/// the scheduler goes back FIRST here. A sched_ext scheduler is loaded for the
/// whole machine rather than for the game, so it is the one tweak whose
/// staying behind is felt by everything the user does next - and the one most
/// worth undoing before anything else has a chance to fail.
pub fn restore_plan(state: &Applied) -> Vec<RecomputeStep> {
    let mut plan = Vec::new();
    if state.scx.is_some() {
        plan.push(RecomputeStep::Scx(ScxAction::Restore));
    }
    if state.helper.tweaks_applied {
        plan.push(RecomputeStep::Helper(HelperStep::RevertAll));
    }
    if state.tearing {
        plan.push(RecomputeStep::RestoreTearing);
    }
    if state.adaptive_sync {
        plan.push(RecomputeStep::RestoreAdaptiveSync);
    }
    if state.refresh_cap {
        plan.push(RecomputeStep::RestoreRefreshCap);
    }
    if state.focus_mode {
        plan.push(RecomputeStep::ExitFocus);
    }
    plan
}

/// What the privileged helper answered when a status was taken.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HelperReply {
    /// Not there at all - it never claimed to be.
    Unavailable,
    /// It answered both questions.
    Answered {
        governor: Option<String>,
        power_limits_uw: (i64, i64),
    },
    /// It said it was there and then stopped answering. `governor` is
    /// whatever it managed before it stopped.
    Failed { governor: Option<String> },
}

/// The four fields of a status that come from the helper rather than from the
/// payload's own bookkeeping.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StatusReads {
    pub governor: Option<String>,
    pub power_limits_w: Option<(i64, i64)>,
    pub helper_available: bool,
    pub limited_mode: bool,
}

/// Microwatts as the status reports them: whole watts, Python's rounding.
///
/// `round()` goes to EVEN on a tie rather than away from zero, and RAPL holds
/// its limits in microwatts, so a half-watt value is an ordinary thing for it
/// to answer. The pair ends up in the status reply the GUI draws and in the
/// `pl:45/60` token stored with every session - so a rounding that disagreed
/// would put a different token in the history and quietly spoil the comparison
/// those tokens exist for.
pub fn power_limits_w(pl1_uw: i64, pl2_uw: i64) -> (i64, i64) {
    let watts = |uw: i64| crate::round::half_even(uw as f64 / 1e6) as i64;
    (watts(pl1_uw), watts(pl2_uw))
}

/// The fields of a status that come from the helper rather than from the
/// payload's own bookkeeping.
///
/// `helper_available` is what the READS did, not what the probe claimed. A
/// helper that answers `available()` and then stops answering is reported
/// unavailable, because that is what the caller will find when it tries to
/// change something.
///
/// The governor it managed to answer before it stopped is still reported. The
/// two reads are separate calls and the helper can die between them; throwing
/// away the answer it did give would be a status that knows less than the
/// daemon does.
pub fn status_reads(reply: &HelperReply) -> StatusReads {
    match reply {
        HelperReply::Unavailable => StatusReads {
            governor: None,
            power_limits_w: None,
            helper_available: false,
            limited_mode: true,
        },
        HelperReply::Answered {
            governor,
            power_limits_uw,
        } => StatusReads {
            governor: governor.clone(),
            power_limits_w: Some(power_limits_w(power_limits_uw.0, power_limits_uw.1)),
            helper_available: true,
            limited_mode: false,
        },
        HelperReply::Failed { governor } => StatusReads {
            governor: governor.clone(),
            power_limits_w: None,
            helper_available: false,
            limited_mode: true,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- what the helper contributes to a status --------------------------

    #[test]
    fn watts_are_microwatts_rounded_the_way_python_rounds() {
        // Halves go to EVEN, not away from zero. RAPL reports in microwatts
        // and a half-watt limit is an ordinary thing for it to hold.
        assert_eq!(power_limits_w(45_000_000, 60_000_000), (45, 60));
        assert_eq!(power_limits_w(45_500_000, 46_500_000), (46, 46));
        assert_eq!(power_limits_w(500_000, 1_500_000), (0, 2));
        assert_eq!(power_limits_w(2_500_000, 3_500_000), (2, 4));
    }

    #[test]
    fn a_limit_of_nothing_is_zero_watts_rather_than_no_answer() {
        // The pair is `None` when the helper could not be asked; a helper
        // that answers zero has answered.
        assert_eq!(power_limits_w(0, 0), (0, 0));
    }

    #[test]
    fn an_unavailable_helper_leaves_every_field_it_owns_empty() {
        let reads = status_reads(&HelperReply::Unavailable);
        assert_eq!(reads.governor, None);
        assert_eq!(reads.power_limits_w, None);
        assert!(!reads.helper_available);
        assert!(
            reads.limited_mode,
            "limited mode is the other side of the coin"
        );
    }

    #[test]
    fn a_helper_that_answers_fills_them_in() {
        let reads = status_reads(&HelperReply::Answered {
            governor: Some("performance".into()),
            power_limits_uw: (45_000_000, 60_000_000),
        });
        assert_eq!(reads.governor.as_deref(), Some("performance"));
        assert_eq!(reads.power_limits_w, Some((45, 60)));
        assert!(reads.helper_available);
        assert!(!reads.limited_mode);
    }

    #[test]
    fn a_helper_that_stops_answering_is_unavailable_however_it_started() {
        // `available()` said yes and a read then raised. What the status
        // reports is what the READS did, not what the probe claimed.
        let reads = status_reads(&HelperReply::Failed { governor: None });
        assert!(!reads.helper_available);
        assert!(reads.limited_mode);
        assert_eq!(reads.power_limits_w, None);
    }

    #[test]
    fn a_governor_read_before_the_failure_is_still_reported() {
        // The two reads are separate calls and the helper can die between
        // them. Throwing away the answer it did give would be a status that
        // knows less than the daemon does.
        let reads = status_reads(&HelperReply::Failed {
            governor: Some("performance".into()),
        });
        assert_eq!(reads.governor.as_deref(), Some("performance"));
        assert!(!reads.helper_available);
    }

    // ---- the whole recompute, in order -------------------------------------

    fn all_wanted() -> Wanted {
        Wanted {
            governor: false,
            power: None,
            fan_spinup: false,
            helper: false,
            tearing: true,
            adaptive_sync: true,
            vrr_outputs: Some(vec!["DP-1".to_string()]),
            refresh_cap: Some(60),
            focus_mode: true,
        }
    }

    fn nothing_wanted() -> Wanted {
        Wanted {
            governor: false,
            power: None,
            fan_spinup: false,
            helper: false,
            tearing: false,
            adaptive_sync: false,
            vrr_outputs: None,
            refresh_cap: None,
            focus_mode: false,
        }
    }

    fn everything_applied() -> Applied {
        Applied {
            tearing: true,
            adaptive_sync: true,
            refresh_cap: true,
            focus_mode: true,
            ..Applied::default()
        }
    }

    #[test]
    fn nothing_wanted_and_nothing_in_force_is_nothing_to_do() {
        assert_eq!(
            recompute_plan(&nothing_wanted(), &[], &Applied::default()),
            vec![]
        );
    }

    #[test]
    fn a_game_starting_turns_the_display_tweaks_on_in_order() {
        assert_eq!(
            recompute_plan(&all_wanted(), &[], &Applied::default()),
            vec![
                RecomputeStep::EnableTearing,
                RecomputeStep::EnableAdaptiveSync {
                    outputs: Some(vec!["DP-1".to_string()]),
                },
                RecomputeStep::EnableRefreshCap { hz: 60 },
                RecomputeStep::EnterFocus,
            ]
        );
    }

    #[test]
    fn the_last_game_leaving_puts_them_all_back() {
        assert_eq!(
            recompute_plan(&nothing_wanted(), &[], &everything_applied()),
            vec![
                RecomputeStep::RestoreTearing,
                RecomputeStep::RestoreAdaptiveSync,
                RecomputeStep::RestoreRefreshCap,
                RecomputeStep::ExitFocus,
            ]
        );
    }

    #[test]
    fn what_is_already_in_force_is_not_asked_for_again() {
        // Edge-triggered, unlike the helper half above it, which re-applies
        // every recompute so that a changed profile set is picked up.
        assert_eq!(
            recompute_plan(&all_wanted(), &[], &everything_applied()),
            vec![]
        );
    }

    #[test]
    fn a_refresh_cap_already_in_force_is_not_changed_by_a_second_game() {
        // Reproduced rather than fixed: the trigger is "is one applied", not
        // "is THIS one applied", so a second game asking for a different cap
        // gets the first game's. Worth knowing before someone reports it as a
        // bug in the compositor.
        let mut wanted = all_wanted();
        wanted.refresh_cap = Some(40);
        assert!(!recompute_plan(&wanted, &[], &everything_applied())
            .iter()
            .any(|s| matches!(s, RecomputeStep::EnableRefreshCap { .. })));
    }

    #[test]
    fn a_change_of_vrr_outputs_is_not_reapplied_either() {
        let mut wanted = all_wanted();
        wanted.vrr_outputs = Some(vec!["HDMI-A-1".to_string()]);
        assert!(!recompute_plan(&wanted, &[], &everything_applied())
            .iter()
            .any(|s| matches!(s, RecomputeStep::EnableAdaptiveSync { .. })));
    }

    #[test]
    fn the_helper_is_dealt_with_before_the_display() {
        let mut wanted = all_wanted();
        wanted.helper = true;
        wanted.governor = true;
        let plan = recompute_plan(&wanted, &[], &Applied::default());
        assert_eq!(
            plan[0],
            RecomputeStep::Helper(HelperStep::SetGovernor {
                governor: PERFORMANCE_GOVERNOR.to_string(),
            })
        );
        assert_eq!(plan[2], RecomputeStep::EnableTearing);
    }

    #[test]
    fn the_scheduler_is_decided_after_the_display_and_before_focus() {
        let mut game = GameProfile {
            exe: "a".into(),
            scx_scheduler: "lavd".into(),
            ..GameProfile::default()
        };
        game.scx_mode = serde_json::json!("gaming");
        let plan = recompute_plan(
            &all_wanted(),
            std::slice::from_ref(&game),
            &Applied::default(),
        );
        let scx = plan.iter().position(|s| matches!(s, RecomputeStep::Scx(_)));
        let focus = plan.iter().position(|s| *s == RecomputeStep::EnterFocus);
        let cap = plan
            .iter()
            .position(|s| matches!(s, RecomputeStep::EnableRefreshCap { .. }));
        assert!(cap < scx && scx < focus, "{plan:?}");
    }

    #[test]
    fn a_scheduler_with_nothing_to_do_is_not_a_step() {
        assert!(!recompute_plan(&nothing_wanted(), &[], &Applied::default())
            .iter()
            .any(|s| matches!(s, RecomputeStep::Scx(_))));
    }

    // ---- and the restore, which is a different order -----------------------

    #[test]
    fn restoring_puts_the_scheduler_back_first() {
        // The whole MACHINE is on it, not just the game - so it goes back
        // before anything else, rather than last with the other tweaks.
        let plan = restore_plan(&Applied {
            scx: Some("lavd".into()),
            helper: HelperState {
                tweaks_applied: true,
                ..HelperState::default()
            },
            ..everything_applied()
        });
        assert_eq!(
            plan,
            vec![
                RecomputeStep::Scx(ScxAction::Restore),
                RecomputeStep::Helper(HelperStep::RevertAll),
                RecomputeStep::RestoreTearing,
                RecomputeStep::RestoreAdaptiveSync,
                RecomputeStep::RestoreRefreshCap,
                RecomputeStep::ExitFocus,
            ]
        );
    }

    #[test]
    fn restoring_what_was_never_applied_does_nothing() {
        assert_eq!(restore_plan(&Applied::default()), vec![]);
    }

    #[test]
    fn restoring_only_undoes_what_is_actually_in_force() {
        assert_eq!(
            restore_plan(&Applied {
                tearing: true,
                ..Applied::default()
            }),
            vec![RecomputeStep::RestoreTearing]
        );
    }

    // ---- the two switches that are not a game -----------------------------

    #[test]
    fn the_forced_profile_is_a_default_one_under_a_reserved_name() {
        // What force-boost DOES is whatever a default profile asks for. That
        // is worth saying out loud: the switch has no settings of its own, so
        // changing a profile default changes what this switch does.
        let forced = forced_profile();
        assert_eq!(forced.exe, FORCED_EXE);
        assert_eq!(forced.display_name, "Forced performance");
        let default = GameProfile::default();
        assert_eq!(forced.governor_boost, default.governor_boost);
        assert_eq!(forced.tearing_enabled, default.tearing_enabled);
        assert_eq!(forced.power_limit_enabled, default.power_limit_enabled);
    }

    #[test]
    fn the_forced_profile_asks_for_nothing_that_needs_a_process() {
        // There is no pid. Each of these is ALSO refused elsewhere - renice
        // and core-pinning are guarded on the pid, and MangoHud on the
        // reserved name - so all three are belt and braces rather than the
        // defence. Neither layer is redundant enough to remove: with the
        // name check gone, an overlay switched "off" still means a written
        // MangoHud config, not an unwritten one.
        let forced = forced_profile();
        assert!(!truthy(&forced.renice_enabled));
        assert!(!truthy(&forced.per_game_mangohud));
        assert_eq!(
            forced.mangohud.get("enabled").map(truthy),
            Some(false),
            "the overlay is off, whatever a profile default says"
        );
    }

    #[test]
    fn turning_the_tool_off_hands_everything_back() {
        assert_eq!(
            master_plan(false, false),
            vec![
                SwitchStep::RevertAll,
                SwitchStep::ForgetEveryPid,
                SwitchStep::StopDiagnostics,
                SwitchStep::BroadcastStatus,
            ]
        );
    }

    #[test]
    fn turning_the_tool_on_changes_nothing_by_itself() {
        // The observer's next sweep finds whatever is running and starts it.
        // Applying here would race that and apply twice.
        assert_eq!(master_plan(true, false), vec![SwitchStep::BroadcastStatus]);
        assert_eq!(master_plan(true, true), vec![SwitchStep::BroadcastStatus]);
    }

    #[test]
    fn a_forced_boost_keeps_the_sampler_when_the_tool_is_switched_off() {
        // The tweaks still go back - the switch means what it says - but the
        // boost the user is holding by hand is still up, and the sampler is
        // what watches it.
        assert_eq!(
            master_plan(false, true),
            vec![
                SwitchStep::RevertAll,
                SwitchStep::ForgetEveryPid,
                SwitchStep::BroadcastStatus,
            ]
        );
    }

    #[test]
    fn forcing_a_boost_applies_the_profile_and_starts_watching() {
        assert_eq!(
            force_boost_plan(true, false),
            vec![
                SwitchStep::ApplyForced,
                SwitchStep::EnsureDiagnostics,
                SwitchStep::BroadcastStatus,
            ]
        );
        assert_eq!(force_boost_plan(true, true), force_boost_plan(true, false));
    }

    #[test]
    fn releasing_a_boost_stops_watching_only_if_nothing_is_playing() {
        assert_eq!(
            force_boost_plan(false, false),
            vec![
                SwitchStep::RevertForced,
                SwitchStep::StopDiagnostics,
                SwitchStep::BroadcastStatus,
            ]
        );
        assert_eq!(
            force_boost_plan(false, true),
            vec![SwitchStep::RevertForced, SwitchStep::BroadcastStatus],
            "a game is still being played and still wants its diagnostics"
        );
    }

    use serde_json::json;

    fn profiles(raw: serde_json::Value) -> Vec<GameProfile> {
        crate::config::from_value(&json!({"profiles": raw})).profiles
    }

    fn one(raw: serde_json::Value) -> Vec<GameProfile> {
        profiles(json!([raw]))
    }

    #[test]
    fn the_governor_boost_default_is_on_which_every_test_here_depends_on() {
        // A fresh profile asks for the governor. Several assertions below are
        // only meaningful because of it, and one of them silently was not
        // until this was written down.
        assert!(truthy(&one(json!({"exe": "a"}))[0].governor_boost));
    }

    #[test]
    fn nothing_running_wants_nothing() {
        let w = wanted(&[], false, None);
        assert!(!w.governor && !w.helper && !w.tearing && !w.focus_mode);
        assert_eq!(w.power, None);
        assert_eq!(w.refresh_cap, None);
    }

    #[test]
    fn one_game_asking_for_the_governor_needs_the_helper() {
        let w = wanted(
            &one(json!({"exe": "a", "governor_boost": true})),
            false,
            None,
        );
        assert!(w.governor && w.helper);
    }

    #[test]
    fn a_global_tweak_is_wanted_if_any_game_wants_it() {
        // The refcount: one game asking is enough, and it stays wanted until
        // nobody is asking.
        let both = profiles(json!([
            {"exe": "a", "tearing_enabled": true},
            {"exe": "b", "tearing_enabled": false},
        ]));
        assert!(wanted(&both, false, None).tearing);
        assert!(!wanted(&both[1..], false, None).tearing);
    }

    #[test]
    fn the_highest_power_limit_wins() {
        // Ceilings being raised: the second game should get its headroom
        // rather than being held to the first game's number.
        let both = profiles(json!([
            {"exe": "a", "power_limit_enabled": true, "pl1_w": 45, "pl2_w": 60},
            {"exe": "b", "power_limit_enabled": true, "pl1_w": 55, "pl2_w": 55},
        ]));
        assert_eq!(
            desired_power_limits_uw(&both, false),
            (55_000_000, 60_000_000)
        );
    }

    #[test]
    fn the_lowest_refresh_cap_wins() {
        // A cap is a ceiling on the panel, so honouring the highest would
        // ignore whoever asked for less. The opposite rule to power limits,
        // and the two sit six lines apart.
        let both = profiles(json!([
            {"exe": "a", "refresh_rate_hz": 144},
            {"exe": "b", "refresh_rate_hz": 60},
        ]));
        assert_eq!(wanted(&both, false, None).refresh_cap, Some(60));
    }

    #[test]
    fn a_profile_with_the_switch_off_asks_for_no_power_limit() {
        let off = one(json!({"exe": "a", "power_limit_enabled": false, "pl1_w": 45}));
        assert_eq!(desired_power_limits_uw(&off, false), (0, 0));
    }

    #[test]
    fn on_battery_a_profile_uses_its_battery_numbers() {
        let p = one(json!({"exe": "a", "power_limit_enabled": true, "pl1_w": 45,
                           "pl2_w": 60, "battery_pl1_w": 25, "battery_pl2_w": 30}));
        assert_eq!(desired_power_limits_uw(&p, true), (25_000_000, 30_000_000));
        assert_eq!(desired_power_limits_uw(&p, false), (45_000_000, 60_000_000));
    }

    #[test]
    fn an_unset_battery_number_falls_back_to_the_ac_one() {
        // Zero means "no opinion", not "cap the machine at nothing".
        let p = one(json!({"exe": "a", "power_limit_enabled": true, "pl1_w": 45,
                           "pl2_w": 60, "battery_pl1_w": 0}));
        assert_eq!(desired_power_limits_uw(&p, true), (45_000_000, 60_000_000));
    }

    #[test]
    fn ryzenadj_gets_one_number_and_it_is_the_higher_of_the_pair() {
        let p = one(json!({"exe": "a", "power_limit_enabled": true, "pl1_w": 45,
                           "pl2_w": 60}));
        let w = wanted(&p, false, Some("ryzenadj"));
        assert_eq!(
            w.power,
            Some(Power {
                backend: PowerBackend::Ryzenadj,
                first: 60,
                second: 0
            })
        );
    }

    #[test]
    fn rapl_gets_the_pair_in_microwatts() {
        let p = one(json!({"exe": "a", "power_limit_enabled": true, "pl1_w": 45,
                           "pl2_w": 60}));
        let w = wanted(&p, false, Some("rapl"));
        assert_eq!(
            w.power,
            Some(Power {
                backend: PowerBackend::Rapl,
                first: 45_000_000,
                second: 60_000_000
            })
        );
    }

    #[test]
    fn vrr_outputs_are_the_union_of_what_everyone_named() {
        let both = profiles(json!([
            {"exe": "a", "adaptive_sync_enabled": true, "vrr_outputs": ["DP-1"]},
            {"exe": "b", "adaptive_sync_enabled": true, "vrr_outputs": ["HDMI-1", "DP-1"]},
        ]));
        assert_eq!(
            wanted(&both, false, None).vrr_outputs,
            Some(vec!["DP-1".to_string(), "HDMI-1".to_string()])
        );
    }

    #[test]
    fn a_profile_naming_no_outputs_means_all_of_them() {
        // The broader ask wins: naming none is "every output", and that
        // cannot be narrowed by somebody else's restriction.
        let both = profiles(json!([
            {"exe": "a", "adaptive_sync_enabled": true, "vrr_outputs": ["DP-1"]},
            {"exe": "b", "adaptive_sync_enabled": true, "vrr_outputs": []},
        ]));
        let w = wanted(&both, false, None);
        assert!(w.adaptive_sync);
        assert_eq!(w.vrr_outputs, None, "None means every output");
    }

    #[test]
    fn a_profile_not_asking_for_vrr_does_not_contribute_outputs() {
        let both = profiles(json!([
            {"exe": "a", "adaptive_sync_enabled": true, "vrr_outputs": ["DP-1"]},
            {"exe": "b", "adaptive_sync_enabled": false, "vrr_outputs": ["HDMI-1"]},
        ]));
        assert_eq!(
            wanted(&both, false, None).vrr_outputs,
            Some(vec!["DP-1".to_string()])
        );
    }

    fn plan_for(raw: serde_json::Value, state: HelperState) -> Vec<HelperStep> {
        let active = one(raw);
        helper_plan(&wanted(&active, false, Some("rapl")), &state)
    }

    #[test]
    fn wanting_the_governor_sets_it_and_the_epp_together() {
        // EPP is the finer knob and on intel_pstate it is the one that
        // actually moves; setting the governor without it does half the job.
        let plan = plan_for(
            json!({"exe": "a", "governor_boost": true}),
            HelperState::default(),
        );
        assert_eq!(
            plan,
            vec![
                HelperStep::SetGovernor {
                    governor: "performance".into()
                },
                HelperStep::SetEpp {
                    epp: "performance".into()
                },
            ]
        );
    }

    #[test]
    fn a_power_limit_that_changed_is_reset_before_it_is_re_applied() {
        // Otherwise the raised TDP leaks until the LAST game exits, which on
        // a laptop means it stays raised through whatever comes next.
        let state = HelperState {
            power_applied: true,
            power_backend: Some("rapl".into()),
            power_values: Some((45_000_000, 60_000_000)),
            ..HelperState::default()
        };
        let plan = plan_for(
            json!({"exe": "a", "governor_boost": false, "power_limit_enabled": true,
                   "pl1_w": 55, "pl2_w": 60}),
            state,
        );
        assert_eq!(
            plan,
            vec![
                HelperStep::ResetPowerLimits,
                HelperStep::SetPowerLimits {
                    pl1_uw: 55_000_000,
                    pl2_uw: 60_000_000
                },
            ]
        );
    }

    #[test]
    fn an_unchanged_power_limit_is_not_reset() {
        let state = HelperState {
            power_applied: true,
            power_backend: Some("rapl".into()),
            power_values: Some((45_000_000, 60_000_000)),
            ..HelperState::default()
        };
        let plan = plan_for(
            json!({"exe": "a", "governor_boost": false, "power_limit_enabled": true,
                   "pl1_w": 45, "pl2_w": 60}),
            state,
        );
        assert_eq!(
            plan,
            vec![HelperStep::SetPowerLimits {
                pl1_uw: 45_000_000,
                pl2_uw: 60_000_000
            }]
        );
    }

    #[test]
    fn a_power_limit_nobody_wants_any_more_is_reset_and_not_replaced() {
        // The two-game case: the one that asked for headroom exits, the one
        // that only wanted the governor stays. The helper is still wanted, so
        // this is not the RevertAll arm - and the limit still has to come off,
        // or it stands until the LAST game exits.
        let state = HelperState {
            tweaks_applied: true,
            power_applied: true,
            power_backend: Some("rapl".into()),
            power_values: Some((45_000_000, 60_000_000)),
            ..HelperState::default()
        };
        let plan = plan_for(json!({"exe": "a", "governor_boost": true}), state);
        assert_eq!(
            plan,
            vec![
                HelperStep::SetGovernor {
                    governor: "performance".into()
                },
                HelperStep::SetEpp {
                    epp: "performance".into()
                },
                HelperStep::ResetPowerLimits,
            ]
        );
    }

    #[test]
    fn a_backend_change_resets_through_the_old_backend() {
        // ResetTDP undoes ryzenadj; ResetPowerLimits undoes RAPL. Resetting
        // through the new one would leave the old limit in place.
        let state = HelperState {
            power_applied: true,
            power_backend: Some("ryzenadj".into()),
            power_values: Some((45, 0)),
            ..HelperState::default()
        };
        let active = one(json!({"exe": "a", "governor_boost": false,
                                "power_limit_enabled": true, "pl1_w": 55}));
        let plan = helper_plan(&wanted(&active, false, Some("rapl")), &state);
        assert_eq!(plan[0], HelperStep::ResetTdp);
    }

    #[test]
    fn ryzenadj_asks_for_zero_watts_never() {
        // Zero is "no opinion"; asking for it would cap the machine at
        // nothing. The Python guards the call the same way.
        //
        // Built by hand rather than from a profile, because no profile can
        // produce it: `GameProfile` truncates and clamps every wattage into
        // [0, 500] on both sides, so an ask that survives to here is at least
        // one whole watt and `wanted` would answer `helper: false` for
        // anything less. The guard is unreachable while that clamp stands and
        // is kept because the Python keeps it - if either side ever stops
        // clamping, this is what stops a zero going out.
        let asked = Wanted {
            governor: false,
            power: Some(Power {
                backend: PowerBackend::Ryzenadj,
                first: 0,
                second: 0,
            }),
            fan_spinup: false,
            helper: true,
            tearing: false,
            adaptive_sync: false,
            vrr_outputs: None,
            refresh_cap: None,
            focus_mode: false,
        };
        assert!(helper_plan(&asked, &HelperState::default()).is_empty());
    }

    #[test]
    fn the_fans_are_asked_once() {
        let raw = json!({"exe": "a", "governor_boost": false,
                         "fan_spinup_enabled": true});
        assert_eq!(
            plan_for(raw.clone(), HelperState::default()),
            vec![HelperStep::SpinUpFans { percent: 100 }]
        );
        let already = HelperState {
            fan_spinup_applied: true,
            ..HelperState::default()
        };
        assert!(plan_for(raw, already).is_empty());
    }

    #[test]
    fn nothing_wanted_and_nothing_applied_is_no_calls_at_all() {
        assert!(helper_plan(&wanted(&[], false, None), &HelperState::default()).is_empty());
    }

    #[test]
    fn nothing_wanted_any_more_puts_everything_back_at_once() {
        // Not a reset of the power limit alone: the last game has gone, so
        // the governor goes back too, and RevertAll does both off the
        // helper's own snapshot rather than off anything recorded here.
        let state = HelperState {
            tweaks_applied: true,
            power_applied: true,
            power_backend: Some("rapl".into()),
            power_values: Some((45_000_000, 60_000_000)),
            fan_spinup_applied: true,
        };
        assert_eq!(
            helper_plan(&wanted(&[], false, None), &state),
            vec![HelperStep::RevertAll]
        );
    }

    #[test]
    fn a_quiet_profile_reverts_what_a_loud_one_left() {
        // The daemon's own case: two games, the one that wanted everything
        // exits, and the recompute runs with only the quiet one left.
        let state = HelperState {
            tweaks_applied: true,
            ..HelperState::default()
        };
        assert_eq!(
            plan_for(json!({"exe": "a", "governor_boost": false}), state),
            vec![HelperStep::RevertAll]
        );
    }

    #[test]
    fn nobody_wanting_a_scheduler_puts_the_machine_back() {
        assert_eq!(
            scx_action(&one(json!({"exe": "a"})), Some("rusty")),
            ScxAction::Restore
        );
    }

    #[test]
    fn nothing_wanted_and_nothing_applied_is_not_a_restore() {
        // There is nothing to put back, and the Python's `_restore_scx`
        // returns immediately rather than calling the scheduler manager.
        assert_eq!(scx_action(&[], None), ScxAction::Nothing);
        assert_eq!(
            scx_action(&one(json!({"exe": "a"})), None),
            ScxAction::Nothing
        );
    }

    #[test]
    fn the_first_scheduler_when_sorted_wins() {
        // Arbitrary but DETERMINISTIC. The alternative is two games flapping
        // the machine's scheduler between them for as long as both run.
        let both = profiles(json!([
            {"exe": "a", "scx_scheduler": "rusty"},
            {"exe": "b", "scx_scheduler": "lavd"},
        ]));
        assert_eq!(
            scx_choice(&both),
            Some(("lavd".to_string(), "gaming".to_string()))
        );
        // And the other way round in the list, to prove it is the sort and
        // not the order they happened to arrive in.
        let reversed: Vec<GameProfile> = both.into_iter().rev().collect();
        assert_eq!(
            scx_choice(&reversed),
            Some(("lavd".to_string(), "gaming".to_string()))
        );
    }

    #[test]
    fn the_previous_scheduler_is_remembered_only_on_the_way_in() {
        // A game that died with sched_ext loaded leaves the WHOLE machine on
        // it, so the revert restores what was there. Overwriting that record
        // on a scheduler-to-scheduler switch would lose the original.
        let p = one(json!({"exe": "a", "scx_scheduler": "rusty"}));
        assert_eq!(
            scx_action(&p, None),
            ScxAction::Switch {
                scheduler: "rusty".into(),
                mode: "gaming".into(),
                remember_previous: true,
            }
        );
        assert_eq!(
            scx_action(&p, Some("lavd")),
            ScxAction::Switch {
                scheduler: "rusty".into(),
                mode: "gaming".into(),
                remember_previous: false,
            }
        );
    }

    #[test]
    fn already_on_the_right_scheduler_is_nothing_to_do() {
        let p = one(json!({"exe": "a", "scx_scheduler": "rusty"}));
        assert_eq!(scx_action(&p, Some("rusty")), ScxAction::Nothing);
    }

    #[test]
    fn a_profile_with_no_scheduler_asks_for_nothing() {
        assert_eq!(
            scx_choice(&one(json!({"exe": "a", "scx_scheduler": ""}))),
            None
        );
    }

    #[test]
    fn the_mode_defaults_to_gaming() {
        let p = one(json!({"exe": "a", "scx_scheduler": "rusty"}));
        assert_eq!(scx_choice(&p).unwrap().1, "gaming");
        let explicit = one(json!({"exe": "a", "scx_scheduler": "rusty",
                                  "scx_mode": "powersave"}));
        assert_eq!(scx_choice(&explicit).unwrap().1, "powersave");
    }

    #[test]
    fn the_helper_is_needed_for_any_of_its_three_tweaks_and_no_others() {
        for field in ["governor_boost", "fan_spinup_enabled"] {
            let p = one(json!({"exe": "a", field: true}));
            assert!(wanted(&p, false, None).helper, "{field}");
        }
        // Compositor and focus tweaks are unprivileged: they must NOT drag
        // the helper in, or a machine without one would lose them too.
        // `governor_boost` defaults to TRUE, so it is switched off explicitly
        // here - otherwise every profile would need the helper and this would
        // pass without testing anything.
        for field in ["tearing_enabled", "adaptive_sync_enabled", "focus_mode"] {
            let p = one(json!({"exe": "a", "governor_boost": false, field: true}));
            assert!(!wanted(&p, false, None).helper, "{field}");
        }
    }
}
