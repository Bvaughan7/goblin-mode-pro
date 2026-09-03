"""Rendering values that arrived from somewhere this process does not control.

Two places need it. ``payload`` reads ``applied.json``, which a person may have
hand-edited and which a previous version may have written differently. ``cli``
reads the daemon's replies across a *frozen* interface, whose whole premise is
that the process on the other end may be a different build - that is what the
``Version`` and ``InterfaceVersion`` properties are for.

Both therefore hold JSON whose shape is expected rather than guaranteed, and
both are reporting paths: the useful thing to do with a field of the wrong type
is to say what is there, not to raise. Everything below is written to that rule,
and the one shared definition is the point - the same file rendered by the
daemon, the CLI and a future Rust build should read identically.
"""

from __future__ import annotations

_CONTAINERS = (list, tuple, set, dict)


def names(value) -> list[str]:
    """The entries of a field meant to hold a list of names.

    A scalar reads as a single entry rather than as nothing, so that a caller
    which has already decided "this field holds something" goes on to say what.
    """
    if not value:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    try:
        return [scalar(item) for item in value]
    except TypeError:
        # Not iterable at all - a number or a bool. One entry, not none.
        return [str(value)]


def scalar(value) -> str:
    """One entry as display text.

    A container renders as its own entries rather than as Python's repr, which
    would put brackets and quotes in front of somebody reading a bug report.
    Nothing should ever nest here; the point is that a file or a reply which
    does still reads as something.
    """
    if isinstance(value, _CONTAINERS):
        return name(value)
    return str(value)


def name(value) -> str:
    """One field as a single piece of display text."""
    return ", ".join(names(value))


def text(value, default: str = "?") -> str:
    """One field as display text, with a stand-in for nothing.

    ``None`` takes the default rather than rendering as the word "None". A
    missing key and a key explicitly set to null mean the same thing to a
    reader, and only one of the two spellings is worth showing them.
    """
    if value is None:
        return default
    return scalar(value)


def number(value):
    """A field meant to hold a number, or None when it does not.

    Bools are refused on purpose. They are integers in Python, so a field that
    should hold a frame rate and holds ``true`` would otherwise format as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value
