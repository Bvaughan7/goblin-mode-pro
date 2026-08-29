"""Integration test for the Observer state machine.

Drives :class:`goblinmode.observer.Observer` through a fake process table and
asserts that launch / exit events fire exactly once each, in order, with the
right PID - the contract :class:`goblinmode.payload.PerformancePayload` relies
on.
"""

from __future__ import annotations

import unittest

import _support  # noqa: F401  (adds src/ to sys.path)

import psutil

from goblinmode import observer as observer_mod
from goblinmode.config import GameProfile, Settings
from goblinmode.observer import Observer


class _FakeProc:
    """Minimal stand-in for psutil.Process as the Observer uses it."""

    def __init__(self, pid, name, exe="", cmdline=None, ppid=1, rss=200_000_000):
        self.info = {
            "pid": pid,
            "name": name,
            "exe": exe or f"/usr/bin/{name}",
            "cmdline": cmdline or [name],
            "ppid": ppid,
        }
        self._rss = rss

    def memory_info(self):
        class _M:
            rss = self._rss

        return _M()


class ObserverStateMachineTest(unittest.TestCase):
    def setUp(self):
        self._table: list[_FakeProc] = []
        self.events: list[tuple] = []

        # Patch the process source and disable the expensive auto-detect sweep;
        # this test is about the profile-match state machine.
        self._orig_iter = psutil.process_iter
        psutil.process_iter = lambda attrs=None: list(self._table)  # type: ignore
        self._orig_detect = observer_mod.gamedetect.detect_games
        observer_mod.gamedetect.detect_games = lambda procs=None: []

        prof = GameProfile(exe="Wow.exe", display_name="WoW", match_mode="exact")
        self.settings = Settings(auto_detect=False, profiles=[prof])
        self.obs = Observer(self.settings, self._record)

    def tearDown(self):
        psutil.process_iter = self._orig_iter
        observer_mod.gamedetect.detect_games = self._orig_detect

    def _record(self, ev):
        self.events.append(
            (ev.profile.exe if ev.profile else None, ev.pid, ev.running)
        )

    def test_single_launch_and_exit_fire_once(self):
        # Nothing running.
        self.obs.poll()
        self.assertEqual(self.events, [])

        # Game appears.
        self._table = [_FakeProc(4242, "Wow.exe")]
        self.obs.poll()
        self.obs.poll()  # second poll while still running -> no duplicate
        self.assertEqual(self.events, [("Wow.exe", 4242, True)])

        # Game exits.
        self._table = []
        self.obs.poll()
        self.obs.poll()  # already gone -> no duplicate
        self.assertEqual(
            self.events,
            [("Wow.exe", 4242, True), ("Wow.exe", None, False)],
        )
        self.assertEqual(self.obs.active_exes, [])

    def test_relaunch_emits_a_fresh_pair(self):
        self._table = [_FakeProc(1, "Wow.exe")]
        self.obs.poll()
        self._table = []
        self.obs.poll()
        self._table = [_FakeProc(2, "Wow.exe")]
        self.obs.poll()
        self.assertEqual(
            [e[2] for e in self.events], [True, False, True]
        )
        self.assertEqual(self.events[-1], ("Wow.exe", 2, True))

    def test_wine_infra_is_never_the_game_pid(self):
        # The wine loader shares the exe path but must not win the PID race.
        self._table = [
            _FakeProc(10, "wine64-preloader", exe="/opt/proton/Wow.exe", rss=9_000_000_000),
            _FakeProc(11, "Wow.exe", exe="/opt/proton/Wow.exe", rss=500_000_000),
        ]
        self.obs.poll()
        self.assertEqual(self.events, [("Wow.exe", 11, True)])

    def test_master_disabled_suppresses_launch_but_still_reverts(self):
        self.settings.master_enabled = False
        self._table = [_FakeProc(7, "Wow.exe")]
        self.obs.poll()
        self.assertEqual(self.events, [])  # nothing starts while disabled

        # Enable mid-session, game gets picked up, then disable again: the exit
        # path must still fire so a payload can't get stranded.
        self.settings.master_enabled = True
        self.obs.poll()
        self.assertEqual(self.events, [("Wow.exe", 7, True)])
        self.settings.master_enabled = False
        self._table = []
        self.obs.poll()
        self.assertEqual(self.events[-1], ("Wow.exe", None, False))


if __name__ == "__main__":
    unittest.main()
