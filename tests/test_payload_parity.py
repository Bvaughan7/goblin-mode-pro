"""The Rust and Python want the same things applied, for the same games.

The tweaks split in two. Per-game ones — renice, core pinning — belong to one
process and are applied and undone with it. **Global** ones — the governor,
power limits, tearing, VRR, the refresh cap, focus mode — belong to the
machine, and with two games running they are shared. So they are recomputed
from the whole active set whenever it changes, and the answer is the union of
what everyone wants.

That recompute is the most valuable thing in this file and the least reachable
from outside: the daemon's own conformance suite says so, and skips it, because
grading it needs two real games running at once and a suite cannot arrange
that. Here it is a function of its inputs, so two games is a two-element list —
which is why most of the corpus below has two.

Two rules sit six lines apart in the Python and go in opposite directions, and
both are here:

* the **highest** power limit wins, because those are ceilings being raised and
  a second game wanting more headroom should get it;
* the **lowest** refresh cap wins, because that one is a ceiling on the panel
  and honouring the highest would ignore whoever asked for less.

Nothing in either implementation applies anything. This is the deciding.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import payload as payload_mod
from goblinmode.config import _from_dict

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_WANTED_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "wanted"
        if candidate.exists():
            return candidate
    return None


def p(exe: str, **over) -> dict:
    return {"exe": exe, **over}


def case(profiles, on_battery=False, tdp_backend=None) -> dict:
    return {"profiles": profiles, "on_battery": on_battery,
            "tdp_backend": tdp_backend}


#: `governor_boost` defaults to True, so a profile that is meant to want
#: nothing has to say so.
QUIET = {"governor_boost": False}

CASES = {
    "nothing_running": case([]),
    "one_default_profile": case([p("a")]),
    "one_quiet_profile": case([p("a", **QUIET)]),
    # -- the refcount: one asking is enough, until nobody is ------------------
    "one_of_two_wants_tearing": case([p("a", **QUIET, tearing_enabled=True),
                                      p("b", **QUIET)]),
    "neither_wants_tearing": case([p("a", **QUIET), p("b", **QUIET)]),
    "both_want_tearing": case([p("a", **QUIET, tearing_enabled=True),
                               p("b", **QUIET, tearing_enabled=True)]),
    "one_of_two_wants_the_governor": case([p("a", governor_boost=True),
                                           p("b", **QUIET)]),
    "one_of_two_wants_focus": case([p("a", **QUIET, focus_mode=True),
                                    p("b", **QUIET)]),
    "one_of_two_wants_fan_spinup": case([p("a", **QUIET, fan_spinup_enabled=True),
                                         p("b", **QUIET)]),
    # -- power limits: the HIGHEST wins ---------------------------------------
    "one_power_limit": case([p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60)]),
    # `governor_boost` defaults to True, so without turning it off every power
    # case would need the helper anyway and "power needs the helper" would go
    # untested. The mutation run found exactly that.
    "power_only_still_needs_the_helper": case([
        p("a", **QUIET, power_limit_enabled=True, pl1_w=45, pl2_w=60)]),
    "fans_only_still_need_the_helper": case([
        p("a", **QUIET, fan_spinup_enabled=True)]),
    "two_power_limits_highest_wins": case([
        p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60),
        p("b", power_limit_enabled=True, pl1_w=55, pl2_w=55)]),
    "the_pair_comes_from_different_profiles": case([
        p("a", power_limit_enabled=True, pl1_w=55, pl2_w=10),
        p("b", power_limit_enabled=True, pl1_w=10, pl2_w=90)]),
    "power_limit_switch_off": case([p("a", power_limit_enabled=False, pl1_w=45)]),
    "one_enabled_one_not": case([
        p("a", power_limit_enabled=False, pl1_w=99),
        p("b", power_limit_enabled=True, pl1_w=45)]),
    "power_limit_enabled_but_zero": case([
        p("a", power_limit_enabled=True, pl1_w=0, pl2_w=0)]),
    "only_pl2_set": case([p("a", power_limit_enabled=True, pl1_w=0, pl2_w=60)]),
    # -- battery presets --------------------------------------------------------
    "battery_preset_used": case([
        p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60,
          battery_pl1_w=25, battery_pl2_w=30)], on_battery=True),
    "battery_preset_ignored_on_ac": case([
        p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60,
          battery_pl1_w=25, battery_pl2_w=30)], on_battery=False),
    # Zero means "no opinion", not "cap at nothing".
    "battery_preset_unset_falls_back": case([
        p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60, battery_pl1_w=0)],
        on_battery=True),
    "only_one_battery_half_set": case([
        p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60, battery_pl1_w=25)],
        on_battery=True),
    "two_profiles_on_battery": case([
        p("a", power_limit_enabled=True, pl1_w=45, battery_pl1_w=25),
        p("b", power_limit_enabled=True, pl1_w=45, battery_pl1_w=35)],
        on_battery=True),
    # -- the two power backends ---------------------------------------------------
    "rapl_backend": case([p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60)],
                         tdp_backend="rapl"),
    "ryzenadj_backend": case([p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60)],
                             tdp_backend="ryzenadj"),
    "ryzenadj_pl1_higher": case([p("a", power_limit_enabled=True, pl1_w=65, pl2_w=60)],
                                tdp_backend="ryzenadj"),
    "no_backend": case([p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60)]),
    "unknown_backend": case([p("a", power_limit_enabled=True, pl1_w=45, pl2_w=60)],
                            tdp_backend="something-else"),
    # -- VRR outputs ----------------------------------------------------------------
    "vrr_no_outputs_named": case([p("a", **QUIET, adaptive_sync_enabled=True)]),
    "vrr_one_output": case([p("a", **QUIET, adaptive_sync_enabled=True,
                              vrr_outputs=["DP-1"])]),
    "vrr_union_of_two": case([
        p("a", **QUIET, adaptive_sync_enabled=True, vrr_outputs=["DP-1"]),
        p("b", **QUIET, adaptive_sync_enabled=True, vrr_outputs=["HDMI-1", "DP-1"])]),
    # Naming none is the broader ask and cannot be narrowed by the other.
    "vrr_one_names_none": case([
        p("a", **QUIET, adaptive_sync_enabled=True, vrr_outputs=["DP-1"]),
        p("b", **QUIET, adaptive_sync_enabled=True, vrr_outputs=[])]),
    "vrr_non_wanting_profile_contributes_nothing": case([
        p("a", **QUIET, adaptive_sync_enabled=True, vrr_outputs=["DP-1"]),
        p("b", **QUIET, adaptive_sync_enabled=False, vrr_outputs=["HDMI-1"])]),
    "vrr_duplicate_outputs": case([
        p("a", **QUIET, adaptive_sync_enabled=True, vrr_outputs=["DP-1", "DP-1"])]),
    "vrr_unsorted_outputs": case([
        p("a", **QUIET, adaptive_sync_enabled=True, vrr_outputs=["Z-1", "A-1"])]),
    # -- the refresh cap: the LOWEST wins ---------------------------------------------
    "one_refresh_cap": case([p("a", **QUIET, refresh_rate_hz=144)]),
    "two_refresh_caps_lowest_wins": case([p("a", **QUIET, refresh_rate_hz=144),
                                          p("b", **QUIET, refresh_rate_hz=60)]),
    "refresh_cap_zero_is_no_cap": case([p("a", **QUIET, refresh_rate_hz=0)]),
    "one_capped_one_not": case([p("a", **QUIET, refresh_rate_hz=0),
                                p("b", **QUIET, refresh_rate_hz=60)]),
    # -- everything at once ------------------------------------------------------------
    "two_games_wanting_everything": case([
        p("a", governor_boost=True, tearing_enabled=True, adaptive_sync_enabled=True,
          vrr_outputs=["DP-1"], refresh_rate_hz=144, focus_mode=True,
          fan_spinup_enabled=True, power_limit_enabled=True, pl1_w=45, pl2_w=60),
        p("b", governor_boost=True, tearing_enabled=True, adaptive_sync_enabled=True,
          vrr_outputs=["HDMI-1"], refresh_rate_hz=60, focus_mode=True,
          power_limit_enabled=True, pl1_w=55, pl2_w=55)]),
    "three_games": case([
        p("a", **QUIET, refresh_rate_hz=240),
        p("b", **QUIET, refresh_rate_hz=60, tearing_enabled=True),
        p("c", power_limit_enabled=True, pl1_w=45)]),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the wanted example is "
                          "not built - run `cargo build -p gmp-core --example "
                          "payload`")
            self.skipTest("build it with `cargo build -p gmp-core --example wanted`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(payload: dict) -> dict:
        """Drive the REAL `_recompute_global` and record what it asks for.

        Its decisions cannot be read off a return value - it decides and acts
        in one pass - so the collaborators it acts through are stubbed and
        their calls are the observation. That makes this a test of the code
        rather than of a transcription of it: deleting a branch in
        `_recompute_global` changes what gets recorded here.
        """
        profiles = _from_dict({"profiles": payload["profiles"]}).profiles
        manager = payload_mod.PerformancePayload.__new__(
            payload_mod.PerformancePayload)
        manager._active = {profile.exe: profile for profile in profiles}
        for field, value in (
            ("_helper_tweaks_applied", False), ("_governor_applied", False),
            ("_power_applied", False), ("_power_backend", None),
            ("_power_values", None), ("_fan_spinup_applied", False),
            ("_tearing_applied", False), ("_vrr_applied", False),
            ("_refresh_cap_applied", False), ("_focus_applied", False),
            ("_scx_applied", None), ("_scx_previous", None),
        ):
            setattr(manager, field, value)

        asked: dict = {}

        # Patched onto the class, so it is called bound: `self` comes first.
        def record_helper(_self, want_governor, power, want_fan_spinup=False):
            asked["governor"] = bool(want_governor)
            asked["fan_spinup"] = bool(want_fan_spinup)
            asked["power"] = (
                None if power is None
                else {"backend": power[0], "first": power[1][0],
                      "second": power[1][1]}
            )

        compositor = _Recorder()
        manager.compositor = compositor
        manager.focus = _Recorder()

        on_ac = not payload["on_battery"]
        with patch("goblinmode.capabilities.on_ac_power", return_value=on_ac), \
                patch.object(payload_mod.PerformancePayload, "_tdp_backend",
                             lambda _self: payload["tdp_backend"]), \
                patch.object(payload_mod.PerformancePayload, "_apply_helper_tweaks",
                             record_helper), \
                patch.object(payload_mod.PerformancePayload, "_recompute_scx",
                             lambda _self: None):
            manager._recompute_global()
            pl1, pl2 = manager._desired_power_limits_uw()

        wanted = {
            "governor": asked.get("governor", False),
            "power": asked.get("power"),
            "fan_spinup": asked.get("fan_spinup", False),
            # `_apply_helper_tweaks` is called only when the helper is needed
            # at all, so its having been called IS the answer.
            "helper": "governor" in asked,
            "tearing": compositor.calls.get("enable_tearing", False),
            "adaptive_sync": compositor.calls.get("enable_adaptive_sync", False),
            "vrr_outputs": compositor.vrr_outputs,
            "refresh_cap": compositor.refresh_cap,
            "focus_mode": manager.focus.calls.get("enter", False),
        }
        return {"wanted": wanted, "power_limits_uw": [pl1, pl2]}


class _Recorder:
    """Stands in for the compositor and the focus-mode controller.

    Records which enable/enter calls `_recompute_global` made and with what,
    and returns True from each so the manager believes the tweak took.
    """

    def __init__(self):
        self.calls: dict = {}
        self.vrr_outputs = None
        self.refresh_cap = None

    def enable_tearing(self):
        self.calls["enable_tearing"] = True
        return True

    def enable_adaptive_sync(self, outputs=None):
        self.calls["enable_adaptive_sync"] = True
        self.vrr_outputs = list(outputs) if outputs is not None else None
        return True

    def enable_refresh_cap(self, hz):
        self.calls["enable_refresh_cap"] = True
        self.refresh_cap = hz
        return True

    def enter(self):
        self.calls["enter"] = True
        return True

    def __getattr__(self, name):
        # restore_* and exit are reached only when a tweak was applied and is
        # no longer wanted, which cannot happen on a first recompute.
        raise AssertionError(f"_recompute_global called {name} on a fresh manager")


class Comparison(BothImplementationsAgree):
    def test_every_active_set_wants_the_same_things(self):
        for label, payload in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(payload)),
                                 typed(self._python(payload)))

    def test_the_corpus_exercises_both_answers_for_every_switch(self):
        """A corpus where nothing is ever off would pass against a stub."""
        seen = {key: set() for key in
                ("governor", "fan_spinup", "helper", "tearing", "adaptive_sync",
                 "focus_mode")}
        backends, caps, outputs = set(), set(), set()
        for payload in CASES.values():
            answer = self._python(payload)["wanted"]
            for key in seen:
                seen[key].add(answer[key])
            backends.add(answer["power"]["backend"] if answer["power"] else None)
            caps.add(answer["refresh_cap"] is None)
            outputs.add(answer["vrr_outputs"] is None)
        for key, values in seen.items():
            self.assertEqual(values, {True, False}, f"{key} is one-sided")
        self.assertEqual(backends, {None, "rapl", "ryzenadj"})
        self.assertEqual(caps, {True, False})
        self.assertEqual(outputs, {True, False})

    def test_the_two_opposite_rules_really_are_opposite(self):
        """Highest power limit, lowest refresh cap - six lines apart."""
        answer = self._rust(CASES["two_games_wanting_everything"])["wanted"]
        self.assertEqual(answer["power"]["first"], 55_000_000, "highest PL1 wins")
        self.assertEqual(answer["refresh_cap"], 60, "lowest cap wins")


if __name__ == "__main__":
    unittest.main()
