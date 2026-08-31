"""Contract test for the privileged helper's D-Bus dispatch.

Focus: every mutating method is actually gated by a polkit check before its
underlying function runs, and gets the *right* action (the stricter
"persistent system config" action for sysctls/modprobe.d writes, the regular
one for everything else). This is what stands between a compromised session
process and root - it's worth a dedicated, explicit test rather than relying
on it being exercised incidentally elsewhere.

No real D-Bus/polkit involved: _check_authorized is stubbed per test, and
_handle_call's `invocation` is a small recorder standing in for the real
Gio.DBusMethodInvocation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_HELPER_DIR = Path(__file__).resolve().parent.parent / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import goblin_helper as gh  # noqa: E402


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error: tuple[str, str] | None = None

    def return_value(self, variant):
        self.value = variant.unpack()

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def _call(method_name: str, args: tuple = (), authorized: bool = True):
    """Drive _handle_call the way the D-Bus layer would, with polkit and the
    underlying mutating function both stubbed so this only tests dispatch."""
    invocation = FakeInvocation()
    fmt = gh.INTROSPECTION_XML  # not parsed here; args are passed pre-built
    params = _FakeParams(args)
    with patch.object(gh, "_check_authorized", return_value=authorized):
        gh._handle_call(None, "fake-sender", gh.OBJECT_PATH, gh.IFACE,
                        method_name, params, invocation)
    return invocation


class _FakeParams:
    """Stands in for the GLib.Variant `parameters` - only .unpack() is used
    by _handle_call before dispatch."""

    def __init__(self, args: tuple):
        self._args = args

    def unpack(self):
        return self._args


class PolkitActionSelection(unittest.TestCase):
    def test_sysctl_and_modeset_use_the_kernel_action(self):
        for m in ("SetSysctl", "RevertSysctl", "SetNvidiaModeset"):
            self.assertEqual(gh._polkit_action_for(m), gh.POLKIT_KERNEL, m)

    def test_spin_up_fans_uses_the_thermal_action(self):
        self.assertEqual(gh._polkit_action_for("SpinUpFans"), gh.POLKIT_THERMAL)
        # restoring EC control must never be gated behind a prompt
        self.assertEqual(gh._polkit_action_for("ResetFans"), gh.POLKIT_PERF)

    def test_everything_else_mutating_uses_the_perf_action(self):
        special = gh._KERNEL_ACTION_METHODS | gh._THERMAL_ACTION_METHODS
        for m in gh._MUTATING - special:
            self.assertEqual(gh._polkit_action_for(m), gh.POLKIT_PERF, m)

    def test_every_kernel_action_method_is_in_mutating(self):
        self.assertTrue(gh._KERNEL_ACTION_METHODS <= gh._MUTATING)

    def test_every_thermal_action_method_is_in_mutating(self):
        self.assertTrue(gh._THERMAL_ACTION_METHODS <= gh._MUTATING)


class FanFloor(unittest.TestCase):
    def test_spin_up_fans_rejects_a_duty_below_the_floor(self):
        for bad in (0, 1, 39):
            with self.assertRaises(ValueError):
                gh.spin_up_fans(bad)

    def test_spin_up_fans_accepts_the_floor_and_above(self):
        # No writable pwm on the test box -> returns False, but must not raise.
        for ok in (gh.MIN_FAN_PERCENT, 100):
            self.assertFalse(gh.spin_up_fans(ok))


class PowerLoweringGuard(unittest.TestCase):
    def test_power_request_below_baseline_is_flagged(self):
        with patch.object(gh, "_load_state",
                          return_value={"pl1_uw": 25_000_000, "pl2_uw": 45_000_000}):
            self.assertTrue(gh._power_request_lowers(4_000_000, 0))
            self.assertTrue(gh._power_request_lowers(0, 10_000_000))
            self.assertFalse(gh._power_request_lowers(25_000_000, 45_000_000))
            self.assertFalse(gh._power_request_lowers(40_000_000, 60_000_000))
            self.assertFalse(gh._power_request_lowers(0, 0))  # "leave alone"

    def test_lowering_power_needs_the_kernel_action(self):
        seen: list[str] = []

        def auth(_sender, action):
            seen.append(action)
            return action == gh.POLKIT_PERF  # session-active perf yes, admin no

        inv = FakeInvocation()
        with patch.object(gh, "_check_authorized", side_effect=auth), \
             patch.object(gh, "_load_state",
                          return_value={"pl1_uw": 25_000_000, "pl2_uw": 45_000_000}), \
             patch.object(gh, "set_power_limits") as set_pl:
            gh._handle_call(None, "s", gh.OBJECT_PATH, gh.IFACE, "SetPowerLimits",
                            _FakeParams((4_000_000, 0)), inv)
        set_pl.assert_not_called()
        self.assertEqual(inv.error[0], f"{gh.IFACE}.NotAuthorized")
        self.assertIn(gh.POLKIT_KERNEL, seen)

    def test_raising_power_stays_promptless(self):
        with patch.object(gh, "_check_authorized", return_value=True), \
             patch.object(gh, "_load_state",
                          return_value={"pl1_uw": 25_000_000, "pl2_uw": 45_000_000}), \
             patch.object(gh, "set_power_limits", return_value=True) as set_pl:
            inv = FakeInvocation()
            gh._handle_call(None, "s", gh.OBJECT_PATH, gh.IFACE, "SetPowerLimits",
                            _FakeParams((50_000_000, 60_000_000)), inv)
        set_pl.assert_called_once_with(50_000_000, 60_000_000)
        self.assertEqual(inv.value, (True,))


class MutatingMethodsAreGated(unittest.TestCase):
    """Every _MUTATING method must refuse to run when polkit denies it - and
    must actually call the real function (not just report ok) when it's
    authorized. Read-only methods (GetGovernor, etc) are deliberately not
    gated at all - not covered here, that's correct as-is."""

    def test_denied_authorization_never_calls_the_underlying_function(self):
        # SetGovernor would raise ValueError for this bogus governor if it
        # were ever actually called - denial must short-circuit before that.
        inv = _call("SetGovernor", args=("bogus-governor",), authorized=False)
        self.assertIsNone(inv.value)
        self.assertEqual(inv.error[0], f"{gh.IFACE}.NotAuthorized")

    def test_every_mutating_method_is_denied_when_unauthorized(self):
        stub_args = {
            "SetGovernor": ("performance",), "SetEPP": ("performance",),
            "Renice": (1, 0), "SetPowerLimits": (0, 0), "ResetPowerLimits": (),
            "SetTDP": (15,), "ResetTDP": (), "RevertAll": (),
            "SetSysctl": ("vm.swappiness", "10"), "RevertSysctl": ("vm.swappiness",),
            "ApplyUndervolt": (), "ApplyAmdUndervolt": (),
            "SetNvidiaModeset": (True,), "SpinUpFans": (100,), "ResetFans": (),
        }
        self.assertEqual(set(stub_args), gh._MUTATING,
                         "every _MUTATING method needs a stub arg tuple above")
        for method, args in stub_args.items():
            inv = _call(method, args=args, authorized=False)
            self.assertIsNotNone(inv.error, f"{method} was not denied")
            self.assertEqual(inv.error[0], f"{gh.IFACE}.NotAuthorized", method)
            self.assertIsNone(inv.value, f"{method} returned a value despite denial")

    def test_authorized_call_reaches_the_real_function(self):
        with patch.object(gh, "revert_all", return_value=True) as revert_all:
            inv = _call("RevertAll", args=(), authorized=True)
        revert_all.assert_called_once()
        self.assertEqual(inv.value, (True,))

    def test_readonly_methods_are_never_gated(self):
        for m in ("GetGovernor", "GetPowerLimits", "HasTDPControl", "ReadUndervolt"):
            self.assertNotIn(m, gh._MUTATING)


if __name__ == "__main__":
    unittest.main()
