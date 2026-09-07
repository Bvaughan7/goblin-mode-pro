//! Tailing the Wine/Proton stderr log the launch wrapper tees out.
//!
//! The daemon is not the game's parent, so it cannot read Proton's stderr
//! directly - the wrapper writes it to a file and this follows that file. The
//! following is the part with the traps in it, and none of them announce
//! themselves when they are wrong: a watcher that reads the same bytes twice
//! reports the same fault twice, and one that mishandles a truncation stops
//! reporting anything at all.
//!
//! The file handling stays with the caller. What is here is where to read
//! from, and what the new text means.

use crate::logrules::live_patterns;
use crate::store::splitlines;

/// How many lines of context an incident carries.
pub const CONTEXT_LINES: usize = 12;
/// How much of the tail is kept for the next incident to quote.
pub const RECENT_LINES: usize = 200;
/// The most that is read in one poll, so a huge Proton log cannot stall the
/// daemon's loop.
pub const MAX_READ: u64 = 512 * 1024;

/// Where the next read starts, and whether the first line it finds is a
/// fragment to throw away.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Window {
    pub seek_to: u64,
    /// True when the seek landed mid-line and the first line must be dropped
    /// to get back onto a boundary.
    pub realign: bool,
}

/// Where to read from, given how big the file is and how far we had got.
///
/// Two things happen here that look like edge cases and are not.
///
/// A file SMALLER than the position we held has been truncated or replaced -
/// log rotation, or the wrapper starting a new run - and the answer is to
/// start again from the beginning rather than to seek past the end and read
/// nothing for ever.
///
/// A backlog bigger than the cap is not read in pieces across several polls;
/// the oldest part of it is SKIPPED. That is deliberate: this watcher exists
/// to notice a fault that is happening now, and a daemon spending its poll
/// budget catching up on a megabyte of shader warnings is a daemon that is not
/// watching. The skip lands mid-line, so the first line read is a fragment and
/// is discarded.
pub fn read_window(size: u64, pos: u64, max_read: u64) -> Window {
    // Truncated or replaced: whatever we had read is gone.
    let pos = if size < pos { 0 } else { pos };
    if size - pos > max_read {
        Window {
            seek_to: size - max_read,
            realign: true,
        }
    } else {
        Window {
            seek_to: pos,
            realign: false,
        }
    }
}

/// A critical line, with the lines around it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    pub label: String,
    pub line: String,
    pub context: Vec<String>,
}

/// What one poll's worth of new text means.
#[derive(Debug, Clone, PartialEq)]
pub struct Scan {
    /// The first critical line, if the cooldown allowed one.
    pub hit: Option<Hit>,
    /// The tail, after this text was folded in.
    pub recent: Vec<String>,
    /// When the last hit was reported, unchanged if there was none.
    pub last_hit_at: f64,
}

/// Read new text, remember it, and report at most one fault from it.
///
/// At most ONE, and every line is still remembered. A Proton log that starts
/// failing produces the same line hundreds of times a second, and an incident
/// per line would bury the one that mattered - but the lines still have to
/// reach the tail, because the NEXT incident quotes them as its context.
///
/// The context includes the offending line itself: it is appended before the
/// patterns are tried, so the last of the twelve lines is the hit.
pub fn scan(new_text: &str, recent: &[String], last_hit_at: f64, now: f64, cooldown: f64) -> Scan {
    let mut out = Scan {
        hit: None,
        recent: recent.to_vec(),
        last_hit_at,
    };
    for raw in splitlines(new_text) {
        // `rstrip()`, which takes every kind of trailing whitespace and not
        // just the newline.
        let line = raw.trim_end().to_string();
        out.recent.push(line.clone());
        if out.recent.len() > RECENT_LINES {
            let excess = out.recent.len() - RECENT_LINES;
            out.recent.drain(..excess);
        }
        if out.hit.is_some() || now - out.last_hit_at < cooldown {
            continue;
        }
        for (pattern, label) in live_patterns() {
            if pattern.is_match(&line) {
                out.last_hit_at = now;
                let start = out.recent.len().saturating_sub(CONTEXT_LINES);
                out.hit = Some(Hit {
                    label: label.to_string(),
                    line: line.clone(),
                    context: out.recent[start..].to_vec(),
                });
                break;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scan_fresh(text: &str) -> Scan {
        scan(text, &[], 0.0, 100.0, 30.0)
    }

    #[test]
    fn reading_starts_where_the_last_read_stopped() {
        assert_eq!(
            read_window(5_000, 1_000, MAX_READ),
            Window {
                seek_to: 1_000,
                realign: false
            }
        );
    }

    #[test]
    fn a_file_that_shrank_is_read_from_the_beginning() {
        // Rotated, or the wrapper started a new run. Seeking to the old
        // position would read nothing, for ever.
        assert_eq!(
            read_window(100, 5_000, MAX_READ),
            Window {
                seek_to: 0,
                realign: false
            }
        );
    }

    #[test]
    fn a_backlog_over_the_cap_skips_its_oldest_part() {
        // Not read in pieces across polls: this watcher is for a fault
        // happening now, and catching up on a megabyte of old warnings is
        // time not spent watching.
        assert_eq!(
            read_window(2_000_000, 0, MAX_READ),
            Window {
                seek_to: 2_000_000 - MAX_READ,
                realign: true
            }
        );
    }

    #[test]
    fn a_backlog_exactly_at_the_cap_is_read_whole() {
        assert_eq!(
            read_window(MAX_READ, 0, MAX_READ),
            Window {
                seek_to: 0,
                realign: false
            }
        );
    }

    #[test]
    fn an_unchanged_file_reads_nothing() {
        assert_eq!(
            read_window(1_000, 1_000, MAX_READ),
            Window {
                seek_to: 1_000,
                realign: false
            }
        );
    }

    #[test]
    fn every_line_reaches_the_tail() {
        let out = scan_fresh("one\ntwo\nthree\n");
        assert_eq!(out.recent, vec!["one", "two", "three"]);
        assert!(out.hit.is_none());
    }

    #[test]
    fn trailing_whitespace_is_stripped_and_not_only_the_newline() {
        let out = scan_fresh("one   \t\ntwo\n");
        assert_eq!(out.recent, vec!["one", "two"]);
    }

    #[test]
    fn the_tail_is_bounded() {
        let text: String = (0..300).map(|i| format!("line{i}\n")).collect();
        let out = scan_fresh(&text);
        assert_eq!(out.recent.len(), RECENT_LINES);
        assert_eq!(out.recent[0], "line100", "the oldest go, not the newest");
        assert_eq!(out.recent[RECENT_LINES - 1], "line299");
    }

    #[test]
    fn the_tail_survives_across_polls() {
        let first = scan_fresh("one\n");
        let second = scan("two\n", &first.recent, first.last_hit_at, 200.0, 30.0);
        assert_eq!(second.recent, vec!["one", "two"]);
    }
}
