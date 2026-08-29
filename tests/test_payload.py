"""Integration test for the Performance Payload apply/revert orchestration.

Uses a fake helper (no system D-Bus, no root) and stubs for the compositor /
focus / MangoHud side effects, then asserts the *refcounting* contract:

* global tweaks (governor, tearing) apply when the first wanting game appears
* they are only reverted when the last one exits
* a helper outage degrades to an incident, not a crash
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401

logging.getLogger("goblinmode.payload").setLevel(logging.CRITICAL)

from goblinmode import payload as payload_mod
from goblinmode.config import GameProfile
from goblinmode.ipc.helper_client import HelperUnavailable
from goblinmode.payload import PerformancePayload


class FakeHelper:
    """Records calls; mimics the HelperClient surface the payload touches."""

    def __init__(self, *, up=True):
        self.up = up
        self.calls: list[tuple] = []
        self.governor = "powersave"
        self.pl_uw = (0, 0)

    def _guard(self):
        if not self.up:
            raise HelperUnavailable("fake helper down")

    def available(self):
        return self.up

    def set_governor(self, g):
        self._guard(); self.calls.append(("set_governor", g)); self.governor = g; return True

    def set_epp(self, e):
        self._guard(); self.calls.append(("set_epp", e)); return True

    def set_power_limits(self, pl1, pl2):
        self._guard(); self.calls.append(("set_power_limits", pl1, pl2)); self.pl_uw = (pl1, pl2); return True

    def set_tdp(self, w):
        self._guard(); self.calls.append(("set_tdp", w)); return True

    def renice(self, pid, nice):
        self._guard(); self.calls.append(("renice", pid, nice)); return True

    def apply_undervolt(self):
        self._guard(); self.calls.append(("apply_undervolt",)); return True

    def revert_all(self):
        self._guard(); self.calls.append(("revert_all",)); self.governor = "powersave"; self.pl_uw = (0, 0); return True

    def get_governor(self):
        self._guard(); return self.governor

    def get_power_limits(self):
        self._guard(); return self.pl_uw


class _StubCompositor:
    def __init__(self):
        self.tearing = False
        self.vrr = False

    def enable_tearing(self):
        self.tearing = True; return True

    def restore_tearing(self):
        self.tearing = False

    def enable_adaptive_sync(self):
        self.vrr = True; return True

    def restore_adaptive_sync(self):
        self.vrr = False


class _StubFocus:
    def __init__(self):
        self.active = False

    def enter(self):
        self.active = True

    def exit(self):
        self.active = False


def _profile(exe, **kw):
    p = GameProfile(exe=exe, display_name=exe, match_mode="exact")
    p.governor_boost = kw.get("governor_boost", True)
    p.tearing_enabled = kw.get("tearing_enabled", True)
    p.renice_enabled = kw.get("renice_enabled", False)
    p.core_pin = "off"
    return p


class PayloadRefcountTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        tmp = Path(self._tmp.name)

        # Redirect the applied-state write and stub MangoHud entirely.
        self._orig_state = payload_mod.APPLIED_STATE_FILE
        self._orig_ensure = payload_mod.ensure_user_dirs
        payload_mod.APPLIED_STATE_FILE = tmp / "applied.json"
        payload_mod.ensure_user_dirs = lambda: None

        self._mh_apply = payload_mod.mangohud.apply
        self._mh_revert = payload_mod.mangohud.revert
        payload_mod.mangohud.apply = lambda profile: tmp / f"{profile.exe}.conf"
        payload_mod.mangohud.revert = lambda profile: None

        self.helper = FakeHelper()
        self.pay = PerformancePayload(helper=self.helper)
        self.pay.compositor = _StubCompositor()
        self.pay.focus = _StubFocus()
        # No AMD path in these tests.
        self.pay._tdp_backend = lambda: None

    def tearDown(self):
        payload_mod.APPLIED_STATE_FILE = self._orig_state
        payload_mod.ensure_user_dirs = self._orig_ensure
        payload_mod.mangohud.apply = self._mh_apply
        payload_mod.mangohud.revert = self._mh_revert
        self._tmp.cleanup()

    def test_global_tweaks_are_refcounted_across_two_games(self):
        a, b = _profile("A.exe"), _profile("B.exe")

        self.pay.apply(a, pid=100)
        self.assertEqual(self.helper.governor, "performance")
        self.assertTrue(self.pay.compositor.tearing)

        # Second game: governor already boosted, must not double-revert later.
        self.pay.apply(b, pid=200)

        # First game exits - globals stay because B still wants them.
        self.pay.revert(a)
        self.assertEqual(self.helper.governor, "performance")
        self.assertTrue(self.pay.compositor.tearing)
        self.assertNotIn(("revert_all",), self.helper.calls)

        # Last game exits - now everything unwinds.
        self.pay.revert(b)
        self.assertEqual(self.helper.governor, "powersave")
        self.assertFalse(self.pay.compositor.tearing)
        self.assertIn(("revert_all",), self.helper.calls)

    def test_applied_state_file_tracks_active_games(self):
        import json

        self.pay.apply(_profile("A.exe"), pid=1)
        state = json.loads(payload_mod.APPLIED_STATE_FILE.read_text())
        self.assertEqual(state["active"], ["A.exe"])
        self.assertTrue(state["governor_applied"])

        self.pay.revert_all()
        state = json.loads(payload_mod.APPLIED_STATE_FILE.read_text())
        self.assertEqual(state["active"], [])

    def test_renice_uses_the_supplied_pid(self):
        p = _profile("A.exe", renice_enabled=True)
        p.nice_value = -5
        self.pay.apply(p, pid=777)
        self.assertIn(("renice", 777, -5), self.helper.calls)

    def test_helper_outage_raises_incident_not_exception(self):
        incidents: list[tuple] = []
        pay = PerformancePayload(
            helper=FakeHelper(up=False),
            on_incident=lambda k, d: incidents.append((k, d)),
        )
        pay.compositor = _StubCompositor()
        pay.focus = _StubFocus()
        pay._tdp_backend = lambda: None

        pay.apply(_profile("A.exe"), pid=1)  # must not raise
        self.assertTrue(any(k == "helper_unavailable" for k, _ in incidents))
        # Compositor tweak is independent of the helper and still lands.
        self.assertTrue(pay.compositor.tearing)

        pay.revert_all()  # must not raise even though the helper is down

    def test_power_limit_refcount_takes_the_max_request(self):
        a = _profile("A.exe"); a.power_limit_enabled = True; a.pl1_w = 25; a.pl2_w = 30
        b = _profile("B.exe"); b.power_limit_enabled = True; b.pl1_w = 45; b.pl2_w = 55

        self.pay.apply(a, pid=1)
        self.pay.apply(b, pid=2)
        # Last write should reflect the higher of the two.
        self.assertEqual(self.helper.pl_uw, (45_000_000, 55_000_000))

        self.pay.revert(b)  # A still wants a limit -> drop back to A's numbers
        self.assertEqual(self.helper.pl_uw, (25_000_000, 30_000_000))

    def test_battery_preset_used_only_on_battery(self):
        from unittest.mock import patch

        a = _profile("A.exe")
        a.power_limit_enabled = True
        a.pl1_w, a.pl2_w = 25, 30
        a.battery_pl1_w, a.battery_pl2_w = 10, 15

        with patch("goblinmode.capabilities.on_ac_power", return_value=True):
            self.pay.apply(a, pid=1)
            self.assertEqual(self.helper.pl_uw, (25_000_000, 30_000_000))

        with patch("goblinmode.capabilities.on_ac_power", return_value=False):
            self.pay.refresh_power_source()
            self.assertEqual(self.helper.pl_uw, (10_000_000, 15_000_000))

        # back on AC: reverts to the AC value
        with patch("goblinmode.capabilities.on_ac_power", return_value=True):
            self.pay.refresh_power_source()
            self.assertEqual(self.helper.pl_uw, (25_000_000, 30_000_000))

    def test_battery_preset_falls_back_to_ac_value_when_unset(self):
        from unittest.mock import patch

        a = _profile("A.exe")
        a.power_limit_enabled = True
        a.pl1_w, a.pl2_w = 25, 30  # no battery_pl*_w set (defaults 0)

        with patch("goblinmode.capabilities.on_ac_power", return_value=False):
            self.pay.apply(a, pid=1)
            self.assertEqual(self.helper.pl_uw, (25_000_000, 30_000_000))


if __name__ == "__main__":
    unittest.main()
