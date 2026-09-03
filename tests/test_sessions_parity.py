"""The Rust and Python session arithmetic agree.

Weighted at the two places this port could quietly diverge:

* `percentile` indexes with `round(q * (n - 1))`, and Python's `round` is
  half-to-EVEN. For six samples at the median that is `round(2.5)` = 2, where
  the obvious `f64::round` gives 3 — a different frame-rate figure from the
  same log.
* `change_pct`, `baseline` and `current` all go through `round(x, 1)`, which is
  half-to-even as well.

Neither shows up on a corpus that avoids exact halves, so the corpus below
aims at them on purpose.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import sessions

_REPO = Path(__file__).resolve().parent.parent

_HEADER = "os,cpu,gpu,ram,kernel,driver\nLinux,x,y,16,7.2,570\n"


def _csv(rows: list[str], cols: str = "fps,cpu_temp,gpu_temp,frametime") -> str:
    return _HEADER + cols + "\n" + "".join(r + "\n" for r in rows)


def _binary() -> Path | None:
    override = os.environ.get("GMP_SESSION_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "session"
        if candidate.exists():
            return candidate
    return None


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the session example is not "
                          "built - run `cargo build -p gmp-core --example session`")
            self.skipTest("build it with `cargo build -p gmp-core --example session`")

    def _rust(self, payload: dict) -> dict:
        proc = subprocess.run([str(self.binary)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _python(self, payload: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.csv"
            path.write_text(payload["csv"])
            fps, cpu, gpu, ft = sessions._parse_csv_full(path)
        cur = sessions.SessionSummary(
            exe="x", game="TestGame", started="", ended="", duration_s=1,
            fps_1low=payload.get("current_1low"), fps_avg=payload.get("current_avg"))
        reg = sessions._detect_regression(cur, payload.get("prior", []))
        return {
            "fps": fps, "cpu_temp": cpu, "gpu_temp": gpu, "frametime_ms": ft,
            "percentile": sessions._percentile(sorted(fps), payload.get("q", 0.5)),
            "regression": reg.as_dict() if reg else None,
            "headline": reg.headline("TestGame") if reg else None,
        }

    def _same(self, payload: dict) -> dict:
        py, rs = self._python(payload), self._rust(payload)
        self.assertEqual(py, rs)
        return py

    # ---- the rounding traps ------------------------------------------------

    def test_the_median_of_six_samples(self):
        """round(0.5 * 5) is round(2.5): 2 in Python, 3 under f64::round."""
        out = self._same({"csv": _csv([f"{v},70,65,16" for v in (10, 20, 30, 40, 50, 60)]),
                          "q": 0.5})
        self.assertEqual(out["percentile"], 30.0)

    def test_percentiles_across_every_awkward_length(self):
        for n in range(1, 13):
            for q in (0.0, 0.01, 0.1, 0.5, 0.9, 0.99, 1.0):
                with self.subTest(n=n, q=q):
                    self._same({
                        "csv": _csv([f"{10 * (i + 1)},70,65,16" for i in range(n)]),
                        "q": q,
                    })

    def test_a_change_that_rounds_on_a_half(self):
        """change_pct goes through round(x, 1), also half-to-even."""
        for cur in (55.25, 66.75, 48.05, 51.15):
            with self.subTest(current=cur):
                self._same({
                    "csv": _csv(["60,70,65,16"]), "q": 0.5,
                    "current_1low": cur,
                    "prior": [{"fps_1low": 60.0} for _ in range(6)],
                })

    # ---- parsing -----------------------------------------------------------

    def test_garbage_and_out_of_range_rows(self):
        self._same({"csv": _csv([
            "60,70,65,16.6",
            "notanumber,70,65,16",
            "0,70,65,16",          # fps must be > 0
            "1500,70,65,16",       # and < 1000
            "90,999,65,16",        # cpu temp out of range, fps still counts
            "45",                  # short row
            "",                    # blank
            "120,72,66,8.3",
        ]), "q": 0.5})

    def test_cells_with_surrounding_whitespace(self):
        """Python's float() accepts " 60 " and Rust's parse() does not.

        Found by auditing capabilities.py for the same trap after its cpu-list
        parser hit it. MangoHud does not usually pad its columns, but a CSV
        that has been through anything else might.
        """
        self._same({"csv": _csv(["60 , 70 , 65 , 16.6", " 90,71,66,11", "120,72,66,8.3 "]),
                    "q": 0.5})

    def test_a_csv_with_no_temperature_columns(self):
        self._same({"csv": _csv(["60", "90", "120"], cols="fps"), "q": 0.5})

    def test_a_csv_with_no_header_at_all(self):
        self._same({"csv": "1,2,3\n4,5,6\n", "q": 0.5})

    def test_an_empty_csv(self):
        self._same({"csv": "", "q": 0.5})

    # ---- regression detection ---------------------------------------------

    def test_a_stable_one_percent_low_suppresses_everything(self):
        """The early return, which reads like a missing `continue`: a steady 1%
        low means nothing is reported even if the average moved enormously."""
        out = self._same({
            "csv": _csv(["60,70,65,16"]), "q": 0.5,
            "current_1low": 100.0, "current_avg": 1000.0,
            "prior": [{"fps_1low": 100.0, "fps_avg": 10.0} for _ in range(6)],
        })
        self.assertIsNone(out["regression"])

    def test_it_falls_through_to_average_when_there_is_no_one_percent_low(self):
        out = self._same({
            "csv": _csv(["60,70,65,16"]), "q": 0.5,
            "current_avg": 50.0,
            "prior": [{"fps_avg": 100.0} for _ in range(6)],
        })
        self.assertEqual(out["regression"]["metric"], "average FPS")

    def test_too_little_history_reports_nothing(self):
        out = self._same({
            "csv": _csv(["60,70,65,16"]), "q": 0.5,
            "current_1low": 10.0,
            "prior": [{"fps_1low": 100.0}, {"fps_1low": 100.0}],
        })
        self.assertIsNone(out["regression"])

    def test_only_the_most_recent_baseline_sessions_count(self):
        """Older sessions past the window must not drag the baseline."""
        self._same({
            "csv": _csv(["60,70,65,16"]), "q": 0.5,
            "current_1low": 60.0,
            "prior": [{"fps_1low": 10.0}] * 10 + [{"fps_1low": 60.0}] * 6,
        })

    def test_an_improvement_is_reported_too(self):
        out = self._same({
            "csv": _csv(["60,70,65,16"]), "q": 0.5,
            "current_1low": 200.0,
            "prior": [{"fps_1low": 100.0} for _ in range(6)],
        })
        self.assertEqual(out["regression"]["direction"], "improvement")
        self.assertIn("gained", out["headline"])

    def test_zero_and_missing_priors_are_ignored(self):
        self._same({
            "csv": _csv(["60,70,65,16"]), "q": 0.5,
            "current_1low": 50.0,
            "prior": [{"fps_1low": 0.0}, {"fps_1low": None}, {},
                      {"fps_1low": 100.0}, {"fps_1low": 100.0}, {"fps_1low": 100.0}],
        })


if __name__ == "__main__":
    unittest.main()
