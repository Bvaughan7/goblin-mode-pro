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
from unittest import mock

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import sessions

_REPO = Path(__file__).resolve().parent.parent

_HEADER = "os,cpu,gpu,ram,kernel,driver\nLinux,x,y,16,7.2,570\n"


def _csv(rows: list[str], cols: str = "fps,cpu_temp,gpu_temp,frametime") -> str:
    return _HEADER + cols + "\n" + "".join(r + "\n" for r in rows)


def _frames(n: int, ft: float = 8.0) -> str:
    """*n* plausible rows - enough to get past the sample minimum, or not."""
    return _csv([f"{60 + (i % 7)},{70 + (i % 5)},{65 + (i % 3)},{ft}"
                 for i in range(n)])


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
        out = json.loads(proc.stdout)
        # The example answers every session question at once; this class diffs
        # the arithmetic, and the class below diffs the summary. Each drops
        # what it does not own, so that adding a question to the example does
        # not fail the tests for the ones already there.
        out.pop("summary", None)
        return out

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


class _FakeClock:
    """`sessions.datetime` with the wall clock taken out.

    The summary carries two timestamps, and neither is a decision - the
    tracker asks the clock and writes down what it says. Pinning them is what
    lets the two implementations be compared on the fields that ARE decisions.
    """

    def __init__(self, stamps):
        self._stamps = list(stamps)

    def now(self, tz=None):
        return self

    def isoformat(self, timespec="seconds"):
        return self._stamps.pop(0)


class TheSummaryOfAFinishedSession(unittest.TestCase):
    """`SessionTracker.end`'s arithmetic, against `sessions::summarise`.

    Everything the tracker OWNS is pinned here - the clock, which CSVs belong
    to the window, the file it appends to - so that what is left to diff is
    the record it makes: the gates that decide a session is worth keeping at
    all, which statistics a benchmark adds, and the rounding on every number
    a user reads.
    """

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the session example is not "
                          "built - run `cargo build -p gmp-core --example session`")
            self.skipTest("build it with `cargo build -p gmp-core --example session`")

    # -- the two sides -------------------------------------------------------

    def _rust(self, payload: dict) -> dict:
        proc = subprocess.run([str(self.binary)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["summary"]

    def _python(self, payload: dict) -> dict | None:
        op = payload["open"]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "s.csv"
            csv_path.write_text(payload["csv"])
            tracker = sessions.SessionTracker()
            clock = _FakeClock([payload["started"], payload["ended"]])
            with mock.patch.object(sessions, "datetime", clock), \
                 mock.patch.object(sessions.time, "monotonic",
                                   side_effect=[0.0, payload["elapsed_s"]]), \
                 mock.patch.object(sessions, "_logs_for_window", return_value=[csv_path]), \
                 mock.patch.object(sessions.platform, "release",
                                   return_value=payload["kernel"]), \
                 mock.patch.object(sessions, "SESSION_FILE", Path(tmp) / "sessions.jsonl"), \
                 mock.patch.object(sessions, "ensure_user_dirs", lambda: None):
                tracker.start(op["exe"], op["game"], op["tweaks"])
                result = tracker.end(op["exe"], benchmark=payload.get("benchmark", False))
        return None if result is None else result[0].as_dict()

    def _same(self, payload: dict):
        payload = {
            "started": "2026-09-05T12:00:00+00:00",
            "ended": "2026-09-05T12:30:00+00:00",
            "kernel": "7.2.2-1-cachyos",
            "elapsed_s": 1800.0,
            **payload,
        }
        payload.setdefault("open", {})
        payload["open"] = {"exe": "game.exe", "game": "A Game", "tweaks": ["governor"],
                           **payload["open"]}
        py, rs = self._python(payload), self._rust(payload)
        # `typed` and not `==`: `samples` is an int where every other number is
        # a float, and the averages are compared by repr, which is what makes a
        # difference in SUMMATION ORDER visible at all.
        self.assertEqual(typed(py), typed(rs))
        return py

    # -- is this session worth recording -------------------------------------

    def test_a_session_shorter_than_a_minute_is_not_recorded(self):
        self.assertIsNone(self._same({"csv": _frames(60), "elapsed_s": 59.9}))

    def test_a_session_of_exactly_a_minute_is(self):
        self.assertIsNotNone(self._same({"csv": _frames(60), "elapsed_s": 60.0}))

    def test_a_benchmark_gets_half_the_minimum(self):
        self.assertIsNone(self._same({"csv": _frames(60), "elapsed_s": 29.9,
                                      "benchmark": True}))
        self.assertIsNotNone(self._same({"csv": _frames(60), "elapsed_s": 30.0,
                                         "benchmark": True}))

    def test_a_clock_that_went_backwards_records_no_time_rather_than_negative(self):
        self.assertIsNone(self._same({"csv": _frames(60), "elapsed_s": -5.0}))

    def test_the_duration_is_rounded_on_a_half(self):
        for elapsed in (60.05, 61.15, 90.25, 120.35):
            with self.subTest(elapsed=elapsed):
                self._same({"csv": _frames(60), "elapsed_s": elapsed})

    # -- how many frames make the statistics meaningful ----------------------

    def test_one_sample_short_of_the_minimum_leaves_every_fps_field_empty(self):
        out = self._same({"csv": _frames(29)})
        self.assertEqual(out["samples"], 0)
        self.assertIsNone(out["fps_avg"])

    def test_the_minimum_itself_fills_them_in(self):
        out = self._same({"csv": _frames(30)})
        self.assertEqual(out["samples"], 30)

    def test_a_csv_with_no_frames_at_all(self):
        self._same({"csv": _csv([])})

    # -- the statistics ------------------------------------------------------

    def test_the_averages_are_summed_the_way_PYTHON_sums(self):
        """`sum()` over floats is not a fold, and has not been since 3.12.

        CPython carries a compensation term (Kahan-Babuska-Neumaier), so its
        answer is far closer to the exact sum than `iter().sum::<f64>()` gets.
        The gap is an ulp or two, which survives `round(x, 1)` only when the
        mean lands on a rounding boundary - so these columns are built to land
        on one rather than to look like anything a game would produce. Each
        sums to an exact decimal: the frame rates and both temperatures to a
        mean of 55.15, the frame times to 16.625, which is representable
        exactly and so is a genuine half-way case at two places.

        Summed as a fold this reports 55.2 where Python says 55.1 - which is
        how the divergence was found. Generated by `find_order.py`.

        It also settles a question the port has to answer. Python sums the
        frame rates SORTED and the temperatures in the order they were logged,
        and the compensation makes that distinction unobservable: over 257,000
        constructed boundary-straddling sets, no order of any of these columns
        differed from another by a single bit (`find_order_compensated.py`).
        The two orders are kept because they are what Python does, not because
        anything can tell.
        """
        out = self._same({"csv": _csv([
        "77.427032,31.379911,47.759058,7.011453",
        "30.275105,21.961966,32.209500,5.201823",
        "29.269986,61.271587,76.565940,5.560909",
        "79.294885,75.280826,43.260079,8.622116",
        "76.331546,92.083246,26.420803,25.534445",
        "78.265039,83.145965,67.240632,16.259584",
        "78.726688,70.243270,79.186534,7.676662",
        "55.643609,81.117681,35.637497,16.793461",
        "25.314744,31.338247,43.813639,19.302256",
        "33.130622,85.167838,35.459107,22.897314",
        "27.502889,34.166634,71.705377,5.860154",
        "59.775303,84.051364,82.990839,25.220618",
        "49.322568,82.311960,67.985927,4.788438",
        "78.027049,16.298024,59.302898,25.168923",
        "35.455423,72.860065,70.330271,21.988546",
        "83.088746,38.363824,40.059904,26.995549",
        "16.887355,95.466321,61.651527,12.362311",
        "41.330991,14.315308,56.539278,20.574242",
        "84.689007,33.893649,26.161572,13.007670",
        "62.341093,36.919484,62.904815,4.267460",
        "33.464159,32.787223,16.402454,19.488898",
        "86.690868,77.338645,59.150365,6.508802",
        "17.417081,29.938806,88.150865,21.033157",
        "84.669149,88.476394,75.349248,22.114575",
        "53.796420,22.076154,72.904785,7.241309",
        "26.002729,57.540083,16.214422,26.278669",
        "48.833788,83.359086,65.373353,21.805550",
        "83.365548,85.020385,58.280393,6.372532",
        "63.005112,88.337646,83.236296,28.878763",
        "36.207502,78.545810,53.442679,20.056300",
        "61.528231,28.029264,82.542179,12.618460",
        "43.690237,88.989174,22.416464,6.654377",
        "85.270841,21.414096,28.933964,13.066905",
        "86.475408,47.139843,44.463478,12.034348",
        "81.258352,39.464174,27.850779,28.628672",
        "58.034386,50.954680,25.070012,11.042207",
        "43.723646,19.451339,49.430933,11.898469",
        "39.979556,26.906648,50.284046,28.980778",
        "45.917569,81.931718,19.100936,25.964358",
        "24.569738,16.661662,180.217152,39.238937",
        ]), "benchmark": True})
        self.assertEqual(out["fps_avg"], 55.1)            # 55.2 summed as a fold
        self.assertEqual(out["cpu_temp_avg"], 55.1)
        self.assertEqual(out["gpu_temp_avg"], 55.1)
        self.assertEqual(out["frametime_ms_avg"], 16.62)  # 16.6 at one place

    def test_the_lowest_frame_rate_is_the_minimum_not_the_first(self):
        rows = [f"{v},70,65,16" for v in ([120] * 20 + [17.5] + [120] * 19)]
        out = self._same({"csv": _csv(rows)})
        self.assertEqual(out["fps_min"], 17.5)

    def test_every_fps_figure_rounds_on_a_half(self):
        rows = [f"{v},70,65,16" for v in
                ([55.25] * 10 + [66.75] * 10 + [48.05] * 10 + [51.15] * 10)]
        self._same({"csv": _csv(rows), "benchmark": True})


    def test_a_session_a_hundredth_short_of_the_minute(self):
        """The gate is on the UNROUNDED time. 59.99 s is recorded as 60.0 s by
        a build that rounds first, which is a session that never happened."""
        self.assertIsNone(self._same({"csv": _frames(60), "elapsed_s": 59.99}))

    def test_the_low_percentiles_pull_apart_once_there_are_enough_frames(self):
        """With 40 frames the 0.1% low, the 1% low and the minimum are all the
        same sample, and p95 and p99 land on the same repeated value - so a
        corpus of 40 cannot tell any of those percentiles from each other.
        120 distinct frame rates separate all of them."""
        rows = [f"{20.0 + i * 0.5},70,65,8.0" for i in range(120)]
        out = self._same({"csv": _csv(rows), "benchmark": True})
        self.assertEqual(out["fps_min"], 20.0)
        self.assertEqual(out["fps_01low"], 20.0)
        self.assertEqual(out["fps_1low"], 20.5)
        self.assertEqual(out["fps_p95"], 76.5)

    def test_the_stutter_threshold_is_twice_the_MEDIAN_frame_time(self):
        """A handful of very long frames drags the mean far above the median,
        and a threshold built on the mean then misses the stutters that a
        player actually felt."""
        rows = [f"60,70,65,{ft}" for ft in ([8.0] * 30 + [20.0] * 5 + [100.0] * 5)]
        out = self._same({"csv": _csv(rows), "benchmark": True})
        self.assertEqual(out["frametime_stutter_pct"], 25.0)  # 5.0 off the mean

    # -- what a benchmark adds ----------------------------------------------

    def test_an_ordinary_session_leaves_the_benchmark_fields_empty(self):
        out = self._same({"csv": _frames(40, ft=8.0)})
        self.assertIsNone(out["fps_01low"])
        self.assertIsNone(out["fps_p95"])
        self.assertIsNone(out["frametime_ms_avg"])
        self.assertIsNone(out["cpu_temp_max"])
        self.assertIsNone(out["gpu_temp_max"])

    def test_a_benchmark_fills_them_in(self):
        out = self._same({"csv": _frames(40, ft=8.0), "benchmark": True})
        self.assertIsNotNone(out["fps_01low"])
        self.assertIsNotNone(out["frametime_stutter_pct"])
        self.assertIsNotNone(out["cpu_temp_max"])

    def test_a_frame_of_exactly_twice_the_median_is_not_a_stutter(self):
        """The comparison is `>`, not `>=`, and a doubled frame time is the
        textbook case for getting that boundary wrong."""
        rows = [f"60,70,65,{ft}" for ft in ([8.0] * 24 + [16.0] * 8)]
        out = self._same({"csv": _csv(rows), "benchmark": True})
        self.assertEqual(out["frametime_stutter_pct"], 0.0)

    def test_the_stutter_percentage_rounds_on_an_exact_half(self):
        """7 of 32 frames is 21.875%, which is exactly representable, so
        `round(x, 2)` lands on the halfway case and goes to even."""
        rows = [f"60,70,65,{ft}" for ft in ([8.0] * 25 + [40.0] * 7)]
        out = self._same({"csv": _csv(rows), "benchmark": True})
        self.assertEqual(out["frametime_stutter_pct"], 21.88)

    def test_too_few_frame_times_leave_the_stutter_figure_empty(self):
        """The frame-time series has its own minimum, and it is counted over
        the FRAME TIMES - a CSV can carry 40 frame rates and 29 frame times."""
        rows = [f"60,70,65,{8.0 if i < 29 else 5000}" for i in range(40)]
        out = self._same({"csv": _csv(rows), "benchmark": True})
        self.assertIsNone(out["frametime_stutter_pct"])
        self.assertIsNone(out["frametime_ms_avg"])

    # -- temperatures --------------------------------------------------------

    def test_a_csv_whose_temperatures_are_all_out_of_range(self):
        rows = ["60,999,999,16"] * 40
        out = self._same({"csv": _csv(rows), "benchmark": True})
        self.assertIsNone(out["cpu_temp_avg"])
        self.assertIsNone(out["gpu_temp_max"])

    def test_a_csv_with_no_temperature_columns_at_all(self):
        self._same({"csv": _csv(["60"] * 40, cols="fps"),
                    "benchmark": True})

    # -- what the tracker carries through ------------------------------------

    def test_a_game_with_no_display_name_is_recorded_under_its_executable(self):
        out = self._same({"csv": _frames(40), "open": {"game": ""}})
        self.assertEqual(out["game"], "game.exe")

    def test_the_tweaks_that_were_active_are_carried_through_in_order(self):
        out = self._same({"csv": _frames(40),
                          "open": {"tweaks": ["governor", "mangohud", "scx_lavd"]}})
        self.assertEqual(out["tweaks"], ["governor", "mangohud", "scx_lavd"])

    def test_a_session_with_no_tweaks_at_all(self):
        self._same({"csv": _frames(40), "open": {"tweaks": []}})


if __name__ == "__main__":
    unittest.main()
