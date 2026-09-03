"""The Rust and Python read the same records out of the same files.

This is the part of the daemon port with no room to be approximately right.
There is no migration step and there is not going to be one: the Rust daemon
opens the same `config.json`, the same `sessions.jsonl` and the same
`incidents.jsonl` that the Python daemon has been writing, and a user
upgrading must not lose a per-game profile or a run of history.

The corpus is mostly damaged files, for the reason the rest of this conversion
keeps running into: these readers already skip a line that will not parse, so
somebody decided long ago that a bad file must not stop the program. They just
did not skip a line that parses into something that is not an object. Those
were appended to the list, and the caller then reached ``.get`` on a number -
out of the session history the CLI and the GUI both list, and out of the
incident export.

Two properties are reproduced rather than tidied up, because changing either
would change what an existing installation reports:

* the two readers trim in **different orders**. The session reader parses
  everything, filters by executable, and trims last, so asking for one game's
  last forty really gives forty. The incident reader trims the *lines* first,
  so an unparseable line inside the window has already spent a slot.
* ``str.splitlines`` breaks on far more than ``\\n``: a lone carriage return,
  the vertical tab, form feed, the three separator controls, NEL, and the
  Unicode line and paragraph separators. Rust's ``lines`` breaks on none of
  those, so a record containing one raw would vanish from one side's history
  and not the other's.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import config, incidents, sessions

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_STORE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "store"
        if candidate.exists():
            return candidate
    return None


def row(exe: str, **over) -> str:
    return json.dumps({"exe": exe, "game": exe.title(), "started": "2026-09-03T09:00",
                       **over})


# A history as the daemon really writes one.
REAL = "\n".join([
    row("Wow.exe", fps_avg=84.3, fps_1low=51.2, samples=900),
    row("rs2client", fps_avg=140.0, fps_1low=96.5, samples=1200),
    row("Wow.exe", fps_avg=79.1, fps_1low=44.0, samples=650, benchmark=True),
]) + "\n"

CONFIG = json.dumps({
    "master_enabled": True,
    "profiles": [{"exe": "Wow.exe", "display_name": "World of Warcraft",
                  "match_mode": "exact", "renice_enabled": True}],
})


def case(jsonl="", exe=None, limit=40, config_text="{}") -> dict:
    return {"jsonl": jsonl, "exe": exe, "limit": limit, "config": config_text}


CASES = {
    # -- what the daemon writes ---------------------------------------------
    "a_real_history": case(REAL, exe="Wow.exe"),
    "a_real_history_unfiltered": case(REAL),
    "a_real_config": case(REAL, config_text=CONFIG),
    "empty_file": case(""),
    "only_a_newline": case("\n"),
    "no_trailing_newline": case(row("a")),
    "blank_lines_between": case(row("a") + "\n\n" + row("b") + "\n"),
    # -- lines that parse into something that is not a record ----------------
    "a_number_line": case(row("a") + "\n5\n" + row("b"), exe="a"),
    "a_string_line": case(row("a") + '\n"hello"\n' + row("b"), exe="a"),
    "a_list_line": case(row("a") + "\n[1,2]\n" + row("b"), exe="a"),
    "a_null_line": case(row("a") + "\nnull\n" + row("b"), exe="a"),
    "a_true_line": case(row("a") + "\ntrue\n" + row("b"), exe="a"),
    "a_float_line": case(row("a") + "\n1.5\n" + row("b"), exe="a"),
    "every_bad_shape": case("\n".join([row("a"), "5", '"s"', "[1]", "null", "true",
                                       "{}", row("b")]), exe="a"),
    # -- lines that do not parse ---------------------------------------------
    "a_truncated_line": case(row("a") + '\n{"exe":\n' + row("b"), exe="a"),
    "a_line_of_junk": case(row("a") + "\nnot json at all\n" + row("b"), exe="a"),
    "only_junk": case("nope\nalso nope"),
    "a_truncated_last_line": case(row("a") + '\n{"exe": "b"'),
    # -- the line separators Python breaks on and Rust does not --------------
    "crlf": case(row("a") + "\r\n" + row("b")),
    "lone_cr": case(row("a") + "\r" + row("b")),
    "mixed_crlf_and_lf": case(row("a") + "\r\n" + row("b") + "\n" + row("c")),
    "vertical_tab": case(row("a") + "" + row("b")),
    "form_feed": case(row("a") + "" + row("b")),
    "file_separator": case(row("a") + "" + row("b")),
    "group_separator": case(row("a") + "" + row("b")),
    "record_separator": case(row("a") + "" + row("b")),
    "next_line": case(row("a") + "" + row("b")),
    "line_separator": case(row("a") + " " + row("b")),
    "paragraph_separator": case(row("a") + " " + row("b")),
    # A record carrying one raw: Python splits it into two unparseable halves
    # and drops it, Rust would keep it whole. Nothing this program writes can
    # contain one, but a hand-edited file can.
    "a_separator_inside_a_record": case(
        '{"exe":"a","note":"before after"}\n' + row("b")),
    "a_cr_inside_a_record": case('{"exe":"a","note":"before\rafter"}\n' + row("b")),
    # -- filtering -------------------------------------------------------------
    "filter_matches_nothing": case(REAL, exe="nosuchgame"),
    "filter_on_empty_string": case(row("") + "\n" + row("a"), exe=""),
    "exe_is_a_number": case('{"exe":5}\n{"exe":"5"}', exe="5"),
    "exe_is_null": case('{"exe":null}\n' + row("a"), exe="a"),
    "exe_is_missing": case('{"game":"x"}\n' + row("a"), exe="a"),
    "exe_is_a_list": case('{"exe":["a"]}\n' + row("a"), exe="a"),
    "unicode_exe": case(row("ゲーム.exe"), exe="ゲーム.exe"),
    # -- the limit, and the two trim orders --------------------------------------
    "limit_one": case(REAL, limit=1),
    "limit_zero_is_everything": case(REAL, limit=0),
    "limit_negative": case(REAL, limit=-1),
    "limit_negative_past_the_end": case(REAL, limit=-99),
    "limit_larger_than_the_file": case(REAL, limit=999),
    # The orders differ: the incident reader has already spent a slot on the
    # unparseable line by the time it parses.
    "junk_inside_the_limit_window": case(
        "\n".join([row("a"), "not json", row("b"), row("c")]), limit=3),
    "junk_outside_the_limit_window": case(
        "\n".join(["not json", row("a"), row("b"), row("c")]), limit=3),
    "many_rows": case("\n".join(row("a", i=i) for i in range(200)), exe="a", limit=40),
    # -- config.json ----------------------------------------------------------------
    "config_empty_object": case(config_text="{}"),
    "config_real": case(config_text=CONFIG),
    "config_unparseable": case(config_text="{not json"),
    "config_is_a_list": case(config_text="[1,2]"),
    "config_is_a_number": case(config_text="5"),
    "config_is_null": case(config_text="null"),
    "config_is_a_string": case(config_text='"hello"'),
    "config_empty_text": case(config_text=""),
    "config_profiles_not_a_list": case(config_text='{"profiles": "nope"}'),
    "config_profile_is_a_string": case(config_text='{"profiles": ["nope"]}'),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the store example is not "
                          "built - run `cargo build -p gmp-core --example store`")
            self.skipTest("build it with `cargo build -p gmp-core --example store`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        # The settings object is compared through the config parity harness
        # already; here only the profile list matters, as the thing a user
        # would actually lose.
        got["settings"] = [p.get("exe") for p in got["settings"]["profiles"]]
        return got

    @staticmethod
    def _python(payload: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text(payload["jsonl"])
            limit = payload["limit"]
            with patch.object(sessions, "SESSION_FILE", path):
                tracker = sessions.SessionTracker.__new__(sessions.SessionTracker)
                everything = sessions.SessionTracker.history(tracker, None, limit)
                filtered = sessions.SessionTracker.history(tracker, payload["exe"], limit)
            with patch.object(incidents, "INCIDENT_FILE", path):
                log = incidents.IncidentLog.__new__(incidents.IncidentLog)
                incident_rows = incidents.IncidentLog.load_history(log, limit)
        settings = _settings_from_text(payload["config"])
        return {
            "lines": payload["jsonl"].splitlines(),
            "records": _records(payload["jsonl"]),
            "sessions_all": everything,
            "sessions_for_exe": filtered,
            "incidents": incident_rows,
            "settings": [p.exe for p in settings.profiles],
        }


def _records(text: str) -> list:
    """`parse_jsonl`, spelled out from the two readers' shared behaviour."""
    out = []
    for line in text.splitlines():
        try:
            row_ = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row_, dict):
            out.append(row_)
    return out


def _settings_from_text(text: str):
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return config.default_settings()
    return config._from_dict(raw)


class Comparison(BothImplementationsAgree):
    def test_every_file_reads_the_same_way(self):
        for label, payload in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(payload)),
                                 typed(self._python(payload)))

    def test_the_corpus_reaches_both_answers_everywhere(self):
        """A corpus where nothing is ever dropped would pass against a stub."""
        dropped = filtered = kept = 0
        for payload in CASES.values():
            answer = self._python(payload)
            lines = len(answer["lines"])
            dropped += lines - len(answer["records"])
            filtered += len(answer["sessions_all"]) - len(answer["sessions_for_exe"])
            kept += len(answer["records"])
        self.assertGreater(dropped, 0, "no corpus entry has a line worth dropping")
        self.assertGreater(filtered, 0, "no corpus entry is filtered by exe")
        self.assertGreater(kept, 0, "no corpus entry has a usable record")


class NoFileShapeCanBreakAReader(unittest.TestCase):
    """A history nobody can list is worse than a history with a gap in it.

    Both readers already skipped a line that would not parse, so the intent
    was there. This is the property that intent implies, asserted.
    """

    def test_no_corpus_file_raises(self):
        for label, payload in CASES.items():
            with self.subTest(label):
                BothImplementationsAgree._python(payload)

    def test_nor_do_shapes_the_corpus_does_not_pin(self):
        for text in ('{"exe": {"nested": 1}}', '{"exe": [[[1]]]}',
                     "\x00\n" + row("a"), "﻿" + row("a")):
            with self.subTest(text[:40]):
                BothImplementationsAgree._python(case(text, exe="a"))


if __name__ == "__main__":
    unittest.main()
