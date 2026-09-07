//! Focus mode: quieting the desktop while a game is up.
//!
//! Suspending the file indexer, holding off the screensaver, and turning on
//! Do Not Disturb - and, more importantly, undoing all three afterwards. The
//! running of commands and the D-Bus call stay with the caller; what is here
//! is which commands, in what order, and under what conditions, because that
//! is where a focus mode that never lifts comes from.

/// Which of the indexer tools are installed.
///
/// Only the indexer, because that is the only choice the plan makes. Whether
/// Do Not Disturb can be set is decided where it is set - it needs the desktop
/// as well as the tool, and neither is a fact about the plan. See [`is_kde`].
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Tools {
    pub balooctl6: bool,
    pub balooctl: bool,
    pub tracker3: bool,
}

/// What focus mode has already done, as the caller records it.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct State {
    pub active: bool,
    pub baloo_suspended: bool,
    pub tracker_paused: bool,
}

/// Which flag a step touches.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flag {
    None,
    BalooSuspended,
    TrackerPaused,
}

/// One command, and what running it means.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub argv: Vec<String>,
    /// Set when the command STARTS. A tool that could not be run has
    /// suspended nothing.
    pub records: Flag,
}

/// One thing to do.
///
/// The two command variants are the two stopping rules, spelled apart because
/// this module uses both and they are a line apart in the Python.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// Try each in order; the first that RUNS wins and the rest are skipped.
    /// A tool that is installed and cannot be started has not suspended
    /// anything, so the next candidate gets a turn.
    FirstThatRuns(Vec<Command>),
    /// Run it, and carry on whatever happens.
    Run(Command),
    /// Clear a flag, whether or not the command before it worked.
    ///
    /// The Python clears these OUTSIDE the loop that resumes: having tried,
    /// it stops claiming the indexer is suspended. Leaving the flag set
    /// because the resume failed would mean trying again on the next exit,
    /// which is a second resume of an indexer somebody may have re-suspended
    /// themselves.
    Forget(Flag),
    /// Ask the screensaver to hold off.
    InhibitIdle,
    /// Let it resume. The inhibit dies with the process that held it, which
    /// is why the cold restore below does not do this.
    UninhibitIdle,
    /// Plasma's Do Not Disturb, which is stored as a far-future "not before"
    /// and cleared by emptying it.
    SetKdeDnd(bool),
}

fn command(argv: &[&str], records: Flag) -> Command {
    Command {
        argv: argv.iter().map(|a| (*a).to_string()).collect(),
        records,
    }
}

/// Carry out a plan, given what was already true and which commands can be
/// started.
///
/// Returns the commands that were run, in order, and the flags afterwards.
/// This is where the two stopping rules become observable, which is why it is
/// here rather than in the caller: a plan is only half an answer until
/// something says how it is walked.
///
/// The state comes IN as well as out, because not every plan clears what it
/// acts on - see [`force_restore_plan`], which resumes both indexers and
/// leaves the flags exactly as it found them, having no idea what they should
/// become.
pub fn carry_out(
    plan: &[Step],
    state: &State,
    spawns: &dyn Fn(&[String]) -> bool,
) -> (Vec<Vec<String>>, State) {
    let mut ran = Vec::new();
    let mut state = *state;
    for step in plan {
        match step {
            Step::FirstThatRuns(candidates) => {
                for candidate in candidates {
                    ran.push(candidate.argv.clone());
                    if spawns(&candidate.argv) {
                        set(&mut state, candidate.records, true);
                        break;
                    }
                }
            }
            Step::Run(candidate) => {
                ran.push(candidate.argv.clone());
                if spawns(&candidate.argv) {
                    set(&mut state, candidate.records, true);
                }
            }
            Step::Forget(flag) => set(&mut state, *flag, false),
            _ => {}
        }
    }
    (ran, state)
}

fn set(state: &mut State, flag: Flag, value: bool) {
    match flag {
        Flag::None => {}
        Flag::BalooSuspended => state.baloo_suspended = value,
        Flag::TrackerPaused => state.tracker_paused = value,
    }
}

/// Turning focus mode on.
///
/// Nothing at all when it is already on: every step here is one a second
/// caller would repeat, and repeating the indexer suspend is how you end up
/// with a resume that does not resume.
///
/// Both Baloo spellings are offered before Tracker - `balooctl6` is the Plasma
/// 6 name and `balooctl` the older one - and the first that STARTS wins.
pub fn enter_plan(tools: &Tools, state: &State) -> Vec<Step> {
    if state.active {
        return Vec::new();
    }
    let mut attempts = Vec::new();
    if tools.balooctl6 {
        attempts.push(command(&["balooctl6", "suspend"], Flag::BalooSuspended));
    }
    if tools.balooctl {
        attempts.push(command(&["balooctl", "suspend"], Flag::BalooSuspended));
    }
    if tools.tracker3 {
        attempts.push(command(
            &["tracker3", "daemon", "--pause", "goblin-mode-pro"],
            Flag::TrackerPaused,
        ));
    }
    let mut plan = Vec::new();
    if !attempts.is_empty() {
        plan.push(Step::FirstThatRuns(attempts));
    }
    plan.push(Step::InhibitIdle);
    plan.push(Step::SetKdeDnd(true));
    plan
}

/// Turning it off again.
///
/// The indexer is resumed only if this process suspended it. Resuming one it
/// did not touch would un-pause an indexer the user paused themselves.
pub fn exit_plan(tools: &Tools, state: &State) -> Vec<Step> {
    if !state.active {
        return Vec::new();
    }
    let mut plan = Vec::new();
    if state.baloo_suspended {
        let mut attempts = Vec::new();
        if tools.balooctl6 {
            attempts.push(command(&["balooctl6", "resume"], Flag::None));
        }
        if tools.balooctl {
            attempts.push(command(&["balooctl", "resume"], Flag::None));
        }
        if !attempts.is_empty() {
            plan.push(Step::FirstThatRuns(attempts));
        }
        plan.push(Step::Forget(Flag::BalooSuspended));
    }
    if state.tracker_paused {
        plan.push(Step::Run(command(
            &["tracker3", "daemon", "--resume"],
            Flag::None,
        )));
        plan.push(Step::Forget(Flag::TrackerPaused));
    }
    plan.push(Step::UninhibitIdle);
    plan.push(Step::SetKdeDnd(false));
    plan
}

/// Undoing everything after a crash, with no flags left to consult.
///
/// Three differences from [`exit_plan`], and each is deliberate.
///
/// Both indexers are resumed rather than the one that was suspended, because
/// there is no record of which. A resume of an indexer that was already
/// running is harmless; leaving a suspended one is not.
///
/// The Baloo choice is made on EXISTENCE rather than on starting - a plain
/// `Run` of one tool, not a `FirstThatRuns` of both. The suspend path needs
/// one to have worked; here a tool that is present and fails has still had its
/// turn, and both names are the same indexer.
///
/// And the screensaver is not touched. The inhibit was held by a process that
/// is gone, and the session released it when that process died; asking to
/// release a cookie this process never held would be asking about nothing.
pub fn force_restore_plan(tools: &Tools) -> Vec<Step> {
    let mut plan = Vec::new();
    if tools.balooctl6 {
        plan.push(Step::Run(command(&["balooctl6", "resume"], Flag::None)));
    } else if tools.balooctl {
        plan.push(Step::Run(command(&["balooctl", "resume"], Flag::None)));
    }
    if tools.tracker3 {
        plan.push(Step::Run(command(
            &["tracker3", "daemon", "--resume"],
            Flag::None,
        )));
    }
    plan.push(Step::SetKdeDnd(false));
    plan
}

/// The value Plasma stores for Do Not Disturb.
///
/// A far-future "not before" turns it on and an empty one clears it. There is
/// no boolean; this IS the switch.
pub fn kde_dnd_value(on: bool) -> &'static str {
    if on {
        "2099-01-01T00:00:00"
    } else {
        ""
    }
}

/// Whether this desktop is one where the Do Not Disturb switch means
/// anything. Matched case-insensitively on a substring, because
/// `XDG_CURRENT_DESKTOP` is a colon-separated list and "KDE" may not be alone
/// in it.
pub fn is_kde(xdg_current_desktop: &str) -> bool {
    xdg_current_desktop.to_uppercase().contains("KDE")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn all() -> Tools {
        Tools {
            balooctl6: true,
            balooctl: true,
            tracker3: true,
        }
    }

    /// Everything a plan would run if every command starts.
    fn ran(plan: &[Step]) -> Vec<Vec<String>> {
        carry_out(plan, &State::default(), &|_| true).0
    }

    #[test]
    fn entering_twice_does_nothing_the_second_time() {
        let on = State {
            active: true,
            ..State::default()
        };
        assert!(enter_plan(&all(), &on).is_empty());
    }

    #[test]
    fn leaving_when_it_was_never_on_does_nothing() {
        assert!(exit_plan(&all(), &State::default()).is_empty());
    }

    #[test]
    fn the_first_indexer_that_starts_wins_and_the_rest_are_skipped() {
        let plan = enter_plan(&all(), &State::default());
        assert_eq!(ran(&plan), vec![vec!["balooctl6", "suspend"]]);
        let (_, state) = carry_out(&plan, &State::default(), &|_| true);
        assert!(state.baloo_suspended);
        assert!(!state.tracker_paused);
    }

    #[test]
    fn a_tool_that_will_not_start_hands_over_to_the_next() {
        // Installed and unusable is not a suspended indexer.
        let plan = enter_plan(&all(), &State::default());
        let (ran, state) = carry_out(&plan, &State::default(), &|argv| {
            !argv[0].starts_with("baloo")
        });
        assert_eq!(
            ran,
            vec![
                vec!["balooctl6", "suspend"],
                vec!["balooctl", "suspend"],
                vec!["tracker3", "daemon", "--pause", "goblin-mode-pro"],
            ]
        );
        assert!(state.tracker_paused);
        assert!(!state.baloo_suspended);
    }

    #[test]
    fn a_missing_tool_is_never_offered() {
        let only_tracker = Tools {
            tracker3: true,
            ..Tools::default()
        };
        let plan = enter_plan(&only_tracker, &State::default());
        assert_eq!(
            ran(&plan),
            vec![vec!["tracker3", "daemon", "--pause", "goblin-mode-pro"]]
        );
        assert!(plan.contains(&Step::InhibitIdle));
    }

    #[test]
    fn do_not_disturb_is_asked_for_whatever_the_desktop_is() {
        // The plan says to set it; whether that means anything is decided
        // where it is set, because it needs the desktop as well as the tool.
        let plan = enter_plan(&Tools::default(), &State::default());
        assert!(plan.contains(&Step::SetKdeDnd(true)));
        assert!(exit_plan(
            &all(),
            &State {
                active: true,
                ..State::default()
            }
        )
        .contains(&Step::SetKdeDnd(false)));
        assert!(force_restore_plan(&Tools::default()).contains(&Step::SetKdeDnd(false)));
    }

    #[test]
    fn only_the_indexer_this_process_suspended_is_resumed() {
        let baloo = State {
            active: true,
            baloo_suspended: true,
            ..State::default()
        };
        assert_eq!(
            ran(&exit_plan(&all(), &baloo)),
            vec![vec!["balooctl6", "resume"]]
        );

        let tracker = State {
            active: true,
            tracker_paused: true,
            ..State::default()
        };
        assert_eq!(
            ran(&exit_plan(&all(), &tracker)),
            vec![vec!["tracker3", "daemon", "--resume"]]
        );

        let neither = State {
            active: true,
            ..State::default()
        };
        assert!(ran(&exit_plan(&all(), &neither)).is_empty());
    }

    #[test]
    fn a_cold_restore_resumes_both_because_it_cannot_know_which() {
        assert_eq!(
            ran(&force_restore_plan(&all())),
            vec![
                vec!["balooctl6", "resume"],
                vec!["tracker3", "daemon", "--resume"],
            ]
        );
    }

    #[test]
    fn a_cold_restore_picks_a_baloo_name_by_existence_not_by_starting() {
        // Unlike the suspend path. A tool that is present and fails has had
        // its turn - both names are the same indexer.
        let plan = force_restore_plan(&all());
        let (ran, _) = carry_out(&plan, &State::default(), &|_| false);
        assert_eq!(
            ran,
            vec![
                vec!["balooctl6", "resume"],
                vec!["tracker3", "daemon", "--resume"],
            ],
            "the older name is not tried after the newer one fails"
        );

        let older = Tools {
            balooctl6: false,
            ..all()
        };
        assert_eq!(
            ran_first(&force_restore_plan(&older)),
            vec!["balooctl", "resume"]
        );
    }

    fn ran_first(plan: &[Step]) -> Vec<String> {
        ran(plan)
            .into_iter()
            .next()
            .expect("a plan with a command in it")
    }

    #[test]
    fn a_cold_restore_leaves_the_screensaver_alone() {
        let plan = force_restore_plan(&all());
        assert!(!plan.contains(&Step::UninhibitIdle));
        assert!(!plan.contains(&Step::InhibitIdle));
    }

    #[test]
    fn do_not_disturb_is_a_date_rather_than_a_switch() {
        assert_eq!(kde_dnd_value(true), "2099-01-01T00:00:00");
        assert_eq!(kde_dnd_value(false), "");
    }

    #[test]
    fn the_desktop_is_recognised_inside_a_list_and_in_any_case() {
        assert!(is_kde("KDE"));
        assert!(is_kde("kde"));
        assert!(is_kde("KDE:plasma"));
        assert!(is_kde("X-Cinnamon:KDE"));
        assert!(!is_kde("GNOME"));
        assert!(!is_kde(""));
    }
}
