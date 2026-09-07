"""The Rust recompute plan and what the Python payload does agree.

`_recompute_global` is where the order of everything is decided: the
privileged helper, then the display tweaks, then the kernel scheduler, then
focus mode. `_restore_global` is a DIFFERENT order - the scheduler goes back
first, because it is loaded for the whole machine rather than for the game.

Two things this pins that nothing else does. That order, and the fact that the
display tweaks are EDGE-triggered where the helper's are not: the helper is
re-asked every recompute so a changed profile set is picked up, and the
compositor is asked only on the transition. Which has a consequence the corpus
covers on purpose - a second game asking for a different refresh cap gets the
first game's, because the trigger is "is one applied", not "is this one".

The helper's individual calls are collapsed to one marker on both sides. They
have their own harness, against the seam where the Python records them; this
one is about where that half sits relative to the others.
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
from goblinmode.payload import PerformancePayload

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_RECOMPUTE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "recompute"
        if candidate.exists():
            return candidate
    return None


PROFILES = {
    "quiet": {"exe": "quiet", "governor_boost": False, "tearing_enabled": False,
              "adaptive_sync_enabled": False, "focus_mode": False,
              "refresh_rate_hz": 0},
    "display": {"exe": "display", "governor_boost": False,
                "tearing_enabled": True, "adaptive_sync_enabled": True,
                "vrr_outputs": ["DP-1"], "refresh_rate_hz": 60,
                "focus_mode": True},
    "display2": {"exe": "display2", "governor_boost": False,
                 "tearing_enabled": True, "adaptive_sync_enabled": True,
                 "vrr_outputs": ["HDMI-A-1"], "refresh_rate_hz": 40,
                 "focus_mode": True},
    "boost": {"exe": "boost", "governor_boost": True, "tearing_enabled": False,
              "adaptive_sync_enabled": False, "focus_mode": False,
              "refresh_rate_hz": 0},
    "sched": {"exe": "sched", "governor_boost": False, "tearing_enabled": False,
              "adaptive_sync_enabled": False, "focus_mode": False,
              "refresh_rate_hz": 0, "scx_scheduler": "lavd",
              "scx_mode": "gaming"},
    "vrr-all": {"exe": "vrr-all", "governor_boost": False,
                "tearing_enabled": False, "adaptive_sync_enabled": True,
                "vrr_outputs": [], "focus_mode": False, "refresh_rate_hz": 0},
}

STATES = [
    {},
    {"tearing": True},
    {"tearing": True, "adaptive_sync": True, "refresh_cap": True,
     "focus_mode": True},
    {"helper": True},
    {"scx": "lavd"},
    {"scx": "bpfland"},
    {"helper": True, "tearing": True, "adaptive_sync": True,
     "refresh_cap": True, "focus_mode": True, "scx": "lavd"},
]


class _Scx:
    def __init__(self, log):
        self._log = log
        self._asked = False

    def current(self):
        self._asked = True
        return "previous"

    def switch(self, scheduler, mode):
        self._log.append(["ScxSwitch", [scheduler, mode, self._asked]])
        self._asked = False
        return True

    def restore(self, _previous):
        self._log.append(["ScxRestore", []])


class _Compositor:
    def __init__(self, log):
        self._log = log

    def enable_tearing(self):
        self._log.append(["EnableTearing", []])
        return True

    def restore_tearing(self):
        self._log.append(["RestoreTearing", []])

    def enable_adaptive_sync(self, outputs=None):
        self._log.append(["EnableAdaptiveSync", [outputs]])
        return True

    def restore_adaptive_sync(self):
        self._log.append(["RestoreAdaptiveSync", []])

    def enable_refresh_cap(self, hz):
        self._log.append(["EnableRefreshCap", [hz]])
        return True

    def restore_refresh_cap(self):
        self._log.append(["RestoreRefreshCap", []])


class _Focus:
    def __init__(self, log):
        self._log = log

    def enter(self):
        self._log.append(["EnterFocus", []])

    def exit(self):
        self._log.append(["ExitFocus", []])


def _payload(log, names, state):
    p = PerformancePayload.__new__(PerformancePayload)
    p._active = {n: config.GameProfile(**PROFILES[n]) for n in names}
    p.compositor = _Compositor(log)
    p.focus = _Focus(log)
    p.scx = _Scx(log)
    p._helper_tweaks_applied = state.get("helper", False)
    p._tearing_applied = state.get("tearing", False)
    p._vrr_applied = state.get("adaptive_sync", False)
    p._refresh_cap_applied = state.get("refresh_cap", False)
    p._focus_applied = state.get("focus_mode", False)
    p._scx_applied = state.get("scx")
    p._scx_previous = None
    p._apply_helper_tweaks = lambda *a: log.append(["Helper", []])
    p._restore_helper_tweaks = lambda: log.append(["HelperRestore", []])
    p._incident = lambda kind, detail: None
    return p


def _python(which, names, state) -> list:
    log: list = []
    p = _payload(log, names, state)
    with patch("goblinmode.capabilities.on_ac_power", lambda: True), \
            patch("goblinmode.capabilities.detect", lambda: {"tdp_control": "rapl"}):
        if which == "restore":
            p._restore_global()
        else:
            p._recompute_global()
    return log


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the recompute example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example recompute`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example recompute`")

    def _rust(self, which, names, state) -> list:
        payload = {"which": which,
                   "profiles": [PROFILES[n] for n in names],
                   "on_battery": False, "tdp_backend": "rapl",
                   "state": state}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _sets(self):
        names = sorted(PROFILES)
        for size in range(0, 3):
            yield from itertools.combinations(names, size)

    def test_every_recompute_agrees(self):
        for names in self._sets():
            for state in STATES:
                with self.subTest(active=names, state=tuple(sorted(state))):
                    self.assertEqual(self._rust("recompute", names, state),
                                     _python("recompute", names, state))

    def test_every_restore_agrees(self):
        for state in STATES:
            with self.subTest(state=tuple(sorted(state))):
                self.assertEqual(self._rust("restore", [], state),
                                 _python("restore", [], state))

    def test_a_second_game_does_not_move_a_cap_that_is_already_set(self):
        """Named on its own because it is the surprising one, and because a
        future change to it should fail a test that says what it was."""
        state = {"tearing": True, "adaptive_sync": True, "refresh_cap": True,
                 "focus_mode": True}
        got = self._rust("recompute", ("display", "display2"), state)
        self.assertEqual(got, _python("recompute", ("display", "display2"), state))
        self.assertFalse([s for s in got if s[0] == "EnableRefreshCap"])


if __name__ == "__main__":
    unittest.main()
