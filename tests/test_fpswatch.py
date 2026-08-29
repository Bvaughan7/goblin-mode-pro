import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC, write_mangohud_csv  # noqa: F401

from goblinmode import fpswatch


class DipDetection(unittest.TestCase):
    def _watch(self, td):
        fpswatch.MANGOHUD_LOG_DIR = Path(td)
        return fpswatch.FpsWatcher(dip_floor=22.0, dip_ratio=0.5)

    def test_sustained_low_fps_raises_a_dip_then_recovers(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            write_mangohud_csv(csv, [90.0] * 200)          # healthy baseline
            self.assertIsNone(w.poll())
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 60)  # cliff
            ev = w.poll()
            self.assertIsNotNone(ev)
            self.assertEqual(ev.kind, "dip")
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 60 + [88.0] * 60)
            rec = w.poll()
            self.assertIsNotNone(rec)
            self.assertEqual(rec.kind, "recovered")


class ReadCap(unittest.TestCase):
    def test_huge_backlog_is_capped_not_read_whole(self):
        with TemporaryDirectory() as td:
            fpswatch.MANGOHUD_LOG_DIR = Path(td)
            w = fpswatch.FpsWatcher()
            w._MAX_READ = 4096
            csv = Path(td) / "game_1.csv"
            # ~2000 rows -> well over 4 KB
            write_mangohud_csv(csv, [60.0] * 2000)
            w.poll()
            # it advanced to (near) EOF without reading the entire file in one gulp
            self.assertGreater(w._pos, 0)
            self.assertLessEqual(csv.stat().st_size - w._pos, w._MAX_READ + 4096)

    def test_truncation_resets_position(self):
        with TemporaryDirectory() as td:
            fpswatch.MANGOHUD_LOG_DIR = Path(td)
            w = fpswatch.FpsWatcher()
            csv = Path(td) / "game_1.csv"
            write_mangohud_csv(csv, [60.0] * 100)
            w.poll()
            far = w._pos
            write_mangohud_csv(csv, [60.0] * 5)   # smaller file
            w.poll()
            self.assertLess(w._pos, far)


if __name__ == "__main__":
    unittest.main()
