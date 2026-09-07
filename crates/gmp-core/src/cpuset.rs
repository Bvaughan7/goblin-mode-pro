//! CPU affinity ("core pinning") for a game's process tree.
//!
//! The half that decides WHICH cpus a pinning mode means. Reading the process
//! tree and moving its threads stays in Python for now; this is the part that
//! can be wrong without failing, which is the part worth diffing. Pin a game
//! to the wrong half of a hybrid CPU and it runs on the efficiency cores with
//! no error anywhere.

use serde_json::Value;

/// The cpu list a pinning mode means on this machine, or `None` when the mode
/// does not apply here.
///
/// Three answers, not two, and the third is easy to lose. `None` means "this
/// machine has nothing to pin to" - every core the same, or one cache group -
/// and an EMPTY LIST means the layout named a group and the group is empty.
/// The caller treats both as "do not pin", but they are different claims and
/// the Python distinguishes them, so this does too.
///
/// `performance` is guarded on the list being non-empty and `cache0` is not:
/// the Python checks the outer list of groups and then indexes the first one
/// whatever is in it. Reproduced rather than tidied, because the two are only
/// the same until a layout turns up with an empty first group.
pub fn target_cpus(mode: &str, layout: &Value) -> Option<Vec<i64>> {
    match mode {
        "performance" => {
            // A null field needs no guard of its own: it is not an array, and
            // `numbers` answers nothing for anything that is not one.
            let cpus = numbers(layout.get("performance")?)?;
            // `if cpus:` - an empty list is no answer rather than an empty one.
            if cpus.is_empty() {
                return None;
            }
            Some(cpus)
        }
        "cache0" => {
            let groups = layout.get("cache_groups")?.as_array()?;
            // `if groups:` tests the OUTER list; the first group is then taken
            // whatever it holds, empty included.
            numbers(groups.first()?)
        }
        // "off", and anything a profile spells that this build does not know.
        _ => None,
    }
}

/// A field meant to hold cpu numbers, or nothing if it is not one.
///
/// ALL of it or none of it, and that is the point. The Python does not
/// sanitise at all - it calls `list()` on whatever is there, which raises for
/// a number and yields characters for a string, and either way `sched_setaffinity`
/// refuses the result and the launch goes on unpinned. Dropping the entries
/// that are not numbers would be the one answer neither implementation gives:
/// a game pinned to a SUBSET of the cores the layout named, silently, because
/// a probe answered oddly.
fn numbers(value: &Value) -> Option<Vec<i64>> {
    let rows = value.as_array()?;
    rows.iter().map(Value::as_i64).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layout(raw: &str) -> Value {
        serde_json::from_str(raw).unwrap()
    }

    #[test]
    fn performance_is_the_fast_cores_a_hybrid_cpu_reports() {
        assert_eq!(
            target_cpus("performance", &layout(r#"{"performance": [0, 1, 2, 3]}"#)),
            Some(vec![0, 1, 2, 3])
        );
    }

    #[test]
    fn a_cpu_with_no_fast_half_has_nothing_to_pin_to() {
        // Every core the same. `_core_layout` omits the key entirely, and an
        // empty list means the same thing.
        assert_eq!(target_cpus("performance", &layout(r#"{}"#)), None);
        assert_eq!(
            target_cpus("performance", &layout(r#"{"performance": []}"#)),
            None
        );
        assert_eq!(
            target_cpus("performance", &layout(r#"{"performance": null}"#)),
            None
        );
    }

    #[test]
    fn cache0_is_the_first_group_of_cores_that_share_a_cache() {
        // One CCD on a chiplet Ryzen, which is what keeps a game off the
        // cross-CCD latency penalty.
        assert_eq!(
            target_cpus(
                "cache0",
                &layout(r#"{"cache_groups": [[0, 1, 2], [3, 4, 5]]}"#)
            ),
            Some(vec![0, 1, 2])
        );
    }

    #[test]
    fn one_cache_group_is_no_group_to_choose_between() {
        // `_core_layout` omits the key when there is only one.
        assert_eq!(target_cpus("cache0", &layout(r#"{}"#)), None);
        assert_eq!(
            target_cpus("cache0", &layout(r#"{"cache_groups": []}"#)),
            None
        );
    }

    #[test]
    fn an_empty_first_cache_group_is_an_empty_answer_not_no_answer() {
        // The one place the two modes differ: `performance` is guarded on the
        // list being non-empty and this is guarded on the outer list only.
        assert_eq!(
            target_cpus("cache0", &layout(r#"{"cache_groups": [[]]}"#)),
            Some(vec![])
        );
    }

    #[test]
    fn off_pins_nothing() {
        let full = layout(r#"{"performance": [0, 1], "cache_groups": [[0, 1]]}"#);
        assert_eq!(target_cpus("off", &full), None);
        assert_eq!(target_cpus("", &full), None);
        assert_eq!(target_cpus("cache1", &full), None);
    }

    #[test]
    fn a_layout_that_answers_oddly_costs_the_pinning_and_not_the_launch() {
        // Probed from sysfs. Every one of these is "do not pin" on both
        // sides: the Python raises or produces something `sched_setaffinity`
        // refuses, and its caller swallows either.
        for (mode, raw) in [
            ("performance", r#"{"performance": 3}"#),
            ("performance", r#"{"performance": [0, "x", 2]}"#),
            ("performance", r#"[]"#),
            ("cache0", r#"{"cache_groups": {}}"#),
            ("cache0", r#"{"cache_groups": [1, 2, 3]}"#),
            ("cache0", r#"{"cache_groups": "no"}"#),
        ] {
            assert_eq!(target_cpus(mode, &layout(raw)), None, "{raw}");
        }
    }

    #[test]
    fn a_partial_cpu_list_is_never_the_answer() {
        // The tempting reading of the above, and the one answer neither
        // implementation gives. A game pinned to SOME of the cores the layout
        // named, because a probe answered oddly, would be slower than not
        // pinning it and would say nothing about why.
        assert_eq!(
            target_cpus("performance", &layout(r#"{"performance": [0, "x", 2]}"#)),
            None
        );
    }
}
