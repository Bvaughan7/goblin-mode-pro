//! `json.dumps` as CPython writes it, which is not what `serde_json` writes.
//!
//! Two of these documents are read by a person or diffed against the other
//! implementation - the payload a user pastes into a model, the applied-state
//! file a cold `--revert` reads - so "parses to the same thing" is not the bar.
//! The bytes are the bar.
//!
//! The difference that matters is `ensure_ascii`, which is CPython's DEFAULT.
//! Every character above U+007F is escaped to `\uXXXX`, in lowercase hex, and
//! anything above U+FFFF is written as a UTF-16 SURROGATE PAIR. `serde_json`
//! emits all of it as UTF-8. So the same incident renders differently the
//! moment a game is called Pokemon with an accent, a path has one in it, or a
//! line of Proton output carries a trademark sign.
//!
//! U+007F is the other one to know: DEL is not a control character by
//! `serde_json`'s reckoning and is left alone there, and CPython escapes it
//! along with everything else outside printable ASCII.

use serde_json::Value;

/// `json.dumps(value, indent=n)`.
pub fn dumps_indented(value: &Value, indent: usize) -> String {
    let mut out = String::new();
    write_value(&mut out, value, indent, 0);
    out
}

/// `json.dumps(value)` - one line, and note the separators: CPython's default
/// puts a SPACE after the comma when there is no indent, and none when there
/// is. A renderer that used one set for both would be wrong for one of them.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, 0, 0);
    out
}

fn write_value(out: &mut String, value: &Value, indent: usize, depth: usize) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => out.push_str(&number(n)),
        Value::String(s) => write_string(out, s),
        Value::Array(rows) => {
            if rows.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, row) in rows.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if indent == 0 {
                        out.push(' ');
                    }
                }
                newline(out, indent, depth + 1);
                write_value(out, row, indent, depth + 1);
            }
            newline(out, indent, depth);
            out.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if indent == 0 {
                        out.push(' ');
                    }
                }
                newline(out, indent, depth + 1);
                write_string(out, key);
                out.push_str(": ");
                write_value(out, item, indent, depth + 1);
            }
            newline(out, indent, depth);
            out.push('}');
        }
    }
}

fn newline(out: &mut String, indent: usize, depth: usize) {
    if indent == 0 {
        return;
    }
    out.push('\n');
    for _ in 0..indent * depth {
        out.push(' ');
    }
}

/// A number as Python prints it: an integer has no point, and a float goes
/// through `repr`, which is the shortest string that reads back as the same
/// double.
fn number(n: &serde_json::Number) -> String {
    match n.as_f64() {
        Some(f) if n.is_f64() => crate::round::py_str(f),
        _ => n.to_string(),
    }
}

fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            // Printable ASCII, which is everything CPython leaves alone.
            ' '..='~' => out.push(c),
            _ => {
                let code = c as u32;
                if code > 0xFFFF {
                    // Outside the basic plane, CPython writes the UTF-16
                    // surrogate pair rather than one six-digit escape.
                    let offset = code - 0x1_0000;
                    let high = 0xD800 + (offset >> 10);
                    let low = 0xDC00 + (offset & 0x3FF);
                    out.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
                } else {
                    out.push_str(&format!("\\u{code:04x}"));
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(value: &Value) -> String {
        dumps_indented(value, 2)
    }

    fn quoted(inner: &str) -> String {
        format!("\"{inner}\"")
    }

    #[test]
    fn non_ascii_is_escaped_rather_than_written_out() {
        assert_eq!(
            s(&Value::String("Pok\u{e9}mon".into())),
            quoted("Pok\\u00e9mon")
        );
        assert_eq!(s(&Value::String("\u{2122}".into())), quoted("\\u2122"));
    }

    #[test]
    fn a_character_outside_the_basic_plane_is_a_surrogate_pair() {
        assert_eq!(
            s(&Value::String("\u{1f525}".into())),
            quoted("\\ud83d\\udd25")
        );
    }

    #[test]
    fn del_is_escaped_where_serde_leaves_it_alone() {
        assert_eq!(s(&Value::String("\u{7f}".into())), quoted("\\u007f"));
    }

    #[test]
    fn the_shortcut_escapes_are_the_five_python_uses() {
        assert_eq!(
            s(&Value::String("\u{8}\u{c}\n\r\t".into())),
            quoted("\\b\\f\\n\\r\\t")
        );
    }

    #[test]
    fn other_control_characters_are_lowercase_hex() {
        assert_eq!(
            s(&Value::String("\u{0}\u{1b}\u{1f}".into())),
            quoted("\\u0000\\u001b\\u001f")
        );
    }

    #[test]
    fn a_slash_is_not_escaped() {
        assert_eq!(s(&Value::String("/home/x".into())), quoted("/home/x"));
    }

    #[test]
    fn an_empty_container_stays_on_one_line() {
        assert_eq!(
            s(&serde_json::json!({"a": {}, "b": []})),
            "{\n  \"a\": {},\n  \"b\": []\n}"
        );
    }

    #[test]
    fn without_an_indent_a_comma_is_followed_by_a_space() {
        // CPython's default separators, which differ between the two modes.
        assert_eq!(dumps(&serde_json::json!([1, 2])), "[1, 2]");
        assert_eq!(
            dumps(&serde_json::json!({"a": 1, "b": 2})),
            "{\"a\": 1, \"b\": 2}"
        );
    }

    #[test]
    fn with_an_indent_a_comma_is_followed_by_a_newline() {
        assert_eq!(s(&serde_json::json!([1, 2])), "[\n  1,\n  2\n]");
    }

    #[test]
    fn an_integer_keeps_no_decimal_point_and_a_float_keeps_one() {
        assert_eq!(
            dumps(&serde_json::json!({"i": 3, "f": 3.0})),
            "{\"i\": 3, \"f\": 3.0}"
        );
    }
}
