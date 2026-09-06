"""The Rust exit plan and what the Python daemon actually does agree.

The Python side of this is not a function - it is `Daemon._on_game_event`,
which acts rather than answers. So it is recorded at the seams where it acts:
the pid it forgets, the call it makes on the payload, the timers it arms with
their delays, the thread it starts, the notification it sends and the status it
broadcasts. What comes back is an ordered list in the same vocabulary the Rust
plan is rendered in, and the two are compared whole - order included, because
the order is most of what this code decides.

The corpus is every combination of the six things the daemon knows about
itself at that moment - 64 of them. Exhaustive rather than representative
because the interesting cases are the interactions between flags (a forced
boost with a clip running and a dip seen, and nothing else left playing), and
those are exactly the ones a hand-written corpus leaves out.
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

FLAGS = ("others_running", "fps_dip_seen", "gpu_available",
         "forced_boost", "clip_running", "boost_announced")

EXE = "Wow.exe"
GAME = "World of Warcraft"


def _binary() -> Path | None:
    override = os.environ.get("GMP_GAMEEVENT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "gameevent"
        if candidate.exists():
            return candidate
    return None


class _Pids(dict):
    """The daemon's live-pid table, which records the one thing done to it."""

    def __init__(self, log):
        super().__init__({EXE: 4242})
        self._log = log

    def pop(self, key, *default):
        self._log.append(["ForgetPid", [key]])
        return super().pop(key, *default)


class _Payload:
    def __init__(self, log):
        self._log = log

    def revert(self, profile):
        self._log.append(["Revert", [profile.exe]])


class _Clip:
    def __init__(self, log, running):
        self._log = log
        self._running = running

    def running(self):
        return self._running

    def stop(self):
        self._log.append(["StopClip", []])


class _Observer:
    def __init__(self, active_exes):
        self.active_exes = active_exes


class _Event:
    running = False

    def __init__(self, profile):
        self.profile = profile
        self.pid = 0
        self.candidate = None


def _python_plan(state: dict) -> list:
    """Drive the real `_on_game_event` and record what it does, in order."""
    log: list = []
    profile = config.new_profile(EXE, GAME)

    daemon = Daemon.__new__(Daemon)
    daemon._active_pids = _Pids(log)
    daemon.payload = _Payload(log)
    daemon.clip = _Clip(log, state["clip_running"])
    daemon.observer = _Observer(["other"] if state["others_running"] else [])
    daemon._fps_dip_seen = state["fps_dip_seen"]
    daemon._forced_boost = state["forced_boost"]
    daemon._boost_announced = state["boost_announced"]
    daemon._stop_diagnostics = lambda: log.append(["StopDiagnostics", []])
    daemon._broadcast_status = lambda: log.append(["BroadcastStatus", []])

    def notify(title, body="", **kw):
        # The only notification this path can send. Recorded by what it means
        # rather than by its words, which are translated.
        log.append(["AnnounceBoostOff", []])

    daemon._notify = notify

    def timeout_add_seconds(seconds, func, *args):
        name = ("FinishSessionIn" if func.__name__ == "_finish_session"
                else "FpsPostMortemIn")
        log.append([name, [seconds, *args]])
        return 1

    class _Thread:
        def __init__(self, target=None, **kw):
            self._target = target

        def start(self):
            self._target()

    with patch("goblinmode.daemon.GLib.timeout_add_seconds", timeout_add_seconds), \
            patch("goblinmode.daemon.threading.Thread", _Thread), \
            patch("goblinmode.daemon.gpu.available",
                  lambda: state["gpu_available"]):
        daemon._on_game_event(_Event(profile))

    return log, daemon._boost_announced


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the gameevent example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example gameevent`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example gameevent`")

    def _rust(self, state: dict) -> list:
        payload = {"exe": EXE, "game": GAME, "state": state}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _states(self):
        for values in itertools.product((False, True), repeat=len(FLAGS)):
            yield dict(zip(FLAGS, values, strict=True))

    def test_every_combination_of_what_the_daemon_knows_agrees(self):
        for state in self._states():
            with self.subTest(**state):
                want, _ = _python_plan(state)
                self.assertEqual(self._rust(state), want)

    def test_the_boost_flag_is_cleared_exactly_when_it_is_announced_off(self):
        """The plan says to announce; the flag is what stops it announcing
        twice. They are the same decision and must not drift apart."""
        for state in self._states():
            with self.subTest(**state):
                _, still_announced = _python_plan(state)
                announced_off = ["AnnounceBoostOff", []] in self._rust(state)
                self.assertEqual(
                    still_announced,
                    state["boost_announced"] and not announced_off,
                    "the flag and the plan disagree about whether the boost "
                    "was announced off",
                )


if __name__ == "__main__":
    unittest.main()
