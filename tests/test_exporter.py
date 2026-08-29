import os
import tempfile
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import exporter

_STATUS = {
    "master_enabled": True,
    "active_games": ["game.exe"],
    "forced_boost": False,
    "helper_available": True,
    "limited_mode": False,
    "health": {"score": 8.5},
    "latest_sample": {"cpu_temp": 55.0, "cpu_load": 42.0, "pkg_power_w": 65.0,
                       "gpu_load": 80.0, "gpu_temp": 70.0},
    "gpu": {"vram_used_mb": 4000},
    "fps": {"fps_avg": 90.0, "fps_min": 60.0, "fps_1low": 65.0},
}


class Render(unittest.TestCase):
    def test_includes_expected_metrics(self):
        text = exporter.render(_STATUS)
        self.assertIn("goblin_mode_pro_master_enabled 1", text)
        self.assertIn("goblin_mode_pro_boosting 1", text)
        self.assertIn("goblin_mode_pro_health_score 8.5", text)
        self.assertIn("goblin_mode_pro_cpu_temp_celsius 55", text)
        self.assertIn("goblin_mode_pro_fps_avg 90", text)
        self.assertIn("# HELP goblin_mode_pro_fps_avg", text)
        self.assertIn("# TYPE goblin_mode_pro_fps_avg gauge", text)

    def test_missing_data_is_omitted_not_zero(self):
        text = exporter.render({})
        self.assertNotIn("goblin_mode_pro_health_score", text)
        self.assertNotIn("goblin_mode_pro_cpu_temp_celsius", text)
        # booleans still render (master_enabled defaults True when absent)
        self.assertIn("goblin_mode_pro_master_enabled 1", text)

    def test_idle_no_games_not_boosting(self):
        text = exporter.render({**_STATUS, "active_games": [], "forced_boost": False})
        self.assertIn("goblin_mode_pro_boosting 0", text)
        self.assertIn("goblin_mode_pro_active_games 0", text)


class WriteTextfile(unittest.TestCase):
    def test_atomic_write_creates_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "goblin-mode-pro.prom")
            exporter.write_textfile(path, _STATUS)
            text = Path(path).read_text()
            self.assertIn("goblin_mode_pro_master_enabled", text)
            # no leftover temp files
            self.assertEqual(os.listdir(d), ["goblin-mode-pro.prom"])

    def test_bad_path_does_not_raise(self):
        exporter.write_textfile("/nonexistent-dir/x/y.prom", _STATUS)


class ExporterThrottle(unittest.TestCase):
    def test_maybe_write_respects_min_interval(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.prom")
            exp = exporter.Exporter(path, min_interval=999)
            exp.maybe_write(_STATUS)
            self.assertTrue(os.path.exists(path))
            os.remove(path)
            exp.maybe_write(_STATUS)  # too soon, should skip
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
