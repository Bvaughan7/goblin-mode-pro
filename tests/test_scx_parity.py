"""The Rust and Python scheduler-name handling agree.

Two things are being pinned. The mode table, because a wrong id does not fail
- it quietly selects a different tuning of the correct scheduler, which no
downstream check would notice. And the name shape, because that check is what
stops a profile turning a scheduler field into a path or an argument.

So the corpus is weighted towards names that are ALMOST valid, not obviously
invalid ones: a length-32 name against a length-33 one, a leading digit
against a leading underscore, an embedded newline against a plain space.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import scx
from goblinmode.config import SCX_NAME_RE

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_SCX_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "scx"
        if candidate.exists():
            return candidate
    return None


NAMES = [
    # Real schedulers, as a profile would name them either way round.
    "lavd", "scx_lavd", "bpfland", "scx_bpfland", "rusty", "simple",
    "central", "flatcg", "p2dq", "nest", "layered",
    # Idempotence: feeding the full name back through.
    "scx_scx_lavd",
    # Length boundary, both sides of 32.
    "a" * 31, "a" * 32, "a" * 33,
    # First-character boundary.
    "0lavd", "_lavd", "-lavd", ".lavd",
    # Case: the pattern is lowercase-only and a name is a binary name.
    "LAVD", "Lavd", "lavD",
    # Shapes that would matter if the check were missing.
    "", "scx_", "lavd lavd", "lavd\n", "lavd\t", "la/vd", "../lavd",
    "lavd;true", "lavd$(id)", "lavd|x", "-rf", "/usr/bin/scx_lavd",
    # Underscores in every position.
    "l_a_v_d", "lavd_", "l__d",
    # Non-ASCII, which .isascii()-free regexes get wrong in both directions.
    "lavdé", "ｌａｖｄ", "lavd​",
]

MODES = ["auto", "gaming", "lowlatency", "powersave", "server",
         "", "Gaming", "GAMING", "nonsense", "auto ", " auto", "0", "1"]


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the scx example is not "
                          "built - run `cargo build -p gmp-core --example scx`")
            self.skipTest("build it with `cargo build -p gmp-core --example scx`")

    def _rust(self, name: str, mode: str) -> dict:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"name": name, "mode": mode}),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_mode_ids_match(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                want = scx.SCHED_MODES.get(mode, scx.SCHED_MODES[scx.DEFAULT_MODE])
                self.assertEqual(self._rust("lavd", mode)["mode_id"], want)

    def test_name_normalisation_matches(self):
        for name in NAMES:
            with self.subTest(name=name):
                got = self._rust(name, "gaming")
                short = name.removeprefix("scx_")
                self.assertEqual(got["short_name"], short)
                self.assertEqual(got["full_name"], f"scx_{short}")

    def test_name_validation_matches(self):
        for name in NAMES:
            with self.subTest(name=name):
                want = SCX_NAME_RE.match(name) is not None
                self.assertEqual(self._rust(name, "gaming")["valid_name"], want,
                                 f"{name!r} graded differently")


class ThePythonSideMeansWhatItSays(unittest.TestCase):
    """These hold with or without the Rust build, so they run in the normal suite."""

    def test_the_mode_table_is_the_loaders_enum(self):
        self.assertEqual(scx.SCHED_MODES,
                         {"auto": 0, "gaming": 1, "lowlatency": 2,
                          "powersave": 3, "server": 4})
        self.assertIn(scx.DEFAULT_MODE, scx.SCHED_MODES)

    def test_the_name_pattern_rejects_a_path(self):
        # The one case that would actually hurt: a profile field that becomes
        # part of a binary name.
        for bad in ("../lavd", "/usr/bin/scx_lavd", "lavd;true", "lavd lavd"):
            self.assertIsNone(SCX_NAME_RE.match(bad), bad)


if __name__ == "__main__":
    unittest.main()
