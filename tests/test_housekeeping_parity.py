"""The Rust and Python pruners delete the same files.

This one deletes things. A port that read either ceiling slightly differently
would quietly remove somebody's session logs, and nothing downstream would
notice - the directory would simply be emptier than it should be.

The Python is driven for real: files are created with the sizes and mtimes the
case describes, `prune` runs against them, and what survives on disk is
compared with what the port says should. Faking the filesystem here would test
the part that was never in doubt.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC  # noqa: F401

from goblinmode import housekeeping

_REPO = Path(__file__).resolve().parent.parent

#: (name, mtime, size) per file, newest last in this listing.
CASES = [
    # Comfortably inside both ceilings.
    ([("a", 1, 10), ("b", 2, 10), ("c", 3, 10)], 40, 1000),
    # Too many.
    ([(f"f{i}", i, 1) for i in range(6)], 3, 1000),
    # Too much.
    ([(f"f{i}", i, 100) for i in range(5)], 40, 250),
    # Exactly on the budget, and one byte over it.
    ([(f"f{i}", i, 100) for i in range(5)], 40, 500),
    ([(f"f{i}", i, 100) for i in range(5)], 40, 499),
    # One file larger than the whole budget, and it is the newest.
    ([("small", 1, 1), ("huge", 2, 10_000)], 40, 5_000),
    # A tiny file behind a huge one is not rescued by being tiny.
    ([("tiny", 1, 1), ("huge", 2, 10_000), ("new", 3, 10)], 40, 100),
    # Nothing at all.
    ([], 40, 100),
    # Keep nothing.
    ([("a", 1, 1), ("b", 2, 1)], 0, 1_000_000),
    # Empty files, so only the count can decide.
    ([(f"f{i}", i, 0) for i in range(5)], 2, 1_000),
    # A single file, empty, with a budget of nothing.
    ([("only", 1, 0)], 40, 0),
    # Real-ish: forty-one logs of a few kilobytes.
    ([(f"g{i}", i, 4096) for i in range(41)], 40, 500 * 1024 * 1024),
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_HOUSEKEEPING_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "housekeeping"
        if candidate.exists():
            return candidate
    return None


def _python_survivors(files, keep_newest, max_bytes) -> set:
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name, mtime, size in files:
            path = directory / f"{name}.log"
            path.write_bytes(b"x" * size)
            os.utime(path, (float(mtime), float(mtime)))
        housekeeping.prune(directory, keep_newest=keep_newest,
                           max_bytes=max_bytes, pattern="*.log")
        return {p.stem for p in directory.iterdir()}


class BothImplementationsDeleteTheSameFiles(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the housekeeping "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example housekeeping`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example housekeeping`")

    def _rust_doomed(self, files, keep_newest, max_bytes) -> set:
        payload = {"files": [{"name": n, "mtime": float(m), "size": s}
                             for n, m, s in files],
                   "keep_newest": keep_newest, "max_bytes": max_bytes}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return set(json.loads(r.stdout))

    def test_every_case_leaves_the_same_files_behind(self):
        for files, keep_newest, max_bytes in CASES:
            with self.subTest(files=len(files), keep=keep_newest,
                              budget=max_bytes):
                doomed = self._rust_doomed(files, keep_newest, max_bytes)
                survivors = {n for n, _, _ in files} - doomed
                self.assertEqual(
                    _python_survivors(files, keep_newest, max_bytes),
                    survivors)

    def test_a_subdirectory_is_never_touched(self):
        """The Python checks `is_file` and the port is handed a list, so this
        is the caller's rule rather than the decision's - pinned here because
        it is the one that would delete something irreplaceable."""
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "keep.log").write_bytes(b"x" * 10_000)
            nested = directory / "nested.log"
            nested.mkdir()
            (nested / "inside").write_text("data")
            housekeeping.prune(directory, keep_newest=0, max_bytes=0,
                               pattern="*.log")
            self.assertTrue(nested.is_dir())
            self.assertTrue((nested / "inside").exists())
            self.assertFalse((directory / "keep.log").exists())

    def test_a_directory_that_cannot_be_read_is_not_an_error(self):
        """Called on daemon start; a missing log directory is the ordinary
        state of a fresh install, not a reason to fail."""
        self.assertEqual(
            housekeeping.prune(Path("/nonexistent/goblin-mode-pro")), 0)


if __name__ == "__main__":
    unittest.main()
