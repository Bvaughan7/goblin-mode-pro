"""The Rust and Python cold-revert paths read the same state file.

``applied.json`` is what the daemon leaves behind so that a *different*
process can undo its work: the ``--revert`` hook systemd runs on stop, and the
stale-state check a fresh daemon does at startup. Both are recovery paths, and
the corpus is built on that: most of it is files that are wrong in some way,
because a recovery path that cannot cope with a damaged file is a recovery
path that does not work when it is needed.

Every "broken" entry below crashed at least one of the three readers before
this port, ``AttributeError`` or ``TypeError`` escaping all the way out. The
loader was written to treat a bad file as no file - it caught ``OSError`` and
``JSONDecodeError`` - and it did that correctly for a file that will not
parse, but not for one that parses into something that is not an object, nor
for a field inside a good object holding the wrong type.

Two divergences are pinned rather than fixed, both at the JSON layer and
neither reachable from a file this program writes:

* Python's ``json`` accepts ``NaN``, ``Infinity`` and ``-Infinity``, which are
  not JSON. ``serde_json`` rejects them, so Python reads such a file as usable
  and Rust reads it as absent.
* An integer too large for 64 bits keeps its value in Python and becomes a
  float in ``serde_json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import payload

_REPO = Path(__file__).resolve().parent.parent
STATE_PATH = "/home/u/.local/share/goblin-mode-pro/applied.json"


def _binary() -> Path | None:
    override = os.environ.get("GMP_APPLIED_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "applied"
        if candidate.exists():
            return candidate
    return None


def js(obj) -> str:
    return json.dumps(obj)


# A realistic file, as the daemon actually writes one.
APPLIED = js({
    "active": ["Wow.exe"],
    "governor_applied": True,
    "power_applied": True,
    "power_backend": "rapl",
    "tearing_applied": True,
    "adaptive_sync_applied": False,
    "refresh_cap_applied": False,
    "focus_mode": True,
    "scx_applied": "rusty",
    "scx_previous": "lavd",
    "reniced": {"12345": -5},
    "compositor": {"tearing_active": True, "tearing_saved": False,
                   "vrr_active": False, "refresh_active": True,
                   "refresh_saved": {"DP-1": 60}, "x11_suspended": False},
})

# The same file after a clean shutdown: present, everything cleared.
CLEAN = js({
    "active": [], "governor_applied": False, "power_applied": False,
    "power_backend": None, "tearing_applied": False,
    "adaptive_sync_applied": False, "refresh_cap_applied": False,
    "focus_mode": False, "scx_applied": None, "scx_previous": None,
    "reniced": {}, "compositor": {"tearing_active": False, "vrr_active": False,
                                  "refresh_active": False, "x11_suspended": False},
})

CASES = {
    # -- the file as it is really written ---------------------------------
    "a_real_applied_file": APPLIED,
    "a_clean_shutdown": CLEAN,
    "an_empty_object": "{}",
    # -- the file is not usable -------------------------------------------
    "unparseable": '{"active": ["Wow.exe"',
    "empty_file": "",
    "whitespace_only": "   \n  ",
    "top_level_list": "[1, 2]",
    "top_level_string": '"hello"',
    "top_level_number": "5",
    "top_level_true": "true",
    "top_level_null": "null",
    "top_level_empty_list": "[]",
    # -- the object is usable but a field is not --------------------------
    "compositor_is_a_string": js({"compositor": "yes"}),
    "compositor_is_a_list": js({"compositor": ["yes"]}),
    "compositor_is_a_number": js({"compositor": 3}),
    "compositor_is_true": js({"compositor": True}),
    "compositor_is_null": js({"compositor": None}),
    "compositor_key_is_a_string": js({"compositor": {"vrr_active": "yes"}}),
    "compositor_key_is_zero": js({"compositor": {"vrr_active": 0}}),
    "active_is_a_number": js({"active": 5}),
    "active_is_a_float": js({"active": 5.0}),
    "active_is_true": js({"active": True}),
    "active_is_a_string": js({"active": "Wow.exe"}),
    "active_is_a_dict": js({"active": {"Wow.exe": 1}}),
    "active_is_a_list_of_numbers": js({"active": [1, 2]}),
    "active_is_a_list_of_nulls": js({"active": [None]}),
    "active_is_a_nested_list": js({"active": [["a"]]}),
    "active_is_a_list_of_floats": js({"active": [1.5, 2.0]}),
    "active_is_a_list_of_bools": js({"active": [True, False]}),
    "reniced_is_a_number": js({"reniced": 5}),
    "reniced_is_true": js({"reniced": True}),
    "reniced_is_a_string": js({"reniced": "1234"}),
    "reniced_is_a_list": js({"reniced": [1234, 5678]}),
    "scx_applied_is_a_list": js({"scx_applied": ["a", "b"]}),
    "scx_applied_is_a_number": js({"scx_applied": 5}),
    "scx_previous_is_a_list": js({"scx_applied": "rusty", "scx_previous": ["a"]}),
    "power_backend_is_a_list": js({"power_backend": ["rapl"]}),
    "power_backend_is_a_number": js({"power_backend": 7}),
    # -- the scheduler lines, which have two shapes -----------------------
    "scx_with_a_previous": js({"scx_applied": "rusty", "scx_previous": "lavd"}),
    "scx_without_a_previous": js({"scx_applied": "rusty"}),
    "scx_with_a_null_previous": js({"scx_applied": "rusty", "scx_previous": None}),
    "scx_with_an_empty_previous": js({"scx_applied": "rusty", "scx_previous": ""}),
    # -- one field at a time, so each line is exercised alone -------------
    "only_governor": js({"governor_applied": True}),
    "only_power": js({"power_applied": True}),
    "only_tearing": js({"tearing_applied": True}),
    "only_adaptive_sync": js({"adaptive_sync_applied": True}),
    "only_refresh_cap": js({"refresh_cap_applied": True}),
    "only_focus_mode": js({"focus_mode": True}),
    "only_reniced": js({"reniced": {"12345": -5}}),
    "only_power_backend": js({"power_backend": "rapl"}),
    "only_compositor_tearing": js({"compositor": {"tearing_active": True}}),
    "only_compositor_vrr": js({"compositor": {"vrr_active": True}}),
    "only_compositor_refresh": js({"compositor": {"refresh_active": True}}),
    "only_compositor_x11": js({"compositor": {"x11_suspended": True}}),
    # power_backend alone is not enough to be dirty, so it is reported only
    # when something else already made the file dirty. Both orders are here.
    "power_backend_without_anything_applied": js({"power_backend": "rapl"}),
    "power_backend_with_something_applied": js({"power_backend": "rapl",
                                                "focus_mode": True}),
    # -- values that are only falsy in one obvious reading ----------------
    "zero_is_not_applied": js({"governor_applied": 0, "focus_mode": 0.0}),
    "empty_string_is_not_applied": js({"scx_applied": "", "power_backend": ""}),
    "empty_containers_are_not_applied": js({"active": [], "reniced": {}}),
    "a_number_is_applied": js({"governor_applied": 1}),
    "a_nonempty_string_is_applied": js({"governor_applied": "no"}),
    # -- unknown keys, which are simply not read --------------------------
    "an_unknown_key": js({"who_knows": True}),
    "an_unknown_key_beside_a_real_one": js({"who_knows": True, "focus_mode": True}),
    # -- text that has to survive the round trip --------------------------
    "unicode_game_name": js({"active": ["ゲーム.exe"]}),
    "a_name_with_a_comma": js({"active": ["a, b", "c"]}),
    "a_name_with_a_newline": js({"active": ["a\nb"]}),
}

# Floats reach the describe output through `active`, and Python's `str()` for a
# float is not what Rust's `{}` prints - 5.0 renders as "5.0" and "5". These
# straddle every boundary in CPython's repr: the switch to exponent notation at
# either end, the two-digit exponent, and a negative zero.
FLOATS = [0.0, -0.0, 5.0, -5.0, 0.1, 1.5, -2.75, 123.456, 1e15, 1e16, 1e-4, 1e-5,
          1.5e20, 1e100, 1e-100, 3.141592653589793, 2.5, 1e22, 9007199254740993.0,
          0.30000000000000004, 1234567890123456.0, 12345678901234567.0]


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the applied example is not "
                          "built - run `cargo build -p gmp-core --example applied`")
            self.skipTest("build it with `cargo build -p gmp-core --example applied`")

    def _rust(self, raw: str | None) -> dict:
        payload_in = {"raw": raw, "path": STATE_PATH}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload_in),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(raw: str | None, tmp: Path) -> dict:
        # `raw is None` is the file being absent, which is the one state no
        # file content can stand in for.
        if raw is None:
            tmp.unlink(missing_ok=True)
        else:
            tmp.write_text(raw)
        with patch.object(payload, "APPLIED_STATE_FILE", tmp):
            data = payload._read_applied_state()

        # describe() interpolates the state file's path into its output and the
        # Rust is handed the real one, so from here the record is fixed and
        # only the path still has to match.
        with patch.object(payload, "APPLIED_STATE_FILE", Path(STATE_PATH)), \
                patch.object(payload, "_read_applied_state", lambda: data):
            plan = payload.revert_plan(data)
            return {
                "parsed": data is not None,
                "dirty": payload.applied_state_dirty(),
                "describe": payload.describe_applied_state(),
                "plan": {
                    "compositor": plan.compositor,
                    "compositor_state": plan.compositor_state,
                    "focus_mode": plan.focus_mode,
                    "scx": plan.scx,
                    "scx_previous": plan.scx_previous,
                },
            }

    def test_every_state_file_reads_the_same_way(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            for label, raw in CASES.items():
                with self.subTest(label):
                    self.assertEqual(typed(self._rust(raw)),
                                     typed(self._python(raw, tmp)))

    def test_an_absent_file_reads_the_same_way(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            self.assertEqual(typed(self._rust(None)), typed(self._python(None, tmp)))

    def test_floats_render_the_way_python_renders_them(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            for value in FLOATS:
                raw = json.dumps({"active": [value], "focus_mode": True})
                with self.subTest(repr(value)):
                    got = self._rust(raw)
                    self.assertEqual(got, self._python(raw, tmp))
                    # And the line really does carry the number, so this is not
                    # two implementations agreeing on an empty string.
                    self.assertIn(f"active games: {value}", got["describe"])

    def test_the_corpus_actually_exercises_both_answers(self):
        """A corpus that is all one answer would pass against a stub."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            dirty = {self._python(raw, tmp)["dirty"] for raw in CASES.values()}
            parsed = {self._python(raw, tmp)["parsed"] for raw in CASES.values()}
        self.assertEqual(dirty, {True, False})
        self.assertEqual(parsed, {True, False})


# Files Python's json reads and serde_json refuses, and the one number the two
# parse differently. Neither is reachable from a file this program writes -
# json.dump never emits NaN for a value the daemon records, and no field here
# holds a number that large - so these are pinned rather than fixed.
JSON_LAYER_DIVERGENCES = {
    "nan_is_not_json": '{"active": [NaN]}',
    "infinity_is_not_json": '{"active": [Infinity]}',
    "negative_infinity_is_not_json": '{"active": [-Infinity]}',
}

HUGE_INT = '{"active": [123456789012345678901234567890], "focus_mode": true}'


class TheJsonLayerDivergesInTwoPlaces(BothImplementationsAgree):
    """Pinned, with the direction each one fails in stated.

    Both are in the parser rather than in anything this port wrote, and both
    are worth knowing before the Rust becomes the shipped reader.
    """

    def test_python_accepts_three_literals_that_are_not_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            for label, raw in JSON_LAYER_DIVERGENCES.items():
                with self.subTest(label):
                    want = self._python(raw, tmp)
                    got = self._rust(raw)
                    self.assertTrue(want["parsed"], "Python should read this file")
                    self.assertFalse(got["parsed"], "serde_json should refuse it")
                    # This is the unsafe direction and the reason it is written
                    # down: Rust calls the file absent, so it reports nothing to
                    # undo and the cold revert would skip work Python would do.
                    self.assertTrue(want["dirty"])
                    self.assertFalse(got["dirty"])

    def test_an_integer_too_large_for_64_bits_becomes_a_float(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            want = self._python(HUGE_INT, tmp)
            got = self._rust(HUGE_INT)
        # Both agree the file is usable and dirty; they disagree only on how
        # the number reads back, which is a diagnostic line and nothing more.
        self.assertEqual(want["dirty"], got["dirty"])
        self.assertEqual(want["plan"], got["plan"])
        self.assertIn("active games: 123456789012345678901234567890", want["describe"])
        self.assertIn("active games: 1.2345678901234568e+29", got["describe"])


class TheReadersNeverRaise(unittest.TestCase):
    """The property the whole module rests on, asserted against Python directly.

    The parity corpus proves the two agree; this proves what they agree *on* is
    "carry on". Without it a future change could make both raise in step.
    """

    def test_no_state_file_shape_can_stop_the_daemon_starting(self):
        import tempfile
        shapes = [
            *CASES.values(),
            js({"compositor": {"vrr_active": [1]}}),
            js({"active": [{"a": 1}]}),
            js({"reniced": [[1]]}),
            js({"scx_applied": {"a": 1}, "scx_previous": {"b": 2}}),
        ]
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "applied.json"
            for raw in shapes:
                tmp.write_text(raw)
                with patch.object(payload, "APPLIED_STATE_FILE", tmp), \
                        self.subTest(raw[:60]):
                    payload.applied_state_dirty()
                    payload.describe_applied_state()
                    payload.revert_plan(payload._read_applied_state())


if __name__ == "__main__":
    unittest.main()
