//! What the daemon does when a game stops running.
//!
//! The acting is not here. This answers what SHOULD happen, from what the
//! daemon already knows at the moment the observer reports a game gone, so
//! that the answer can be diffed against the Python without a bus, a helper or
//! a game. The daemon carries out the plan and owns everything with a clock or
//! a handle in it: the timers, the payload, the clip recorder, the notifier.

/// What the daemon knows about itself when a game exits.
///
/// `others_running` is the state AFTER this exit - the observer has already
/// dropped the game that left. It is the single most consequential field here:
/// everything in the plan below the session bookkeeping happens only when the
/// machine has no games left at all, and getting it wrong means one game
/// quitting tears down the diagnostics of another that is still being played.
#[derive(Debug, Clone, Default)]
pub struct ExitState {
    pub others_running: bool,
    pub fps_dip_seen: bool,
    pub gpu_available: bool,
    pub forced_boost: bool,
    pub clip_running: bool,
    pub boost_announced: bool,
}

/// One thing the daemon should do, in the order it should do it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExitStep {
    /// Stop treating the game's pid as live. Nothing else may run first: the
    /// pid belongs to a process that is already gone.
    ForgetPid {
        exe: String,
    },
    /// Hand back every tweak this profile asked for.
    Revert {
        exe: String,
    },
    /// Summarise the session, but not yet - MangoHud has a CSV to flush and
    /// the summary is computed from it.
    FinishSessionIn {
        seconds: u32,
        exe: String,
        game: String,
    },
    /// Ask the GPU whether it let go, once the driver has had a moment to.
    FpsPostMortemIn {
        seconds: u32,
    },
    StopDiagnostics,
    StopClip,
    /// Say the boost is over, and stop remembering that it was announced.
    AnnounceBoostOff,
    /// Tell whoever is listening what the daemon looks like now.
    BroadcastStatus,
}

/// Everything a game leaving implies, in order.
///
/// The shape to read first is the split: three steps that belong to the game
/// that left, then - only when it was the LAST one - the teardown of the
/// things the daemon runs for as long as anything is being played at all.
///
/// The session is not summarised here and not summarised now. MangoHud writes
/// its CSV as the game shuts down, and a summary computed before that flush
/// reads a truncated log: fewer frames, a different average, and on a short
/// session no rows at all. Four seconds is the wait the Python has always
/// used.
///
/// Force-boost is the exception running through the teardown twice, and it is
/// the same exception both times: the user asked the machine to stay up. A
/// game exiting is not permission to lower it, so neither the diagnostics that
/// watch it nor the notification that announced it are ended by this.
pub fn exit_plan(exe: &str, game: &str, state: &ExitState) -> Vec<ExitStep> {
    let mut plan = vec![
        ExitStep::ForgetPid {
            exe: exe.to_string(),
        },
        ExitStep::Revert {
            exe: exe.to_string(),
        },
        ExitStep::FinishSessionIn {
            seconds: 4,
            exe: exe.to_string(),
            game: game.to_string(),
        },
    ];
    if !state.others_running {
        // A dip is only worth a post-mortem when there is a GPU to ask, and
        // only after the driver has had a moment to let go of the memory the
        // dip was blamed on - which is the whole question being asked.
        if state.fps_dip_seen && state.gpu_available {
            plan.push(ExitStep::FpsPostMortemIn { seconds: 5 });
        }
        if !state.forced_boost {
            plan.push(ExitStep::StopDiagnostics);
        }
        if state.clip_running {
            plan.push(ExitStep::StopClip);
        }
        if state.boost_announced && !state.forced_boost {
            plan.push(ExitStep::AnnounceBoostOff);
        }
    }
    // Unconditional, and outside the branch in the Python too: a game
    // arriving or leaving changes what a client sees whether or not this
    // daemon found anything to do about it.
    plan.push(ExitStep::BroadcastStatus);
    plan
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> ExitState {
        ExitState::default()
    }

    #[test]
    fn the_last_game_out_puts_the_machine_back() {
        let plan = exit_plan("Wow.exe", "World of Warcraft", &state());
        assert_eq!(
            plan,
            vec![
                ExitStep::ForgetPid {
                    exe: "Wow.exe".into()
                },
                ExitStep::Revert {
                    exe: "Wow.exe".into()
                },
                ExitStep::FinishSessionIn {
                    seconds: 4,
                    exe: "Wow.exe".into(),
                    game: "World of Warcraft".into(),
                },
                ExitStep::StopDiagnostics,
                ExitStep::BroadcastStatus,
            ]
        );
    }

    #[test]
    fn a_game_leaving_while_another_plays_touches_only_its_own_session() {
        // The teardown is for an idle machine. Another game is still being
        // played, and its diagnostics, its clip and its boost are not this
        // game's to end.
        let plan = exit_plan(
            "Wow.exe",
            "World of Warcraft",
            &ExitState {
                others_running: true,
                fps_dip_seen: true,
                gpu_available: true,
                clip_running: true,
                boost_announced: true,
                ..state()
            },
        );
        assert_eq!(
            plan,
            vec![
                ExitStep::ForgetPid {
                    exe: "Wow.exe".into()
                },
                ExitStep::Revert {
                    exe: "Wow.exe".into()
                },
                ExitStep::FinishSessionIn {
                    seconds: 4,
                    exe: "Wow.exe".into(),
                    game: "World of Warcraft".into(),
                },
                ExitStep::BroadcastStatus,
            ]
        );
    }

    #[test]
    fn a_dip_that_was_seen_is_asked_about_after_the_game_is_gone() {
        let plan = exit_plan(
            "x",
            "X",
            &ExitState {
                fps_dip_seen: true,
                gpu_available: true,
                ..state()
            },
        );
        assert!(plan.contains(&ExitStep::FpsPostMortemIn { seconds: 5 }));
    }

    #[test]
    fn a_dip_is_not_asked_about_without_a_gpu_to_ask() {
        let plan = exit_plan(
            "x",
            "X",
            &ExitState {
                fps_dip_seen: true,
                gpu_available: false,
                ..state()
            },
        );
        assert!(!plan
            .iter()
            .any(|s| matches!(s, ExitStep::FpsPostMortemIn { .. })));
    }

    #[test]
    fn no_dip_was_seen_so_there_is_nothing_to_ask_about() {
        let plan = exit_plan(
            "x",
            "X",
            &ExitState {
                fps_dip_seen: false,
                gpu_available: true,
                ..state()
            },
        );
        assert!(!plan
            .iter()
            .any(|s| matches!(s, ExitStep::FpsPostMortemIn { .. })));
    }

    #[test]
    fn a_forced_boost_survives_the_game_that_happened_to_be_running() {
        // Force-boost is the user holding the machine up by hand. A game
        // exiting does not lower it, and the diagnostics that watch it stay on.
        let plan = exit_plan(
            "x",
            "X",
            &ExitState {
                forced_boost: true,
                boost_announced: true,
                ..state()
            },
        );
        assert!(!plan.contains(&ExitStep::StopDiagnostics));
        assert!(!plan.contains(&ExitStep::AnnounceBoostOff));
    }

    #[test]
    fn the_boost_is_announced_off_only_when_it_was_announced_on() {
        let announced = exit_plan(
            "x",
            "X",
            &ExitState {
                boost_announced: true,
                ..state()
            },
        );
        assert!(announced.contains(&ExitStep::AnnounceBoostOff));
        let quiet = exit_plan("x", "X", &state());
        assert!(!quiet.contains(&ExitStep::AnnounceBoostOff));
    }

    #[test]
    fn a_clip_is_stopped_only_when_one_is_running() {
        let recording = exit_plan(
            "x",
            "X",
            &ExitState {
                clip_running: true,
                ..state()
            },
        );
        assert!(recording.contains(&ExitStep::StopClip));
        let idle = exit_plan("x", "X", &state());
        assert!(!idle.contains(&ExitStep::StopClip));
    }

    #[test]
    fn the_teardown_runs_in_the_order_the_daemon_runs_it() {
        // All four optional steps at once, so their relative order is pinned
        // rather than being whatever the last test happened to accept.
        let plan = exit_plan(
            "x",
            "X",
            &ExitState {
                fps_dip_seen: true,
                gpu_available: true,
                clip_running: true,
                boost_announced: true,
                ..state()
            },
        );
        assert_eq!(
            &plan[3..],
            &[
                ExitStep::FpsPostMortemIn { seconds: 5 },
                ExitStep::StopDiagnostics,
                ExitStep::StopClip,
                ExitStep::AnnounceBoostOff,
                ExitStep::BroadcastStatus,
            ]
        );
    }

    #[test]
    fn the_status_goes_out_however_little_else_happened() {
        let plan = exit_plan(
            "x",
            "X",
            &ExitState {
                others_running: true,
                ..state()
            },
        );
        assert_eq!(plan.last(), Some(&ExitStep::BroadcastStatus));
    }
}
