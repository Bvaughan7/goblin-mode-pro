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

#[cfg(test)]
mod tests {
    use super::*;
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
