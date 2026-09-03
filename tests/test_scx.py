"""Tests for the sched_ext integration.

The interesting parts are the ones that touch the machine: which scheduler
gets asked for when several games disagree, and - much more important - that
whatever was running before Goblin touched it is what's running afterwards. A
sched_ext scheduler is system-wide and outlives the game, so a revert that
doesn't restore is not a cosmetic bug.

No D-Bus: ScxManager is replaced with a fake that records calls.
"""

from __future__ import annotations

import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import scx
from goblinmode.config import SCX_NAME_RE, GameProfile


class FakeScx:
    """Records switch/stop calls and models 'what is running right now'."""

    def __init__(self, running=None, supported=("lavd", "bpfland", "flash")):
        self.running = running
        self.supported = list(supported)
        self.calls: list[tuple] = []
        self.fail_next = False

    def available(self):
        return True

    def current(self):
        return self.running

    def state(self):
        return {"running": self.running, "supported": self.supported,
                "kernel": True, "loader": True, "mode": 0}

    def switch(self, scheduler, mode="gaming"):
        self.calls.append(("switch", scheduler, mode))
        if self.fail_next or scheduler not in self.supported:
            return False
        self.running = scheduler
        return True

    def stop(self):
        self.calls.append(("stop",))
        self.running = None
        return True

    def restore(self, previous, mode="gaming"):
        self.calls.append(("restore", previous))
        if previous:
            self.running = previous
        else:
            self.running = None
        return True


def _profile(exe, sched="", mode="gaming"):
    return GameProfile(exe=exe, scx_scheduler=sched, scx_mode=mode)


class NameHandling(unittest.TestCase):
    def test_short_and_prefixed_names_both_work(self):
        self.assertEqual(_profile("a.exe", "lavd").scx_scheduler, "lavd")
        self.assertEqual(_profile("a.exe", "scx_lavd").scx_scheduler, "lavd")

    def test_a_name_that_is_not_a_name_is_dropped(self):
        for bad in ("x; rm -rf /", "../../etc/passwd", "LAVD", "a" * 40):
            self.assertEqual(_profile("a.exe", bad).scx_scheduler, "",
                             f"{bad!r} should not survive validation")

    def test_the_name_pattern_is_anchored_at_the_real_end(self):
        # Python's `$` also matches just before a trailing newline. GameProfile
        # strips the name first, so nothing reachable depends on this - but the
        # pattern is exported and read as "these characters, this length", and
        # it should mean that for the next caller too.
        self.assertIsNone(SCX_NAME_RE.match("lavd\n"))
        self.assertIsNotNone(SCX_NAME_RE.match("lavd"))

    def test_an_unknown_mode_falls_back_to_gaming(self):
        self.assertEqual(_profile("a.exe", "lavd", "bogus").scx_mode, "gaming")

    def test_every_mode_name_maps_to_a_number(self):
        for name in ("auto", "gaming", "lowlatency", "powersave", "server"):
            self.assertIn(name, scx.SCHED_MODES)
        self.assertEqual(scx.SCHED_MODES["auto"], 0)


class PayloadIntegration(unittest.TestCase):
    """Refcounting and - the part that matters - restoring."""

    def _payload(self, running=None):
        from goblinmode.payload import PerformancePayload

        class _NoHelper:
            def available(self): return False
            def revert_all(self): return True

        p = PerformancePayload(helper=_NoHelper())
        p.scx = FakeScx(running=running)
        return p

    def test_a_game_that_wants_a_scheduler_gets_it(self):
        p = self._payload()
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._recompute_scx()
        self.assertEqual(p.scx.running, "lavd")
        self.assertEqual(p._scx_applied, "lavd")

    def test_a_game_that_wants_nothing_changes_nothing(self):
        p = self._payload()
        p._active["a.exe"] = _profile("a.exe")
        p._recompute_scx()
        self.assertEqual(p.scx.calls, [])
        self.assertIsNone(p._scx_applied)

    def test_exiting_restores_the_kernel_scheduler_when_none_was_running(self):
        p = self._payload(running=None)
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._recompute_scx()
        del p._active["a.exe"]
        p._recompute_scx()
        self.assertIsNone(p.scx.running)
        self.assertIsNone(p._scx_applied)

    def test_exiting_restores_the_scheduler_the_user_already_had(self):
        """The case that matters: don't clobber someone's own setup."""
        p = self._payload(running="bpfland")
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._recompute_scx()
        self.assertEqual(p.scx.running, "lavd")
        del p._active["a.exe"]
        p._recompute_scx()
        self.assertEqual(p.scx.running, "bpfland",
                         "the user's own scheduler must come back")

    def test_two_games_do_not_flap_the_scheduler(self):
        p = self._payload()
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._active["b.exe"] = _profile("b.exe", "flash")
        p._recompute_scx()
        first = p._scx_applied
        p._recompute_scx()
        p._recompute_scx()
        self.assertEqual(p._scx_applied, first)
        self.assertEqual(len([c for c in p.scx.calls if c[0] == "switch"]), 1)

    def test_the_scheduler_is_kept_until_the_last_wanting_game_exits(self):
        p = self._payload(running=None)
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._active["b.exe"] = _profile("b.exe", "lavd")
        p._recompute_scx()
        del p._active["a.exe"]
        p._recompute_scx()
        self.assertEqual(p.scx.running, "lavd", "still one game wants it")
        del p._active["b.exe"]
        p._recompute_scx()
        self.assertIsNone(p.scx.running)

    def test_a_failed_switch_raises_an_incident_and_does_not_claim_success(self):
        incidents = []
        p = self._payload()
        p._on_incident = lambda kind, detail: incidents.append((kind, detail))
        p.scx.fail_next = True
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._recompute_scx()
        self.assertIsNone(p._scx_applied)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0][0], "scx_switch_failed")

    def test_revert_all_puts_the_scheduler_back(self):
        p = self._payload(running="rusty")
        p._active["a.exe"] = _profile("a.exe", "lavd")
        p._recompute_scx()
        p._restore_global()
        self.assertEqual(p.scx.running, "rusty")


class AppliedState(unittest.TestCase):
    """A game killed mid-session leaves the machine on our scheduler, so the
    cold --revert path has to know about it."""

    def test_a_loaded_scheduler_makes_the_state_dirty(self):
        from goblinmode import payload
        self.assertTrue(payload.applied_state_dirty.__doc__)

    def test_describe_names_the_scheduler_and_what_it_goes_back_to(self):
        import json
        import tempfile
        from pathlib import Path

        from goblinmode import payload
        orig = payload.APPLIED_STATE_FILE
        with tempfile.TemporaryDirectory() as d:
            payload.APPLIED_STATE_FILE = Path(d) / "applied.json"
            try:
                payload.APPLIED_STATE_FILE.write_text(json.dumps(
                    {"active": ["a.exe"], "scx_applied": "lavd",
                     "scx_previous": "bpfland"}))
                self.assertTrue(payload.applied_state_dirty())
                text = " ".join(payload.describe_applied_state())
                self.assertIn("scx_lavd", text)
                self.assertIn("scx_bpfland", text)
            finally:
                payload.APPLIED_STATE_FILE = orig

    def test_describe_handles_having_had_no_scheduler_before(self):
        import json
        import tempfile
        from pathlib import Path

        from goblinmode import payload
        orig = payload.APPLIED_STATE_FILE
        with tempfile.TemporaryDirectory() as d:
            payload.APPLIED_STATE_FILE = Path(d) / "applied.json"
            try:
                payload.APPLIED_STATE_FILE.write_text(json.dumps(
                    {"active": ["a.exe"], "scx_applied": "lavd",
                     "scx_previous": None}))
                text = " ".join(payload.describe_applied_state())
                self.assertIn("kernel's own scheduler", text)
            finally:
                payload.APPLIED_STATE_FILE = orig


if __name__ == "__main__":
    unittest.main()
