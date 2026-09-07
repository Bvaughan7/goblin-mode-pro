//! Keeping the log directories from growing without bound.
//!
//! The `goblin-run` wrapper writes a stderr log per launch and MangoHud writes
//! a CSV per session, and nothing else prunes either - a heavy user quietly
//! accumulates gigabytes. This is the deciding half: which files go. The
//! walking and the unlinking stay with the caller, because a function that
//! answers "these" can be checked against the Python from a list, and one that
//! deletes can only be checked by letting it.

/// One file the pruner is choosing between.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub name: String,
    /// Modification time. Only the ORDER matters.
    pub mtime: f64,
    pub size: i64,
}

/// How many files survive whatever their size.
pub const KEEP_NEWEST: usize = 40;
/// How much survives whatever the count.
pub const MAX_BYTES: i64 = 500 * 1024 * 1024;

/// Which files to delete, newest kept first.
///
/// Two ceilings, and a file goes when it crosses EITHER. The running total
/// includes the file being judged, which is the part worth stating: a single
/// file bigger than the whole budget is deleted even though it is the newest
/// one there is. That is deliberate - the budget is what the directory is
/// allowed to cost, not what its oldest files are allowed to cost - and it is
/// the case a rewrite gets wrong by testing the total BEFORE adding.
///
/// The comparison is `<=`, so a directory landing exactly on the budget is
/// left alone.
///
/// Once either ceiling is crossed everything older goes with it: the count
/// only rises and the total never falls, so the survivors are always a prefix
/// of the newest-first order. A caller that stopped at the first survivor
/// after a deletion would be reading this correctly.
pub fn to_delete(files: &[Entry], keep_newest: usize, max_bytes: i64) -> Vec<String> {
    let mut newest_first: Vec<&Entry> = files.iter().collect();
    // Stable, like Python's `sorted(..., reverse=True)`: files sharing an
    // mtime keep the order they were listed in rather than being reversed.
    newest_first.sort_by(|a, b| b.mtime.total_cmp(&a.mtime));

    let mut running: i64 = 0;
    let mut doomed = Vec::new();
    for (i, entry) in newest_first.iter().enumerate() {
        running += entry.size;
        if i < keep_newest && running <= max_bytes {
            continue;
        }
        doomed.push(entry.name.clone());
    }
    doomed
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(name: &str, mtime: f64, size: i64) -> Entry {
        Entry {
            name: name.to_string(),
            mtime,
            size,
        }
    }

    /// `n` files, newest first by name: `f0` is newest.
    fn many(n: usize, size: i64) -> Vec<Entry> {
        (0..n)
            .map(|i| entry(&format!("f{i}"), (n - i) as f64, size))
            .collect()
    }

    #[test]
    fn a_directory_inside_both_ceilings_loses_nothing() {
        assert_eq!(to_delete(&many(5, 10), 40, 1000), Vec::<String>::new());
    }

    #[test]
    fn the_oldest_go_first_when_there_are_too_many() {
        let doomed = to_delete(&many(5, 1), 3, 1000);
        assert_eq!(doomed, vec!["f3", "f4"]);
    }

    #[test]
    fn the_oldest_go_first_when_there_is_too_much() {
        // 100 bytes each, 250 allowed: two survive, the rest do not.
        assert_eq!(to_delete(&many(5, 100), 40, 250), vec!["f2", "f3", "f4"]);
    }

    #[test]
    fn landing_exactly_on_the_budget_is_inside_it() {
        assert_eq!(to_delete(&many(5, 100), 40, 500), Vec::<String>::new());
        assert_eq!(to_delete(&many(5, 100), 40, 499), vec!["f4"]);
    }

    #[test]
    fn a_single_file_over_the_whole_budget_goes_even_though_it_is_newest() {
        // The total includes the file being judged. Testing the total BEFORE
        // adding would keep this one for ever and prune the small old files
        // around it instead.
        let files = vec![entry("huge", 2.0, 10_000), entry("small", 1.0, 1)];
        assert_eq!(to_delete(&files, 40, 5_000), vec!["huge", "small"]);
    }

    #[test]
    fn everything_older_than_the_first_casualty_goes_too() {
        // Not "delete the ones that do not fit": the survivors are a prefix.
        // A tiny file behind a huge one is not rescued by being tiny.
        let files = vec![
            entry("new", 3.0, 10),
            entry("huge", 2.0, 10_000),
            entry("tiny", 1.0, 1),
        ];
        assert_eq!(to_delete(&files, 40, 100), vec!["huge", "tiny"]);
    }

    #[test]
    fn an_empty_directory_is_nothing_to_do() {
        assert_eq!(to_delete(&[], 40, 100), Vec::<String>::new());
    }

    #[test]
    fn keeping_none_deletes_everything() {
        assert_eq!(to_delete(&many(3, 1), 0, 1_000_000), vec!["f0", "f1", "f2"]);
    }

    #[test]
    fn files_sharing_a_timestamp_keep_the_order_they_were_listed_in() {
        // Python's sort is stable and `reverse=True` does not reverse ties.
        let files = vec![entry("a", 1.0, 1), entry("b", 1.0, 1), entry("c", 1.0, 1)];
        assert_eq!(to_delete(&files, 1, 1_000), vec!["b", "c"]);
    }
}
