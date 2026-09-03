//! Reading what the Python wrote.
//!
//! On-disk compatibility is the one thing the daemon port has no room to get
//! wrong: there is no migration step, and a user upgrading must not lose a
//! per-game profile or a session history. So the formats are parsed here,
//! from text, and diffed against the Python line for line.
//!
//! Only the parsing. Opening the file belongs to whoever has a filesystem;
//! everything below takes the contents as a string, which is what keeps it
//! testable from fixtures and comparable against the other implementation.
//!
//! Mirrors the readers in `config.py`, `sessions.py` and `incidents.py`.

use serde_json::Value;

use crate::config::{self, Settings};

/// Python's `str.splitlines`, which is not Rust's `str::lines`.
///
/// `lines` splits on `\n` and tolerates a preceding `\r`. Python also breaks on
/// a lone `\r`, on the vertical tab, form feed and file/group/record
/// separators, on NEL, and on the Unicode line and paragraph separators. A
/// record containing any of those raw would be split into two unparseable
/// halves by one implementation and kept whole by the other - the record would
/// simply vanish from one side's history.
///
/// Nothing this program writes can contain them: `json.dumps` escapes every
/// control character and every non-ASCII one. A hand-edited file is a
/// different matter, and the whole point of these readers is surviving those.
pub fn splitlines(text: &str) -> Vec<&str> {
    const BREAKS: &[char] = &[
        '\r',       // carriage return, alone or before a newline
        '\u{000b}', // vertical tab
        '\u{000c}', // form feed
        '\u{001c}', // file separator
        '\u{001d}', // group separator
        '\u{001e}', // record separator
        '\u{0085}', // next line
        '\u{2028}', // line separator
    ];

    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, ch)) = chars.next() {
        let is_break = ch == '\n' || ch == '\u{2029}' || BREAKS.contains(&ch);
        if !is_break {
            continue;
        }
        lines.push(&text[start..index]);
        // `\r\n` is ONE break, not two, so a Windows-written file does not
        // gain an empty record between every pair of real ones.
        if ch == '\r' && chars.peek().map(|(_, next)| *next) == Some('\n') {
            chars.next();
        }
        start = chars.peek().map_or(text.len(), |(next, _)| *next);
    }
    // A trailing break ends the last line rather than starting an empty one.
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// The JSON objects in a JSONL file.
///
/// A line that will not parse is skipped, which is what both Python readers
/// do. A line that parses into something that is NOT an object is skipped too,
/// which is what they should have done: they appended it, and the caller then
/// reached `.get` on a number and raised - out of the session history the CLI
/// and GUI both list, and out of the incident export.
pub fn parse_jsonl(text: &str) -> Vec<Value> {
    splitlines(text)
        .into_iter()
        .filter_map(|line| match serde_json::from_str::<Value>(line) {
            Ok(Value::Object(map)) => Some(Value::Object(map)),
            _ => None,
        })
        .collect()
}

/// Where `xs[k:]` starts, for a `k` that may be negative or out of range.
fn slice_start(index: i64, length: usize) -> usize {
    if index < 0 {
        (length as i64 + index).max(0) as usize
    } else {
        (index as usize).min(length)
    }
}

/// `xs[-limit:]`, with Python's surprises intact: a limit of zero is the WHOLE
/// list rather than none of it, and a negative limit drops entries off the
/// front.
fn tail<T: Clone>(items: &[T], limit: i64) -> Vec<T> {
    items[slice_start(-limit, items.len())..].to_vec()
}

/// The session history, optionally for one executable.
///
/// The whole file is parsed, then filtered, and only then trimmed - so asking
/// for one game's last forty sessions really does give forty of them if they
/// exist, however many other games are interleaved.
pub fn sessions_history(text: &str, exe: Option<&str>, limit: i64) -> Vec<Value> {
    let rows = parse_jsonl(text);
    let filtered: Vec<Value> = match exe {
        Some(exe) => rows
            .into_iter()
            .filter(|row| row.get("exe").and_then(Value::as_str) == Some(exe))
            .collect(),
        None => rows,
    };
    tail(&filtered, limit)
}

/// The incident history.
///
/// The trim happens on the LINES, before parsing - deliberately different from
/// the session reader above, and reproduced rather than harmonised. A file
/// whose last hundred lines include unparseable ones yields fewer than a
/// hundred incidents, because the trim has already spent those slots. The two
/// readers look alike and are not, and quietly making them agree would change
/// what an existing installation reports.
pub fn incidents_history(text: &str, limit: i64) -> Vec<Value> {
    let lines = splitlines(text);
    tail(&lines, limit)
        .into_iter()
        .filter_map(|line| match serde_json::from_str::<Value>(line) {
            Ok(Value::Object(map)) => Some(Value::Object(map)),
            _ => None,
        })
        .collect()
}

/// The settings, from the text of `config.json`.
///
/// A file that will not parse, or that parses into something other than an
/// object, gives the defaults. `None` means the file is not there at all,
/// which the caller treats differently: the Python writes the defaults out so
/// that a fresh install has a file to edit.
pub fn settings_from_text(text: &str) -> Settings {
    match serde_json::from_str::<Value>(text) {
        Ok(value) => config::from_value(&value),
        Err(_) => config::default_settings(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn splitlines_breaks_where_python_breaks() {
        assert_eq!(splitlines("a\nb"), vec!["a", "b"]);
        assert_eq!(splitlines("a\r\nb"), vec!["a", "b"], "CRLF is one break");
        assert_eq!(splitlines("a\rb"), vec!["a", "b"], "a lone CR breaks");
        assert_eq!(splitlines("a\u{000b}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{000c}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{001c}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{001d}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{001e}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{0085}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{2028}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{2029}b"), vec!["a", "b"]);
    }

    #[test]
    fn a_trailing_break_does_not_add_an_empty_line() {
        assert_eq!(splitlines("a\n"), vec!["a"]);
        assert_eq!(splitlines("a\r\n"), vec!["a"]);
        assert_eq!(splitlines(""), Vec::<&str>::new());
        assert_eq!(splitlines("\n"), vec![""]);
        assert_eq!(splitlines("a\n\nb"), vec!["a", "", "b"]);
    }

    #[test]
    fn a_line_that_is_not_an_object_is_not_a_record() {
        // Both Python readers appended these, and the caller then reached
        // `.get` on a number - out of the session history the CLI and the GUI
        // both list, and out of the incident export.
        let text = "{\"exe\":\"a\"}\n5\n\"hello\"\n[1,2]\nnull\ntrue\n{\"exe\":\"b\"}";
        let rows = parse_jsonl(text);
        assert_eq!(rows.len(), 2);
        assert!(rows.iter().all(Value::is_object));
    }

    #[test]
    fn an_unparseable_line_is_skipped_and_the_rest_survive() {
        let text = "{\"exe\":\"a\"}\n{\"exe\":\n{\"exe\":\"b\"}";
        assert_eq!(parse_jsonl(text).len(), 2);
    }

    #[test]
    fn the_session_history_filters_before_it_trims() {
        // Forty of THIS game's sessions, however many others are interleaved.
        let mut text = String::new();
        for i in 0..50 {
            text.push_str(&format!("{{\"exe\":\"a\",\"i\":{i}}}\n"));
            text.push_str(&format!("{{\"exe\":\"b\",\"i\":{i}}}\n"));
        }
        let rows = sessions_history(&text, Some("a"), 40);
        assert_eq!(rows.len(), 40);
        assert!(rows.iter().all(|r| r["exe"] == json!("a")));
        assert_eq!(rows[39]["i"], json!(49));
    }

    #[test]
    fn the_incident_history_trims_before_it_parses() {
        // The opposite order, and the difference is observable: an unparseable
        // line inside the window has already spent one of the slots.
        let text = "{\"a\":1}\nnot json\n{\"a\":2}\n{\"a\":3}";
        assert_eq!(incidents_history(text, 3).len(), 2);
        assert_eq!(
            sessions_history("{\"a\":1}\nnot json\n{\"a\":2}\n{\"a\":3}", None, 3).len(),
            3
        );
    }

    #[test]
    fn a_limit_of_zero_is_everything_and_a_negative_one_drops_the_front() {
        let text = "{\"i\":0}\n{\"i\":1}\n{\"i\":2}";
        assert_eq!(sessions_history(text, None, 0).len(), 3);
        assert_eq!(sessions_history(text, None, -1).len(), 2);
        assert_eq!(sessions_history(text, None, -99).len(), 0);
        assert_eq!(incidents_history(text, 0).len(), 3);
    }

    #[test]
    fn filtering_on_an_exe_compares_strings_only() {
        // A row whose `exe` is a number is not a match for the string "5".
        let text = "{\"exe\":5}\n{\"exe\":\"5\"}";
        assert_eq!(sessions_history(text, Some("5"), 40).len(), 1);
    }

    #[test]
    fn an_unusable_config_gives_the_defaults_rather_than_nothing() {
        for text in ["", "not json", "[1,2]", "5", "null", "\"x\""] {
            let settings = settings_from_text(text);
            assert_eq!(
                settings.profiles.len(),
                config::default_settings().profiles.len(),
                "{text:?}"
            );
        }
    }
}
