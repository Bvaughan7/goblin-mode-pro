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


def _write_unit_csv(path, rows, divisor, gap_at=None, gap_seconds=0.0):
    """A MangoHud-style CSV whose `elapsed` is in a chosen unit.

    `divisor` is units-per-second (1e9 = ns, 1e3 = ms, 1 = s). `gap_at` inserts
    a `gap_seconds` stall before that row index.
    """
    lines = ["os,cpu,gpu\n", "Linux,x,y\n", "fps,frametime,elapsed\n"]
    elapsed = 0.0
    for i, fps in enumerate(rows):
        if gap_at is not None and i == gap_at:
            elapsed += gap_seconds * divisor
        else:
            elapsed += 0.2 * divisor           # the 200 ms cadence we ask for
        lines.append(f"{fps},8.0,{elapsed:.0f}\n")
    path.write_text("".join(lines))


class ElapsedUnits(unittest.TestCase):
    """The `elapsed` unit is decided once per log, not per row."""

    def _watch(self, td):
        fpswatch.MANGOHUD_LOG_DIR = Path(td)
        return fpswatch.FpsWatcher()

    def test_unit_is_inferred_for_every_unit_mangohud_might_use(self):
        for name, divisor in (("s", 1.0), ("ms", 1e3), ("us", 1e6), ("ns", 1e9)):
            with self.subTest(unit=name), TemporaryDirectory() as td:
                w = self._watch(td)
                csv = Path(td) / "game_1.csv"
                _write_unit_csv(csv, [60.0] * 50, divisor)
                w.poll()
                self.assertEqual(w._unit_div, divisor)
                # 50 rows at 200 ms = ~10 s of virtual time, in every unit
                self.assertAlmostEqual(w._vclock, 9.8, delta=0.2)

    def test_a_long_stall_in_a_ms_log_advances_the_clock_by_its_real_length(self):
        """The regression: a 30 s gap read per-row looks like microseconds.

        delta = 30_000 (ms) trips the old `> 1e4` branch, is divided by 1e6 and
        advances the clock 0.03 s instead of 30 s - under-reporting the stall
        by 1000x, which is exactly the event the watchdog exists to catch.
        """
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            _write_unit_csv(csv, [60.0] * 40, 1e3, gap_at=20, gap_seconds=30.0)
            w.poll()
            self.assertEqual(w._unit_div, 1e3)
            # 39 steps: 38 x 0.2 s + one 30 s stall
            self.assertAlmostEqual(w._vclock, 38 * 0.2 + 30.0, delta=0.1)

    def test_a_fast_frame_in_an_ns_log_does_not_over_advance_the_clock(self):
        """The same bug the other way round, on this project's real unit.

        A 1 ms delta in a ns log is 1e6, which also trips `> 1e4` and reads as
        1.0 virtual second instead of 0.001 - a 1000x over-advance. This is the
        ambiguous case the frame rate has to break: 1e6 is equally a 1 s cadence
        in microseconds, and only "rows can't be closer than frames" says which.
        A MangoHud log written with `log_interval=0` logs every frame, so at
        ~900 fps the rows really are ~1 ms apart.
        """
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            lines = ["os,cpu,gpu\n", "Linux,x,y\n", "fps,frametime,elapsed\n"]
            for i in range(60):
                lines.append(f"900.0,1.1,{i * 1_000_000}\n")  # 1 ms rows, in ns
            csv.write_text("".join(lines))
            w.poll()
            self.assertEqual(w._unit_div, 1e9)
            self.assertAlmostEqual(w._vclock, 59 * 0.001, delta=0.005)

    def test_a_slow_log_is_not_mistaken_for_a_fast_one(self):
        """The mirror of the case above, and why the sub-frame floor exists.

        1e6 in a us log at 30 fps is a 1 s row cadence. Reading it as ns would
        mean rows 1 ms apart while frames are 33 ms apart - impossible.
        """
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            lines = ["os,cpu,gpu\n", "Linux,x,y\n", "fps,frametime,elapsed\n"]
            for i in range(30):
                lines.append(f"30.0,33.0,{i * 1_000_000}\n")  # 1 s rows, in us
            csv.write_text("".join(lines))
            w.poll()
            self.assertEqual(w._unit_div, 1e6)
            self.assertAlmostEqual(w._vclock, 29 * 1.0, delta=0.1)

    def test_a_named_unit_in_the_header_is_believed(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            csv.write_text(
                "os,cpu,gpu\nLinux,x,y\nfps,frametime,elapsed_ms\n"
                + "".join(f"60.0,8.0,{i * 200}\n" for i in range(20))
            )
            w.poll()
            self.assertEqual(w._unit_div, 1e3)
            self.assertAlmostEqual(w._vclock, 19 * 0.2, delta=0.05)

    def test_rotation_forgets_the_unit(self):
        with TemporaryDirectory() as td:
            w = self._watch(td)
            first = Path(td) / "game_1.csv"
            _write_unit_csv(first, [60.0] * 40, 1e3)
            w.poll()
            self.assertEqual(w._unit_div, 1e3)
            second = Path(td) / "game_2.csv"
            _write_unit_csv(second, [60.0] * 40, 1e9)
            w.poll()
            self.assertEqual(w._unit_div, 1e9)

    def test_a_short_log_still_settles_a_unit(self):
        """Fewer than _UNIT_SAMPLE_N deltas must not stall detection forever."""
        with TemporaryDirectory() as td:
            w = self._watch(td)
            csv = Path(td) / "game_1.csv"
            _write_unit_csv(csv, [60.0] * 5, 1e9)
            w.poll()
            self.assertEqual(w._unit_div, 1e9)
            self.assertEqual(len(w._hist), 5)


if __name__ == "__main__":
    unittest.main()
