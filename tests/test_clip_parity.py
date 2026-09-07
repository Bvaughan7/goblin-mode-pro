"""The Rust and Python replay buffers agree.

Three small things, all of which fail silently when they drift. A wrong flag
makes a recorder that starts and records nothing. A missing debounce makes one
clip per sample of a stuttering game. Picking the wrong new file hands somebody
a clip of something else.

The Python is driven for real where it can be: the command line is read off a
patched `Popen`, and the file picking off a temporary directory with real
files and real timestamps.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import clip

_REPO = Path(__file__).resolve().parent.parent

OUT_DIR = "/home/tester/Videos/Goblin Mode Pro"

SAVES = [
    {"running": running, "last_save": last, "now": now}
    for running, (last, now) in itertools.product(
        (False, True),
        [(None, 0.0), (None, 1.0), (None, 1e9),
         (100.0, 100.0), (100.0, 110.0), (100.0, 119.9), (100.0, 120.0),
         (100.0, 120.1), (100.0, 200.0), (100.0, 99.0)],
    )
]

#: (files already there, [(name, mtime) after])
PICKS = [
    ([], []),
    (["a.mp4"], [("a.mp4", 1.0)]),
    ([], [("only.mp4", 1.0)]),
    ([], [("old.mp4", 1.0), ("new.mp4", 3.0), ("mid.mp4", 2.0)]),
    (["old.mp4"], [("old.mp4", 9.0), ("new.mp4", 1.0)]),
    (["a.mp4", "b.mp4"], [("a.mp4", 1.0), ("b.mp4", 2.0), ("c.mp4", 3.0)]),
]

#: Two new files sharing a timestamp. The Python sorts a SET here, so its
#: answer depends on string hashing and differs between runs - there is
#: nothing to pin, only a range to stay inside.
TIED = ([], [("x.mp4", 5.0), ("y.mp4", 5.0)])


def _binary() -> Path | None:
    override = os.environ.get("GMP_CLIP_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "clip"
        if candidate.exists():
            return candidate
    return None


def _python_argv(out_dir: str) -> list:
    """Read the command line off the real `start`, through a patched Popen."""
    seen: list = []

    class _Popen:
        def __init__(self, argv, **kw):
            seen.append(list(argv))

        def poll(self):
            return None

    buffer = clip.ClipBuffer()
    with patch.object(clip, "_OUT_DIR", Path(out_dir)), \
            patch.object(clip.shutil, "which", lambda _t: "/usr/bin/" + _t), \
            patch.object(clip.subprocess, "Popen", _Popen), \
            patch.object(Path, "mkdir", lambda *a, **k: None):
        buffer.start()
    return seen[0]


def _python_may_save(running: bool, last_save, now: float) -> str:
    """What `save` would decide, read off whether it signals."""
    signalled: list = []

    class _Proc:
        def poll(self):
            return None if running else 0

        def send_signal(self, sig):
            signalled.append(sig)

    buffer = clip.ClipBuffer()
    buffer._proc = _Proc() if running else None
    # `save` compares against `_last_save`, which starts at 0.0 - the module
    # treats that as "never" only because nothing has run yet, so a case that
    # means never is expressed by putting the clock far ahead of it.
    buffer._last_save = 0.0 if last_save is None else last_save
    shifted_now = now if last_save is not None else now + 1e6

    with TemporaryDirectory() as tmp, \
            patch.object(clip, "_OUT_DIR", Path(tmp)), \
            patch.object(clip.time, "monotonic", lambda: shifted_now), \
            patch.object(clip.time, "sleep", lambda _s: None):
        buffer.save()
    if not running:
        return "not_running"
    return "flush" if signalled else "too_soon"


def _python_pick(before, after):
    """Which file `save` reports, off a real directory."""
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name in before:
            (directory / name).write_bytes(b"")
        existing = set(directory.glob("*.mp4"))
        for name, mtime in after:
            path = directory / name
            path.write_bytes(b"")
            os.utime(path, (mtime, mtime))
        now = set(directory.glob("*.mp4"))
        new = now - existing
        if not new:
            return None
        return sorted(new, key=os.path.getmtime)[-1].name


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the clip example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example clip`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example clip`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_command_line_matches(self):
        """Every flag, in order. `-r` is what makes it a replay buffer and
        `-ro` is where a flush lands; a recorder without them starts happily
        and produces nothing."""
        self.assertEqual(self._rust({"out_dir": OUT_DIR})["argv"],
                         _python_argv(OUT_DIR))

    def test_every_save_is_allowed_or_refused_the_same(self):
        got = self._rust({"saves": SAVES})["saves"]
        for case, answer in zip(SAVES, got, strict=True):
            with self.subTest(**case):
                self.assertEqual(
                    answer,
                    _python_may_save(case["running"], case["last_save"],
                                     case["now"]))

    def test_a_tie_picks_one_of_the_tied_files_on_both_sides(self):
        """Asserted as a range rather than a value. The Python sorts a set of
        paths, whose iteration order depends on string hashing - randomised
        per process - so the same two files pick differently between runs.
        The port answers deterministically; what both must do is pick one of
        the two, and never something else."""
        before, after = TIED
        names = {name for name, _ in after}
        payload = [{"before": before, "after": [list(a) for a in after]}]
        self.assertIn(self._rust({"picks": payload})["picks"][0], names)
        self.assertIn(_python_pick(before, after), names)

    def test_the_same_new_file_is_picked(self):
        payload = [{"before": before, "after": [list(a) for a in after]}
                   for before, after in PICKS]
        got = self._rust({"picks": payload})["picks"]
        for (before, after), answer in zip(PICKS, got, strict=True):
            with self.subTest(before=before, after=after):
                self.assertEqual(answer, _python_pick(before, after))


if __name__ == "__main__":
    unittest.main()
