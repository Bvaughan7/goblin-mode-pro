"""The Rust and Python Prometheus exporters write the same document.

The `.prom` file is parsed by node_exporter, which is not this project, so the
bytes are the contract: the HELP and TYPE lines, the order the metrics come in,
which ones are absent, and how a float is spelled.

That last one is the whole risk. Python writes these with `%g` - six
significant digits, an exponent below -4 or at or above 6, trailing zeros
stripped, and the exponent always signed and two digits wide - and Rust has no
equivalent. Worse, `%g` decides which form to use AFTER rounding, so 999999.5
is `1e+06` rather than a shortened `999999.5`. A port that formatted floats the
obvious way would produce a file that still parses and holds different numbers.

So the corpus is in two halves: whole status snapshots, and a few thousand
floats.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import exporter

_REPO = Path(__file__).resolve().parent.parent

STATUSES = [
    {},
    {"master_enabled": False},
    {"master_enabled": True, "active_games": ["Wow.exe"], "forced_boost": False,
     "helper_available": True, "limited_mode": False,
     "health": {"score": 8.5},
     "latest_sample": {"cpu_temp": 72.4, "cpu_load": 41.25,
                       "pkg_power_w": 45.0, "gpu_load": 99.0, "gpu_temp": 68.0},
     "fps": {"fps_avg": 143.66666, "fps_min": 61.0, "fps_1low": 88.125}},
    # Everything unreadable: the gaps must stay gaps.
    {"health": {}, "latest_sample": {}, "fps": {}},
    {"health": {"score": None}, "latest_sample": {"cpu_temp": None}},
    # Wrong types, which cross the interface as JSON and can be anything.
    {"health": {"score": "8.5"}, "latest_sample": {"cpu_temp": True,
                                                   "cpu_load": "  41.5  ",
                                                   "gpu_temp": [1],
                                                   "pkg_power_w": {"a": 1}}},
    {"active_games": "abc"},
    {"active_games": {"Wow.exe": 1}},
    {"forced_boost": True, "active_games": []},
    # Values that exercise %g inside a real document.
    {"latest_sample": {"cpu_temp": 999999.5, "cpu_load": 0.0000001,
                       "pkg_power_w": 1234567.0, "gpu_load": 0.0,
                       "gpu_temp": -0.0}},
    {"health": {"score": 10}, "latest_sample": {"cpu_temp": 1e100}},
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_EXPORTER_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "exporter"
        if candidate.exists():
            return candidate
    return None


def _floats() -> list[float]:
    """Weighted towards the boundaries `%g` turns on."""
    fixed = [0.0, -0.0, 1.0, -1.0, 0.5, 7.0, 55.15, 123.456789, 12345.6789,
             99999.9, 999999.0, 999999.4, 999999.5, 999999.6, 1000000.0,
             1234567.0, 0.001, 0.0001, 0.000099999, 0.00001, 0.000001,
             1e15, 1e16, 1e100, 1e-100, 2.5, 3.5, 0.1 + 0.2, 1 / 3,
             math.pi, math.e, 1e6 - 0.5, 6.02214076e23, 1.5e-5]
    rng = random.Random(20260907)
    out = list(fixed)
    for _ in range(2000):
        kind = rng.random()
        if kind < 0.3:
            out.append(rng.uniform(-200, 200))
        elif kind < 0.5:
            out.append(rng.uniform(999_990, 1_000_010))
        elif kind < 0.7:
            out.append(rng.uniform(0, 0.001))
        elif kind < 0.85:
            out.append(float(rng.randint(-10**9, 10**9)))
        else:
            out.append(rng.uniform(-1, 1) * 10 ** rng.randint(-30, 30))
    return out


class BothImplementationsWriteTheSameDocument(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the exporter example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example exporter`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example exporter`")

    def _rust(self, payload: dict) -> list:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_status_renders_identically(self):
        got = self._rust({"which": "render", "statuses": STATUSES})
        for status, answer in zip(STATUSES, got, strict=True):
            with self.subTest(status=status):
                self.assertEqual(answer, exporter.render(status))

    def test_a_few_thousand_floats_are_spelled_the_same(self):
        values = _floats()
        got = self._rust({"which": "g", "values": values})
        for value, answer in zip(values, got, strict=True):
            if answer != f"{value:g}":
                self.fail(f"{value!r}: python={value:g!r} rust={answer!r}")

    def test_the_form_is_chosen_after_rounding(self):
        """The one `%g` rule a hand-written port loses: 999999.5 has an
        exponent of 5, which is fixed form, until six significant digits round
        it to 1000000 and the exponent becomes 6."""
        self.assertEqual(f"{999999.5:g}", "1e+06")
        self.assertEqual(self._rust({"which": "g", "values": [999999.5]}),
                         ["1e+06"])


if __name__ == "__main__":
    unittest.main()
