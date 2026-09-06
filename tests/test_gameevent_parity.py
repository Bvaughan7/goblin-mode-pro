"""The Rust game-event plans and what the Python daemon actually does agree.

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
PID = 4242


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
    """The daemon's live-pid table, which records what is done to it."""

    def __init__(self, log):
        super().__init__({EXE: PID})
        self._log = log

    def pop(self, key, *default):
        self._log.append(["ForgetPid", [key]])
        return super().pop(key, *default)

    def __setitem__(self, key, value):
        self._log.append(["RememberPid", [key, value]])
        super().__setitem__(key, value)


class _Payload:
    def __init__(self, log):
        self._log = log

    def revert(self, profile):
        self._log.append(["Revert", [profile.exe]])

    def apply(self, profile, pid):
        self._log.append(["Apply", [profile.exe, pid or 0]])


class _Sessions:
    def __init__(self, log):
        self._log = log

    def start(self, exe, game, tweaks):
        self._log.append(["StartSession", [exe, game, list(tweaks)]])


class _Clip:
    def __init__(self, log, running):
        self._log = log
        self._running = running

    def running(self):
        return self._running

    def stop(self):
        self._log.append(["StopClip", []])

    def start(self):
        self._log.append(["StartClip", []])


class _Observer:
    def __init__(self, active_exes):
        self.active_exes = active_exes


class _Event:
    def __init__(self, profile, *, running=False, pid=0, candidate=None):
        self.profile = profile
        self.running = running
        self.pid = pid
        self.candidate = candidate


class _Thread:
    """`threading.Thread`, run inline so the order it starts work in is the
    order this records. What the daemon puts on a thread is what must not
    block the loop, not what may happen in any order."""

    def __init__(self, target=None, args=(), **kw):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _daemon(log, state):
    """A `Daemon` with nothing wired up but the collaborators this path
    touches, each one recording what it was asked to do."""
    daemon = Daemon.__new__(Daemon)
    daemon._active_pids = _Pids(log)
    daemon.payload = _Payload(log)
    daemon.sessions = _Sessions(log)
    daemon.clip = _Clip(log, state.get("clip_running", False))
    daemon.observer = _Observer(["other"] if state.get("others_running") else [])
    daemon._fps_dip_seen = state.get("fps_dip_seen", False)
    daemon._forced_boost = state.get("forced_boost", False)
    daemon._boost_announced = state.get("boost_announced", False)
    daemon._stop_diagnostics = lambda: log.append(["StopDiagnostics", []])
    daemon._ensure_diagnostics_running = lambda: log.append(
        ["EnsureDiagnostics", []])
    daemon._broadcast_status = lambda: log.append(["BroadcastStatus", []])
    daemon._prewarm_shaders = lambda app_id: log.append(
        ["PrewarmShaders", [app_id]])
    return daemon


def _timers(log):
    def timeout_add_seconds(seconds, func, *args):
        name = ("FinishSessionIn" if func.__name__ == "_finish_session"
                else "FpsPostMortemIn")
        log.append([name, [seconds, *args]])
        return 1
    return timeout_add_seconds


def _python_exit(state: dict) -> tuple[list, bool]:
    """Drive the real `_on_game_event` for an exit and record what it does."""
    log: list = []
    daemon = _daemon(log, state)

    def notify(title, body="", **kw):
        # The only notification this path can send. Recorded by what it means
        # rather than by its words - which the exit plan does not carry,
        # because there is only ever the one message.
        log.append(["AnnounceBoostOff", []])

    daemon._notify = notify

    with patch("goblinmode.daemon.GLib.timeout_add_seconds", _timers(log)), \
            patch("goblinmode.daemon.threading.Thread", _Thread), \
            patch("goblinmode.daemon.gpu.available",
                  lambda: state["gpu_available"]):
        daemon._on_game_event(_Event(config.new_profile(EXE, GAME)))

    return log, daemon._boost_announced


def _python_launch(case: dict) -> tuple[list, bool]:
    """Drive the real `_on_game_event` for a launch and record what it does."""
    log: list = []
    state = {"boost_announced": case["boost_announced"]}
    daemon = _daemon(log, state)
    daemon._tweaks_fingerprint = lambda: list(case["tweaks"])

    def notify(title, body="", **kw):
        # Carried through in full: the body is built from the fingerprint, so
        # the words are a result rather than a fixed string.
        log.append(["AnnounceBoostOn", [title, body]])

    daemon._notify = notify

    profile = config.new_profile(EXE, GAME)
    profile.clip_on_incident = case["clip_on_incident"]
    profile.steam_app_id = case["steam_app_id"]

    with patch("goblinmode.daemon.GLib.timeout_add_seconds", _timers(log)), \
            patch("goblinmode.daemon.threading.Thread", _Thread):
        daemon._on_game_event(_Event(profile, running=True, pid=PID))

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
                want, _ = _python_exit(state)
                self.assertEqual(self._rust(state), want)

    def _rust_launch(self, case: dict) -> list:
        payload = {
            "kind": "launch", "exe": EXE, "game": GAME, "pid": PID,
            "tweaks": case["tweaks"],
            "clip_on_incident": case["clip_on_incident"],
            "steam_app_id": case["steam_app_id"],
            "state": {"boost_announced": case["boost_announced"]},
        }
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _launch_cases(self):
        for tweaks, announced, clip, app_id in itertools.product(
            # Nothing applied, one tweak, and several - the join is what puts
            # the separator in the notification body.
            ([], ["governor"], ["governor", "renice", "tearing"]),
            (False, True),
            (False, True),
            ("", "1343400"),
        ):
            yield {"tweaks": tweaks, "boost_announced": announced,
                   "clip_on_incident": clip, "steam_app_id": app_id}

    def test_every_shape_of_launch_agrees(self):
        for case in self._launch_cases():
            with self.subTest(**case):
                want, _ = _python_launch(case)
                self.assertEqual(self._rust_launch(case), want)

    def test_the_boost_flag_is_set_exactly_when_it_is_announced_on(self):
        for case in self._launch_cases():
            with self.subTest(**case):
                _, announced = _python_launch(case)
                announced_on = any(step[0] == "AnnounceBoostOn"
                                   for step in self._rust_launch(case))
                self.assertEqual(
                    announced,
                    announced_on or case["boost_announced"],
                    "the flag and the plan disagree about whether the boost "
                    "was announced on",
                )

    def test_a_launch_that_resolves_to_no_profile_does_nothing_at_all(self):
        """Not even a status broadcast - the daemon returns before it, which
        is the one path out of this method that skips it. An event with no
        profile and nothing to adopt is not news."""
        log: list = []
        daemon = _daemon(log, {})
        with patch("goblinmode.daemon.GLib.timeout_add_seconds", _timers(log)), \
                patch("goblinmode.daemon.threading.Thread", _Thread):
            daemon._on_game_event(_Event(None, running=True, pid=PID))
        self.assertEqual(log, [])

    def test_the_boost_flag_is_cleared_exactly_when_it_is_announced_off(self):
        """The plan says to announce; the flag is what stops it announcing
        twice. They are the same decision and must not drift apart."""
        for state in self._states():
            with self.subTest(**state):
                _, still_announced = _python_exit(state)
                announced_off = ["AnnounceBoostOff", []] in self._rust(state)
                self.assertEqual(
                    still_announced,
                    state["boost_announced"] and not announced_off,
                    "the flag and the plan disagree about whether the boost "
                    "was announced off",
                )


if __name__ == "__main__":
    unittest.main()
