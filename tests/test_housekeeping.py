"""Log-directory pruning: oldest-first, capped by count and by total bytes."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC  # noqa: F401

from goblinmode import housekeeping


def _make(dir_: Path, name: str, size: int, age_s: float) -> Path:
    p = dir_ / name
    p.write_bytes(b"x" * size)
    when = time.time() - age_s
    os.utime(p, (when, when))
    return p


class Prune(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_keeps_the_newest_n_by_count(self):
        for i in range(10):
            _make(self.d, f"{i}.log", 10, age_s=i * 100)  # 0 newest, 9 oldest
        removed = housekeeping.prune(self.d, keep_newest=4, max_bytes=10**9,
                                     pattern="*.log")
        self.assertEqual(removed, 6)
        left = sorted(p.name for p in self.d.glob("*.log"))
        self.assertEqual(left, ["0.log", "1.log", "2.log", "3.log"])

    def test_enforces_the_byte_ceiling_before_the_count(self):
        for i in range(10):
            _make(self.d, f"{i}.log", 100, age_s=i * 100)
        # budget only fits ~3 files
        housekeeping.prune(self.d, keep_newest=40, max_bytes=350, pattern="*.log")
        left = sorted(p.name for p in self.d.glob("*.log"))
        self.assertEqual(left, ["0.log", "1.log", "2.log"])

    def test_ignores_non_matching_files_and_subdirs(self):
        _make(self.d, "keep.csv", 10, age_s=0)
        (self.d / "sub").mkdir()
        _make(self.d / "sub", "nested.log", 10, age_s=999)
        for i in range(5):
            _make(self.d, f"{i}.log", 10, age_s=i * 100)
        housekeeping.prune(self.d, keep_newest=1, max_bytes=10**9, pattern="*.log")
        self.assertTrue((self.d / "keep.csv").exists())
        self.assertTrue((self.d / "sub" / "nested.log").exists())
        self.assertEqual(len(list(self.d.glob("*.log"))), 1)

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(housekeeping.prune(self.d / "nope"), 0)


if __name__ == "__main__":
    unittest.main()
