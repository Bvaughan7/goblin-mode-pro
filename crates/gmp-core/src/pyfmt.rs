//! Rendering values that arrived from somewhere this process does not control.
//!
//! Two callers need it. [`crate::applied`] reads `applied.json`, which a person
//! may have hand-edited and which a previous version may have written
//! differently. The CLI reads the daemon's replies across a *frozen* interface,
//! whose whole premise is that the process on the other end may be a different
//! build - that is what the `Version` and `InterfaceVersion` properties are for.
//!
//! Both therefore hold JSON whose shape is expected rather than guaranteed, and
//! both are reporting paths: the useful thing to do with a field of the wrong
//! type is to say what is there, not to fail. One shared definition is the
//! point - the same record rendered by the daemon, the CLI and this crate
//! should read identically.
//!
//! Mirrors `src/goblinmode/textfmt.py` function for function.

use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

use crate::config::truthy;
use crate::round::py_str;

/// A record that is supposed to be a mapping, when it may not be.
///
/// Callers index into these, and a value of the wrong type turns that into an
/// error out of a reporting path. An empty mapping renders as the absences it
/// is. Mirrors `textfmt.fields`.
pub fn fields(value: Option<&Value>) -> &serde_json::Map<String, Value> {
    static EMPTY: std::sync::LazyLock<serde_json::Map<String, Value>> =
        std::sync::LazyLock::new(serde_json::Map::new);
    match value {
        Some(Value::Object(map)) => map,
        _ => &EMPTY,
    }
}

/// The entries of a field meant to hold a list of names.
///
/// A scalar reads as a single entry rather than as nothing, so a caller that
/// has already decided "this field holds something" goes on to say what.
pub fn names(value: &Value) -> Vec<String> {
    if !truthy(value) {
        return Vec::new();
    }
    match value {
        // Python iterates a string character by character, so a bare string
        // has to be caught before the sequence arm or "Wow" becomes three
        // entries. Both implementations read it as one name.
        Value::String(s) => vec![s.clone()],
        Value::Array(items) => items.iter().map(scalar).collect(),
        // A mapping iterates its keys, which is what `reniced` relies on: it
        // is stored as pid -> nice value and the pids are what get listed.
        Value::Object(map) => map.keys().cloned().collect(),
        other => vec![scalar(other)],
    }
}

/// One entry as display text.
pub fn scalar(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        // Python capitalises these, and two implementations rendering the same
        // record should not differ over the word "true".
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => i.to_string(),
            (_, Some(u)) => u.to_string(),
            _ => py_str(n.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(s) => s.clone(),
        // A container renders as its own entries rather than as Python's repr,
        // which would put brackets and quotes in front of somebody reading a
        // bug report. Nothing should ever nest here; the point is that a
        // record which does still reads as something.
        other => name(other),
    }
}

/// One field as a single piece of display text.
pub fn name(value: &Value) -> String {
    names(value).join(", ")
}

/// One field as display text, with a stand-in for nothing.
///
/// A null takes the default rather than rendering as the word "None": a
/// missing key and a key explicitly set to null mean the same thing to a
/// reader, and only one of those spellings is worth showing them.
pub fn text(value: Option<&Value>, default: &str) -> String {
    match value {
        None | Some(Value::Null) => default.to_string(),
        // An empty string is a value, not an absence: Python's `.get(k, d)`
        // hands back the empty string it found and only substitutes `d` when
        // the key is not there at all.
        Some(other) => scalar(other),
    }
}

/// A field meant to hold a number, or `None` when it does not.
///
/// Bools are refused on purpose. They are integers in Python, so a field that
/// should hold a frame rate and holds `true` would otherwise format as 1.
pub fn number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

/// `str.isalnum` for one character, which is NOT `char::is_alphanumeric`.
///
/// Rust follows the derived Alphabetic property, which takes in
/// `Other_Alphabetic` - combining marks such as U+0345 and the U+0363..036F
/// block. Python asks only for a general category of `L*` or `N*`, and over
/// the printable characters below U+3000 alone the two disagree about 771 of
/// them.
///
/// Python's regex `\w` is this plus the underscore, which is what the
/// documentation says and what a probe confirms, so a port of a `\w` pattern
/// wants this predicate too.
///
/// Spelled as a pattern because that is what the rule IS: `isalnum` is
/// `isalpha` (the `L` categories) or `isnumeric` (the `N` ones), and
/// `isdecimal` and `isdigit` are subsets of the second.
pub fn is_alnum(c: char) -> bool {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[\p{L}\p{N}]$").expect("a valid pattern"))
        .is_match(c.encode_utf8(&mut [0u8; 4]))
}

/// Python's regex `\w`: [`is_alnum`] or an underscore.
pub fn is_word_char(c: char) -> bool {
    is_alnum(c) || c == '_'
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_string_is_one_name_not_its_characters() {
        assert_eq!(names(&serde_json::json!("Wow")), vec!["Wow"]);
    }

    #[test]
    fn a_mapping_lists_its_keys() {
        assert_eq!(
            names(&serde_json::json!({"123": -5, "456": -5})),
            vec!["123", "456"]
        );
    }

    #[test]
    fn scalars_render_the_way_python_renders_them() {
        assert_eq!(scalar(&serde_json::json!(5)), "5");
        assert_eq!(scalar(&serde_json::json!(5.0)), "5.0");
        assert_eq!(scalar(&serde_json::json!(true)), "True");
        assert_eq!(scalar(&serde_json::json!(null)), "None");
    }

    #[test]
    fn a_falsy_field_names_nothing() {
        for raw in ["null", "false", "0", "\"\"", "[]", "{}"] {
            let value: Value = serde_json::from_str(raw).unwrap();
            assert!(names(&value).is_empty(), "{raw}");
        }
    }

    #[test]
    fn a_missing_or_null_field_takes_the_default() {
        assert_eq!(text(None, "?"), "?");
        assert_eq!(text(Some(&serde_json::json!(null)), "?"), "?");
        // An empty string is a value the reply carried, not a gap in it.
        assert_eq!(text(Some(&serde_json::json!("")), "?"), "");
        assert_eq!(text(Some(&serde_json::json!("i7")), "?"), "i7");
    }

    #[test]
    fn a_bool_is_not_a_number() {
        // `isinstance(True, int)` is true in Python, so this has to be said
        // out loud in both languages or a frame rate of `true` formats as 1.
        assert_eq!(number(Some(&serde_json::json!(true))), None);
        assert_eq!(number(Some(&serde_json::json!(60))), Some(60.0));
        assert_eq!(number(Some(&serde_json::json!(59.5))), Some(59.5));
        assert_eq!(number(Some(&serde_json::json!("60"))), None);
        assert_eq!(number(None), None);
    }
}
