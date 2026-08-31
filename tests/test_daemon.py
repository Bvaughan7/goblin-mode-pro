"""The daemon's own logic - the first tests it has ever had.

857 lines, and until now covered only indirectly through the payload and
observer suites. The daemon is a god object wired to a bus, a tray and a GLib
loop, so these build it with `__new__` and attach only the collaborators the
method under test touches. That is deliberate: it keeps the tests honest about
what each method actually depends on, and it means a method that quietly grows
a new dependency fails here instead of at runtime on someone's machine.

Covered: the readiness score people see on the dashboard, the debounced profile
save (a dragged slider fires set_profile ~10x/second), profile validation at
the D-Bus boundary, and the forced-boost lifecycle.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import config
from goblinmode.daemon import Daemon

logging.getLogger("goblinmode.daemon").setLevel(logging.CRITICAL)


class _Recorder:
    """Accepts any call and records it."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*a, **kw):
            self.calls.append((name, a, kw))
            return None
        return record

    def names(self):
        return [c[0] for c in self.calls]


def _bare_daemon(**attrs) -> Daemon:
    """A Daemon with nothing wired up but what a test asks for.

    `Daemon()` publishes a bus name, starts a tray icon and builds an observer;
    none of that is needed to exercise its logic, and requiring it would make
    these tests need a session bus.
    """
    d = Daemon.__new__(Daemon)
    for k, v in attrs.items():
        setattr(d, k, v)
    return d


class HealthScore(unittest.TestCase):
    """The 0-10 number on the Dashboard. Users read it as a verdict, so the
    arithmetic is worth pinning down."""

    def _score(self, statuses):
        d = _bare_daemon(_health={})
        d._cache_health([{"status": s, "title": f"{s} check"} for s in statuses])
        return d._health

    def test_a_clean_system_scores_ten(self):
        self.assertEqual(self._score(["ok"] * 10)["score"], 10)

    def test_failures_cost_more_than_warnings(self):
        one_fail = self._score(["ok"] * 9 + ["fail"])["score"]
        one_warn = self._score(["ok"] * 9 + ["warn"])["score"]
        self.assertLess(one_fail, one_warn)
        self.assertLess(one_warn, 10)

    def test_info_and_unknown_are_neutral(self):
        self.assertEqual(self._score(["ok"] * 5 + ["info", "unknown"])["score"], 10)

    def test_the_score_never_goes_negative(self):
        self.assertGreaterEqual(self._score(["fail"] * 20)["score"], 0)

    def test_it_counts_every_status(self):
        h = self._score(["ok", "ok", "warn", "fail", "info", "unknown"])
        self.assertEqual(h["counts"],
                         {"ok": 2, "warn": 1, "fail": 1, "info": 1, "unknown": 1})

    def test_worst_lists_failing_titles_capped_at_three(self):
        h = self._score(["fail"] * 5)
        self.assertEqual(len(h["worst"]), 3)
        self.assertTrue(all("fail" in t for t in h["worst"]))

    def test_worst_is_empty_when_nothing_fails(self):
        self.assertEqual(self._score(["ok", "warn"])["worst"], [])

    def test_an_unexpected_status_does_not_crash_the_daemon(self):
        """preflight could grow a new status; the dashboard must not die."""
        d = _bare_daemon(_health={})
        d._cache_health([{"status": "surprising", "title": "new"}])
        self.assertIn("score", d._health)


class HealthCaching(unittest.TestCase):
    def test_a_fresh_result_is_not_recomputed(self):
        d = _bare_daemon(_health={"score": 7.0, "checked_at": time.time()})
        with patch("goblinmode.preflight.run_all") as run:
            self.assertEqual(d.get_health()["score"], 7.0)
        run.assert_not_called()

    def test_a_stale_result_is_recomputed(self):
        d = _bare_daemon(_health={"score": 7.0, "checked_at": time.time() - 3600})
        with patch("goblinmode.preflight.run_all",
                   return_value=[{"status": "ok", "title": "x"}]) as run:
            self.assertEqual(d.get_health()["score"], 10)
        run.assert_called_once()

    def test_a_failing_preflight_returns_the_stale_score_not_a_crash(self):
        d = _bare_daemon(_health={"score": 7.0, "checked_at": 0})
        with patch("goblinmode.preflight.run_all", side_effect=OSError("boom")):
            self.assertEqual(d.get_health()["score"], 7.0)


class SetProfileValidation(unittest.TestCase):
    """set_profile is a D-Bus entry point - anything can arrive on it."""

    def _daemon(self):
        return _bare_daemon(
            settings=config.Settings(profiles=[]),
            _dirty_profiles=set(),
            _save_source_id=object(),      # already scheduled: no GLib needed
            _broadcast_status=lambda: None,
        )

    def test_a_non_dict_is_rejected(self):
        d = self._daemon()
        for junk in (None, "profile", 42, [], True):
            self.assertFalse(d.set_profile(junk), f"{junk!r} was accepted")

    def test_a_profile_without_an_exe_is_rejected(self):
        self.assertFalse(self._daemon().set_profile({"display_name": "x"}))

    def test_unknown_keys_are_dropped_rather_than_crashing(self):
        d = self._daemon()
        self.assertTrue(d.set_profile({"exe": "Wow.exe", "nonsense": 1}))
        self.assertEqual(len(d.settings.profiles), 1)

    def test_a_valid_profile_is_stored(self):
        d = self._daemon()
        self.assertTrue(d.set_profile({"exe": "Wow.exe", "display_name": "WoW"}))
        self.assertEqual(d.settings.profiles[0].exe, "Wow.exe")

    def test_updating_replaces_in_place_rather_than_duplicating(self):
        d = self._daemon()
        d.set_profile({"exe": "Wow.exe", "nice_value": -5})
        d.set_profile({"exe": "Wow.exe", "nice_value": -9})
        self.assertEqual(len(d.settings.profiles), 1)
        self.assertEqual(d.settings.profiles[0].nice_value, -9)

    def test_every_field_the_gui_can_send_is_accepted(self):
        """A field added to GameProfile but rejected here silently fails to
        save, which is invisible until someone reports a lost setting."""
        d = self._daemon()
        payload = {f.name: getattr(config.GameProfile(exe="Wow.exe"), f.name)
                   for f in dataclasses.fields(config.GameProfile)}
        self.assertTrue(d.set_profile(payload))


class DebouncedSave(unittest.TestCase):
    """A dragged SpinRow calls set_profile ~10x/second; only one save should
    reach disk."""

    def _daemon(self, tmp):
        scheduled = []
        d = _bare_daemon(
            settings=config.Settings(profiles=[]),
            _dirty_profiles=set(),
            _save_source_id=None,
            _broadcast_status=lambda: None,
            _active_pids={},
            observer=_Recorder(),
            payload=_Recorder(),
            fpswatch=_Recorder(),
        )
        return d, scheduled

    def test_repeated_edits_schedule_exactly_one_flush(self):
        with TemporaryDirectory() as tmp:
            d, _ = self._daemon(tmp)
            with patch("goblinmode.daemon.GLib.timeout_add",
                       return_value=1) as add:
                for nice in range(-1, -11, -1):
                    d.set_profile({"exe": "Wow.exe", "nice_value": nice})
            self.assertEqual(add.call_count, 1,
                             "the debounce collapsed into more than one timer")
            self.assertEqual(d._dirty_profiles, {"Wow.exe"})

    def test_the_last_value_is_the_one_that_survives(self):
        with TemporaryDirectory() as tmp:
            d, _ = self._daemon(tmp)
            with patch("goblinmode.daemon.GLib.timeout_add", return_value=1):
                d.set_profile({"exe": "Wow.exe", "nice_value": -1})
                d.set_profile({"exe": "Wow.exe", "nice_value": -9})
            self.assertEqual(d.settings.profiles[0].nice_value, -9)

    def test_flushing_saves_clears_the_dirty_set_and_rearms(self):
        with TemporaryDirectory() as tmp:
            d, _ = self._daemon(tmp)
            d.settings.profiles.append(config.GameProfile(exe="Wow.exe"))
            d._dirty_profiles = {"Wow.exe"}
            d._save_source_id = 1
            with patch("goblinmode.config.save") as save, \
                    patch("goblinmode.mangohud.apply"):
                d._flush_profiles()
            save.assert_called_once()
            self.assertEqual(d._dirty_profiles, set())
            self.assertIsNone(d._save_source_id,
                              "the next edit must be able to schedule a flush")

    def test_a_dirty_profile_removed_before_the_flush_is_skipped(self):
        """Edit a game, delete it, then the timer fires."""
        with TemporaryDirectory() as tmp:
            d, _ = self._daemon(tmp)
            d._dirty_profiles = {"Gone.exe"}
            d._save_source_id = 1
            with patch("goblinmode.config.save"), patch("goblinmode.mangohud.apply"):
                d._flush_profiles()          # must not raise on the missing profile
            self.assertEqual(d._dirty_profiles, set())


class ForcedBoost(unittest.TestCase):
    def _daemon(self):
        return _bare_daemon(
            _forced_boost=False,
            payload=_Recorder(),
            observer=_bare_daemon(active_exes=[]),
            _broadcast_status=lambda: None,
            _ensure_diagnostics_running=lambda: None,
            _stop_diagnostics=lambda: None,
        )

    def test_boosting_applies_a_synthetic_profile(self):
        d = self._daemon()
        self.assertTrue(d.force_boost(True))
        self.assertTrue(d._forced_boost)
        self.assertIn("apply", d.payload.names())
        profile = d.payload.calls[0][1][0]
        self.assertEqual(profile.exe, "__forced__")

    def test_the_synthetic_profile_does_not_renice_or_touch_mangohud(self):
        """It is not a real game: there is no pid to renice and no overlay."""
        d = self._daemon()
        d.force_boost(True)
        profile = d.payload.calls[0][1][0]
        self.assertFalse(profile.renice_enabled)
        self.assertFalse(profile.mangohud.get("enabled"))

    def test_unboosting_reverts(self):
        d = self._daemon()
        d.force_boost(True)
        d.payload.calls.clear()
        self.assertTrue(d.force_boost(False))
        self.assertFalse(d._forced_boost)
        self.assertIn("revert", d.payload.names())


class Benchmark(unittest.TestCase):
    def test_arming_records_the_game_and_the_time(self):
        d = _bare_daemon(_benchmark=None)
        self.assertTrue(d.arm_benchmark("Wow.exe"))
        self.assertEqual(d._benchmark["exe"], "Wow.exe")
        self.assertGreater(d._benchmark["armed_at"], 0)


if __name__ == "__main__":
    unittest.main()
