"""The Rust and Python benchmark comparisons agree, row for row.

`b` is the "after" run, and `better` points at whichever side actually
improved - which is not whichever number is larger, because for temperatures,
frame time and stutter the improvement is downwards. Getting that backwards
would tell somebody a hotter, slower run was the better one.

The corpus carries the arithmetic corners on purpose: a baseline of zero, which
has no percentage to change by; a negative baseline, where `abs()` on the
bottom is what keeps the sign right; and values that land on a rounding
boundary, since both deltas go through Python's round.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import benchmarkcard

_REPO = Path(__file__).resolve().parent.parent

FIELDS = [f for f, _ in benchmarkcard._METRICS]

PAIRS = [
    ({}, {}),
    ({"fps_avg": 60.0}, {}),
    ({}, {"fps_avg": 60.0}),
    ({"fps_avg": 60.0}, {"fps_avg": 60.0}),
    ({"fps_avg": 60.0}, {"fps_avg": 90.0}),
    ({"fps_avg": 90.0}, {"fps_avg": 60.0}),
    # Lower-is-better, both directions.
    ({"cpu_temp_max": 90.0}, {"cpu_temp_max": 80.0}),
    ({"cpu_temp_max": 80.0}, {"cpu_temp_max": 90.0}),
    ({"frametime_stutter_pct": 3.0}, {"frametime_stutter_pct": 2.0}),
    # A baseline of zero, on both kinds of metric.
    ({"fps_avg": 0.0}, {"fps_avg": 60.0}),
    ({"cpu_temp_max": 0.0}, {"cpu_temp_max": 5.0}),
    ({"fps_avg": 0}, {"fps_avg": 0}),
    # Negative baselines.
    ({"cpu_temp_avg": -10.0}, {"cpu_temp_avg": -5.0}),
    ({"cpu_temp_avg": -5.0}, {"cpu_temp_avg": -10.0}),
    # Percentages that land on a one-place boundary. The delta and the
    # percentage round to DIFFERENT places, so a pair that pins one says
    # nothing about the other.
    ({"fps_avg": 100.0}, {"fps_avg": 100.25}),
    ({"fps_avg": 1000.0}, {"fps_avg": 1001.5}),
    ({"fps_avg": 10.0}, {"fps_avg": 10.125}),
    ({"cpu_temp_avg": 200.0}, {"cpu_temp_avg": 200.5}),
    ({"fps_avg": 3.0}, {"fps_avg": 3.0585}),
    # Rounding boundaries, on the delta and on the percentage.
    ({"fps_avg": 1.0}, {"fps_avg": 1.005}),
    ({"fps_avg": 1.0}, {"fps_avg": 1.015}),
    ({"fps_avg": 200.0}, {"fps_avg": 200.25}),
    ({"fps_avg": 3.0}, {"fps_avg": 3.35}),
    ({"fps_avg": 16.0}, {"fps_avg": 16.625}),
    # Everything at once, and everything equal.
    ({f: 10.0 for f in FIELDS}, {f: 20.0 for f in FIELDS}),
    ({f: 10.0 for f in FIELDS}, {f: 10.0 for f in FIELDS}),
    # A field the sessions do not share.
    ({"fps_avg": 60.0, "fps_min": 30.0}, {"fps_avg": 61.0}),
    # Nulls, which is what a session that measured nothing holds.
    ({"fps_avg": None}, {"fps_avg": 60.0}),
    ({"fps_avg": None}, {"fps_avg": None}),
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_BENCHMARKCARD_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "benchmarkcard"
        if candidate.exists():
            return candidate
    return None


class BothImplementationsCompareTheSameWay(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the benchmarkcard "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example benchmarkcard`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example benchmarkcard`")

    def _rust(self, pairs) -> list:
        payload = {"pairs": [{"a": a, "b": b} for a, b in pairs]}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_pair_produces_the_same_rows(self):
        got = self._rust(PAIRS)
        for (a, b), answer in zip(PAIRS, got, strict=True):
            with self.subTest(a=a, b=b):
                self.assertEqual(answer, benchmarkcard.diff_sessions(a, b))

    def test_the_labels_match_character_for_character(self):
        """They are what the user reads, in the CLI table and the GUI page."""
        pair = ({f: 1.0 for f in FIELDS}, {f: 2.0 for f in FIELDS})
        rust = self._rust([pair])[0]
        python = benchmarkcard.diff_sessions(*pair)
        self.assertEqual([r["label"] for r in rust],
                         [r["label"] for r in python])


if __name__ == "__main__":
    unittest.main()
