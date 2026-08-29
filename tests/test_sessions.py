import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC, write_mangohud_csv  # noqa: F401

from goblinmode import sessions


class CsvParsing(unittest.TestCase):
    def test_parse_csv_reads_fps_and_temps(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "log.csv"
            write_mangohud_csv(p, [60.0] * 10, cpu_temp=[70.0], gpu_temp=[65.0])
            fps, cpu, gpu = sessions._parse_csv(p)
            self.assertEqual(len(fps), 10)
            self.assertEqual(cpu, [70.0] * 10)
            self.assertEqual(gpu, [65.0] * 10)

    def test_parse_csv_ignores_garbage_and_out_of_range(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "log.csv"
            p.write_text("os\nx\nfps,elapsed\n60,1\nNaN,2\n-3,3\n9999,4\n72,5\n")
            fps, _, _ = sessions._parse_csv(p)
            self.assertEqual(fps, [60.0, 72.0])

    def test_percentile(self):
        s = list(range(1, 101))  # 1..100 sorted
        self.assertEqual(sessions._percentile(s, 0.0), 1)
        self.assertEqual(sessions._percentile(s, 1.0), 100)
        self.assertEqual(sessions._percentile(s, 0.5), 51)


class RegressionDetection(unittest.TestCase):
    def _summary(self, low, avg):
        return sessions.SessionSummary(
            exe="g", game="g", started="", ended="", duration_s=100,
            fps_1low=low, fps_avg=avg,
        )

    def test_flags_regression_below_baseline(self):
        prior = [{"fps_1low": 90, "fps_avg": 140} for _ in range(4)]
        reg = sessions._detect_regression(self._summary(60, 130), prior)
        self.assertIsNotNone(reg)
        self.assertEqual(reg.direction, "regression")
        self.assertLess(reg.change_pct, 0)

    def test_flags_improvement_above_baseline(self):
        prior = [{"fps_1low": 60, "fps_avg": 100} for _ in range(4)]
        reg = sessions._detect_regression(self._summary(80, 130), prior)
        self.assertEqual(reg.direction, "improvement")

    def test_stable_is_not_flagged(self):
        prior = [{"fps_1low": 90, "fps_avg": 140} for _ in range(4)]
        self.assertIsNone(sessions._detect_regression(self._summary(88, 138), prior))

    def test_needs_minimum_history(self):
        prior = [{"fps_1low": 90, "fps_avg": 140} for _ in range(2)]
        self.assertIsNone(sessions._detect_regression(self._summary(40, 80), prior))


class TrackerLifecycle(unittest.TestCase):
    def _patch_paths(self, d):
        sessions.SESSION_FILE = Path(d) / "sessions.jsonl"
        logs = Path(d) / "mangohud"
        logs.mkdir()
        sessions.MANGOHUD_LOG_DIR = logs
        return logs

    def test_short_session_not_recorded(self):
        with TemporaryDirectory() as d:
            self._patch_paths(d)
            t = sessions.SessionTracker()
            t.start("g", "Game", [])
            self.assertIsNone(t.end("g"))  # < 60 s

    def test_end_summarises_and_persists(self):
        with TemporaryDirectory() as d:
            logs = self._patch_paths(d)
            t = sessions.SessionTracker()
            t.start("Wow.exe", "WoW", ["governor", "tearing"])
            t._open["Wow.exe"].started_mono = time.monotonic() - 300
            write_mangohud_csv(logs / "wow_1.csv", [60.0] * 200 + [30.0] * 40,
                               cpu_temp=[80.0])
            result = t.end("Wow.exe")
            self.assertIsNotNone(result)
            summary, _reg = result
            self.assertEqual(summary.game, "WoW")
            self.assertEqual(summary.samples, 240)
            self.assertLess(summary.fps_1low, summary.fps_avg)
            self.assertEqual(summary.tweaks, ["governor", "tearing"])
            self.assertEqual(len(sessions._load_all()), 1)


if __name__ == "__main__":
    unittest.main()
