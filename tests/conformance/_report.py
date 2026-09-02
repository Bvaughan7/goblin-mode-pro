"""Shared scaffolding for the conformance suites.

There are two of them - `helper.py` grades whatever serves the privileged
helper's interface, `daemon.py` grades whatever serves the daemon's - and they
report the same way on purpose: one format to learn, and one place to change it.

The alternative was a second copy of the renderer. This project already decided
that question once, about the D-Bus canonicalizer: two implementations of the
same rendering drift, and when they do, the check that depends on them starts
passing for the wrong reason. Same reasoning, much smaller stakes.

What deliberately does NOT live here is anything either suite knows about what
it is grading. Both drive their target from outside and never import its
source; this module holds presentation and bus-error decoding, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"

_COLOUR = {PASS: "\033[32m", FAIL: "\033[31m", SKIP: "\033[33m", INFO: "\033[36m"}


@dataclass
class Result:
    name: str
    title: str
    status: str
    detail: str
    section: str = "General"
    observed: dict = field(default_factory=dict)


def dbus_error_name(exc: GLib.Error) -> str:
    """The remote error name, e.g. com.goblinmode.ProHelper.Manager.Failed."""
    return Gio.dbus_error_get_remote_error(exc) or ""


def dbus_error_message(exc: GLib.Error) -> str:
    """The message with the remote error name stripped off the front."""
    stripped = GLib.Error.copy(exc)
    Gio.dbus_error_strip_remote_error(stripped)
    return stripped.message


def counts(results: list[Result]) -> dict[str, int]:
    return {status: sum(1 for r in results if r.status == status)
            for status in (PASS, FAIL, SKIP, INFO)}


def render(results: list[Result], use_colour: bool, footer: list[str] | None = None) -> str:
    """The shared report body: sections, rows, tallies, then any footer lines.

    `footer` is where each suite says what its own SKIPs need - the helper's
    complementary root/unprivileged runs, the daemon's game-state caveats.
    """
    out: list[str] = []
    sections: dict[str, list[Result]] = {}
    for r in results:
        sections.setdefault(r.section, []).append(r)
    for section, rows in sections.items():
        out.append(f"\n{section}")
        out.append("-" * len(section))
        for r in rows:
            tag = f"{_COLOUR.get(r.status, '')}{r.status}\033[0m" if use_colour else r.status
            out.append(f"  {tag}  {r.title}")
            for line in r.detail.splitlines():
                out.append(f"        {line}")
    tally = counts(results)
    out.append("")
    out.append(f"{tally[PASS]} PASS  {tally[FAIL]} FAIL  "
               f"{tally[SKIP]} SKIP  {tally[INFO]} INFO")
    if tally[SKIP]:
        out.append("A SKIP is not a pass. Each one above says what is missing.")
    out.extend(footer or [])
    return "\n".join(out)
