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

/// The game a launch resolved to, and the three fields of its profile that
/// change what happens next.
#[derive(Debug, Clone, Default)]
pub struct Launch {
    pub exe: String,
    pub display_name: String,
    pub pid: i64,
    pub clip_on_incident: bool,
    pub steam_app_id: String,
}

/// One thing the daemon should do when a game starts, in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LaunchStep {
    RememberPid {
        exe: String,
        pid: i64,
    },
    /// Everything the profile asks for, applied at once.
    Apply {
        exe: String,
        pid: i64,
    },
    EnsureDiagnostics,
    /// Open a session, stamped with the tweaks that turned out to be in force.
    StartSession {
        exe: String,
        game: String,
        tweaks: Vec<String>,
    },
    /// Say the boost is on, and start remembering that it was said.
    AnnounceBoostOn {
        title: String,
        body: String,
    },
    StartClip,
    PrewarmShaders {
        steam_app_id: String,
    },
    BroadcastStatus,
}

/// The three acts every launch begins with, which are not decisions.
///
/// They are separate from the plan below because the plan cannot contain
/// them. The second of them - the apply - is what produces the fingerprint the
/// rest of the plan is drawn from: what a profile ASKS for is not what the
/// machine turns out to allow, and the tweaks a session is stamped with, and
/// the boost the user is told about, are the ones actually in force. So the
/// daemon carries these out, reads the payload's own status, and only then
/// asks what else to do.
///
/// A plan that claimed to include the apply while also depending on its result
/// would be a plan that has to be drawn twice.
pub fn launch_opening(exe: &str, pid: i64) -> Vec<LaunchStep> {
    vec![
        LaunchStep::RememberPid {
            exe: exe.to_string(),
            pid,
        },
        LaunchStep::Apply {
            exe: exe.to_string(),
            pid,
        },
        LaunchStep::EnsureDiagnostics,
    ]
}

/// Everything else a launch implies, given what applying the profile actually
/// changed.
///
/// `tweaks` is the payload's fingerprint after the opening above. Empty means
/// the machine took none of what the profile asked for - a profile with
/// everything switched off, or one whose every tweak this hardware refuses -
/// and an empty fingerprint announces nothing. Telling someone performance
/// mode is on and then listing nothing is worse than staying quiet. The
/// session is still opened, recording that nothing was boosted, which is what
/// makes a later comparison against a boosted run mean anything.
///
/// `boost_announced` is remembered across games rather than per game, and that
/// is deliberate: a second game starting while the first is still being played
/// has not turned anything on, so it says nothing. The flag is cleared by the
/// exit plan's [`ExitStep::AnnounceBoostOff`], and that pairing is what makes
/// this one announcement per boost rather than one per launch.
pub fn launch_plan(game: &Launch, tweaks: &[String], boost_announced: bool) -> Vec<LaunchStep> {
    let mut plan = vec![LaunchStep::StartSession {
        exe: game.exe.clone(),
        game: game.display_name.clone(),
        tweaks: tweaks.to_vec(),
    }];
    if !tweaks.is_empty() && !boost_announced {
        plan.push(LaunchStep::AnnounceBoostOn {
            title: format!("Performance mode on \u{2014} {}", game.display_name),
            body: format!("Boosting: {}", tweaks.join(", ")),
        });
    }
    if game.clip_on_incident {
        plan.push(LaunchStep::StartClip);
    }
    // Best-effort and off the loop's thread in the daemon, but the decision to
    // do it at all is here: no app id, no Steam shader cache to warm.
    if !game.steam_app_id.is_empty() {
        plan.push(LaunchStep::PrewarmShaders {
            steam_app_id: game.steam_app_id.clone(),
        });
    }
    plan.push(LaunchStep::BroadcastStatus);
    plan
}

#[cfg(test)]
mod tests {
    use super::*;

    fn game() -> Launch {
        Launch {
            exe: "Wow.exe".into(),
            display_name: "World of Warcraft".into(),
            pid: 4242,
            ..Launch::default()
        }
    }

    fn boosting() -> Vec<String> {
        vec!["governor".to_string(), "renice".to_string()]
    }

    #[test]
    fn a_launch_always_opens_the_same_way() {
        assert_eq!(
            launch_opening("Wow.exe", 4242),
            vec![
                LaunchStep::RememberPid {
                    exe: "Wow.exe".into(),
                    pid: 4242
                },
                LaunchStep::Apply {
                    exe: "Wow.exe".into(),
                    pid: 4242
                },
                LaunchStep::EnsureDiagnostics,
            ]
        );
    }

    #[test]
    fn the_session_is_stamped_with_what_is_actually_in_force() {
        let plan = launch_plan(&game(), &boosting(), false);
        assert_eq!(
            plan[0],
            LaunchStep::StartSession {
                exe: "Wow.exe".into(),
                game: "World of Warcraft".into(),
                tweaks: boosting(),
            }
        );
    }

    #[test]
    fn a_boost_that_changed_nothing_is_not_announced() {
        // The profile asked for things the machine did not take. Saying
        // performance mode is on and listing nothing is worse than silence.
        let plan = launch_plan(&game(), &[], false);
        assert!(!plan
            .iter()
            .any(|s| matches!(s, LaunchStep::AnnounceBoostOn { .. })));
        assert_eq!(
            plan[0],
            LaunchStep::StartSession {
                exe: "Wow.exe".into(),
                game: "World of Warcraft".into(),
                tweaks: vec![],
            },
            "the session is still opened - it just records nothing boosted"
        );
    }

    #[test]
    fn the_announcement_names_the_game_and_lists_the_tweaks() {
        let plan = launch_plan(&game(), &boosting(), false);
        assert_eq!(
            plan[1],
            LaunchStep::AnnounceBoostOn {
                title: "Performance mode on \u{2014} World of Warcraft".into(),
                body: "Boosting: governor, renice".into(),
            }
        );
    }

    #[test]
    fn a_second_game_does_not_announce_a_boost_that_is_already_on() {
        // The flag is remembered across games, not per game: nothing was
        // turned on by this launch, so there is nothing to say.
        let plan = launch_plan(&game(), &boosting(), true);
        assert!(!plan
            .iter()
            .any(|s| matches!(s, LaunchStep::AnnounceBoostOn { .. })));
    }

    #[test]
    fn a_clip_is_started_only_for_a_profile_that_asked_for_one() {
        let asked = launch_plan(
            &Launch {
                clip_on_incident: true,
                ..game()
            },
            &boosting(),
            false,
        );
        assert!(asked.contains(&LaunchStep::StartClip));
        assert!(!launch_plan(&game(), &boosting(), false).contains(&LaunchStep::StartClip));
    }

    #[test]
    fn shaders_are_prewarmed_only_for_a_game_steam_knows() {
        let steam = launch_plan(
            &Launch {
                steam_app_id: "1343400".into(),
                ..game()
            },
            &boosting(),
            false,
        );
        assert!(steam.contains(&LaunchStep::PrewarmShaders {
            steam_app_id: "1343400".into()
        }));
        assert!(!launch_plan(&game(), &boosting(), false)
            .iter()
            .any(|s| matches!(s, LaunchStep::PrewarmShaders { .. })));
    }

    #[test]
    fn a_launch_runs_in_the_order_the_daemon_runs_it() {
        let plan = launch_plan(
            &Launch {
                clip_on_incident: true,
                steam_app_id: "1343400".into(),
                ..game()
            },
            &boosting(),
            false,
        );
        assert_eq!(
            plan[2..],
            [
                LaunchStep::StartClip,
                LaunchStep::PrewarmShaders {
                    steam_app_id: "1343400".into()
                },
                LaunchStep::BroadcastStatus,
            ]
        );
    }

    #[test]
    fn the_status_goes_out_last_however_quiet_the_launch_was() {
        let plan = launch_plan(&game(), &[], true);
        assert_eq!(plan.last(), Some(&LaunchStep::BroadcastStatus));
    }

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
