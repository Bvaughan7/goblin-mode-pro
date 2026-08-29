import os
import unittest
from tempfile import TemporaryDirectory

from tests._support import _SRC  # noqa: F401

from goblinmode import benchmarkcard

try:
    import cairo  # noqa: F401
    _HAVE_CAIRO = True
except ImportError:
    _HAVE_CAIRO = False


class DiffSessions(unittest.TestCase):
    def test_higher_fps_is_better_for_b(self):
        a = {"fps_avg": 60.0, "fps_1low": 40.0}
        b = {"fps_avg": 75.0, "fps_1low": 40.0}
        rows = {r["field"]: r for r in benchmarkcard.diff_sessions(a, b)}
        self.assertEqual(rows["fps_avg"]["better"], "b")
        self.assertAlmostEqual(rows["fps_avg"]["delta"], 15.0)
        self.assertAlmostEqual(rows["fps_avg"]["delta_pct"], 25.0)
        self.assertIsNone(rows["fps_1low"]["better"])  # unchanged

    def test_lower_temp_is_better_for_b(self):
        a = {"gpu_temp_max": 80.0}
        b = {"gpu_temp_max": 70.0}
        rows = {r["field"]: r for r in benchmarkcard.diff_sessions(a, b)}
        self.assertEqual(rows["gpu_temp_max"]["better"], "b")

    def test_lower_stutter_is_better_for_b(self):
        a = {"frametime_stutter_pct": 5.0}
        b = {"frametime_stutter_pct": 8.0}
        rows = {r["field"]: r for r in benchmarkcard.diff_sessions(a, b)}
        self.assertEqual(rows["frametime_stutter_pct"]["better"], "a")

    def test_metric_missing_from_both_is_omitted(self):
        rows = benchmarkcard.diff_sessions({}, {})
        self.assertEqual(rows, [])

    def test_metric_present_in_only_one_side_has_no_delta(self):
        a = {"fps_p95": 90.0}
        b = {}
        rows = {r["field"]: r for r in benchmarkcard.diff_sessions(a, b)}
        self.assertEqual(rows["fps_p95"]["a"], 90.0)
        self.assertIsNone(rows["fps_p95"]["b"])
        self.assertIsNone(rows["fps_p95"]["delta"])
        self.assertIsNone(rows["fps_p95"]["better"])

    def test_zero_baseline_does_not_divide_by_zero(self):
        rows = {r["field"]: r for r in benchmarkcard.diff_sessions(
            {"fps_avg": 0.0}, {"fps_avg": 10.0})}
        self.assertEqual(rows["fps_avg"]["delta"], 10.0)
        self.assertIsNone(rows["fps_avg"]["delta_pct"])


@unittest.skipUnless(_HAVE_CAIRO, "pycairo not installed")
class RenderPng(unittest.TestCase):
    def test_writes_a_valid_png(self):
        session = {
            "game": "Test Game", "started": "2026-08-29T12:00:00", "benchmark": True,
            "fps_avg": 88.4, "fps_1low": 61.2, "fps_01low": 40.0, "fps_p95": 95.0,
            "frametime_stutter_pct": 2.1, "cpu_temp_max": 78.0, "gpu_temp_max": 71.0,
        }
        with TemporaryDirectory() as d:
            path = os.path.join(d, "card.png")
            benchmarkcard.render_png(session, path)
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_missing_metrics_render_a_dash_not_a_crash(self):
        with TemporaryDirectory() as d:
            path = os.path.join(d, "card.png")
            benchmarkcard.render_png({"exe": "game.exe"}, path)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
