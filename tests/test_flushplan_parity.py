"""The Rust flush plan and what the Python daemon actually redoes agree.

A profile edit is saved 400 ms after the last keystroke, and `_flush_profiles`
is the work that follows the save. Three rules, and the middle one is the
interesting one: MangoHud's config file is rewritten when the OVERLAY is on or
the WATCHDOG is. The watchdog draws nothing - it reads MangoHud's log - so a
rewrite that checked only the overlay would leave it with nothing to read and
no error to explain it.

`_dirty_profiles` is a `set`, so the Python's iteration order is arbitrary.
Both sides are compared grouped by executable: the order profiles are handled
in cannot be pinned because it is not defined, but the order of the steps
WITHIN one profile can be, and that is where the meaning is.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import config
from goblinmode.daemon import Daemon

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_FLUSHPLAN_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "flushplan"
        if candidate.exists():
            return candidate
    return None


def _grouped(log: list) -> dict:
    """Steps by executable. The order between profiles is not defined; the
    order within one is."""
    out: dict = {}
    for name, args in log:
        out.setdefault(args[0], []).append(name)
    return out


def _python_plan(profiles: list, dirty: list, active: list) -> dict:
    log: list = []

    class _FpsWatch:
        def update(self, floor, ratio):
            log.append(["RetuneWatcher", [self.current]])

    class _Payload:
        def reapply(self, profile):
            log.append(["Reapply", [profile.exe]])

    watcher = _FpsWatch()
    daemon = Daemon.__new__(Daemon)
    daemon.settings = config.Settings(
        profiles=[config.GameProfile(**p) for p in profiles])
    daemon._dirty_profiles = set(dirty)
    daemon._save_source_id = object()
    daemon.fpswatch = watcher
    daemon.payload = _Payload()
    daemon.observer = type("_O", (), {"update_settings": lambda self, s: None})()
    daemon._active_pids = {exe: 1 for exe in active}

    def mangohud_apply(profile):
        log.append(["WriteMangoHud", [profile.exe]])

    # The watcher is told two numbers and not which profile they came from,
    # so it is flushed one executable at a time and told which one that is.
    # The comparison groups by executable, which makes that lossless: the
    # order profiles are handled in is a set's order and is not defined.
    with patch("goblinmode.config.save"), \
            patch("goblinmode.mangohud.apply", mangohud_apply):
        for exe in sorted(dirty):
            daemon._dirty_profiles = {exe}
            daemon._save_source_id = object()
            watcher.current = exe
            daemon._flush_profiles()
    return _grouped(log)


PROFILES = [
    {"exe": "a", "fps_watchdog": False, "mangohud": {"enabled": False}},
    {"exe": "b", "fps_watchdog": True, "mangohud": {"enabled": False}},
    {"exe": "c", "fps_watchdog": False, "mangohud": {"enabled": True}},
    {"exe": "d", "fps_watchdog": True, "mangohud": {"enabled": True}},
]


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the flushplan example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example flushplan`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example flushplan`")

    def _rust(self, profiles, dirty, active) -> dict:
        payload = {"settings": {"profiles": profiles},
                   "dirty": list(dirty), "active": list(active)}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return _grouped(json.loads(r.stdout))

    def test_every_combination_of_edited_and_running_agrees(self):
        names = [p["exe"] for p in PROFILES]
        for running in itertools.chain.from_iterable(
                itertools.combinations(names, n) for n in range(len(names) + 1)):
            with self.subTest(running=running):
                self.assertEqual(
                    self._rust(PROFILES, names, running),
                    _python_plan(PROFILES, names, running))

    def test_a_profile_deleted_since_it_was_edited_is_skipped(self):
        """The edit and the flush are 400 ms apart; a removal can land in
        between and nothing is redone for what is no longer there."""
        self.assertEqual(
            self._rust(PROFILES, ["a", "gone"], ["a", "gone"]),
            _python_plan(PROFILES, ["a", "gone"], ["a", "gone"]))
        self.assertEqual(self._rust(PROFILES, ["gone"], []), {})


if __name__ == "__main__":
    unittest.main()
