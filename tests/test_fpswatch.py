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

    def test_brief_dip_is_ignored(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            write_mangohud_csv(csv, [90.0] * 200)
            w.poll()
            # ~2 s of low FPS at the tail — under the 4 s persistence bar
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 10)
            self.assertIsNone(w.poll())
            # ...and it's back before it could ever have confirmed
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 10 + [90.0] * 40)
            self.assertIsNone(w.poll())

    def test_partial_recovery_is_not_a_recovery(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            write_mangohud_csv(csv, [90.0] * 200)
            w.poll()
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 40)
            self.assertEqual(w.poll().kind, "dip")
            # climbs to 30 FPS: clears the 22 floor but nowhere near 85 % of 90
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 40 + [30.0] * 40)
            self.assertIsNone(w.poll())
            # a real recovery
            write_mangohud_csv(csv, [90.0] * 200 + [12.0] * 40 + [30.0] * 40 + [85.0] * 40)
            self.assertEqual(w.poll().kind, "recovered")

    def test_repeated_sub_threshold_dips_do_not_spam(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            frame = [90.0] * 200
            write_mangohud_csv(csv, frame)
            w.poll()
            kinds = []
            for _ in range(6):
                frame = frame + [12.0] * 10          # 2 s deep
                write_mangohud_csv(csv, frame)
                e = w.poll()
                if e:
                    kinds.append(e.kind)
                frame = frame + [90.0] * 10          # 2 s healthy
                write_mangohud_csv(csv, frame)
                e = w.poll()
                if e:
                    kinds.append(e.kind)
            self.assertEqual(kinds, [])

    def test_not_rendering_window_is_not_a_dip(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            write_mangohud_csv(csv, [120.0] * 200)
            w.poll()
            write_mangohud_csv(csv, [120.0] * 200 + [1.0] * 60)  # alt-tabbed
            self.assertIsNone(w.poll())


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
