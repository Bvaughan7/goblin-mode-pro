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

import os
import subprocess
import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

_HELPER_DIR = Path(__file__).resolve().parent.parent / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import goblin_helper as gh


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


class PowerLimitFloor(unittest.TestCase):
    def test_a_request_below_the_floor_is_refused(self):
        with patch.object(gh, "_snapshot"):
            with self.assertRaises(ValueError):
                gh.set_power_limits(4_000_000, 0)          # 4 W PL1
            with self.assertRaises(ValueError):
                gh.set_power_limits(0, 5_000_000)          # 5 W PL2

    def test_a_refused_request_leaves_no_snapshot_behind(self):
        """A call that is refused must not touch /run.

        set_power_limits used to snapshot before validating, so a below-floor
        request was correctly rejected and still wrote a root-owned
        state.json. Because _snapshot() early-returns once that file exists,
        the next real apply then never recorded its own baseline, and
        RevertAll would restore whatever happened to be true at the moment of
        the rejected call.

        Note the test above patches _snapshot out, which is precisely why this
        went unnoticed - it mocked away the side effect that was the bug. This
        one asserts on the mock instead of ignoring it. Found by
        tests/conformance/helper.py against the live helper.
        """
        for bad in ((4_000_000, 0), (0, 5_000_000), (1, 1)):
            with self.subTest(request=bad), patch.object(gh, "_snapshot") as snap:
                with self.assertRaises(ValueError):
                    gh.set_power_limits(*bad)
                snap.assert_not_called()

    def test_an_accepted_request_does_snapshot_first(self):
        """The complement: the snapshot must still happen on the happy path,
        or RevertAll has nothing to restore to."""
        with patch.object(gh, "_snapshot") as snap, \
             patch.object(gh, "_rapl_constraint",
                          side_effect=lambda i, leaf: Path("/nonexistent")):
            gh.set_power_limits(gh._RAPL_FLOOR_UW, gh._RAPL_FLOOR_UW)
            snap.assert_called_once()

    def test_zero_means_leave_alone_and_is_not_a_floor_violation(self):
        with patch.object(gh, "_snapshot"), \
             patch.object(gh, "_rapl_constraint",
                          side_effect=lambda i, leaf: Path("/nonexistent")):
            # both 0 -> nothing written, no ValueError, ok stays True
            self.assertTrue(gh.set_power_limits(0, 0))


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


class ReniceFailsClosed(unittest.TestCase):
    def _spawn(self) -> subprocess.Popen:
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_owned_process_can_be_reniced_down(self):
        p = self._spawn()
        self.assertTrue(gh.renice(p.pid, 12, caller_uid=os.getuid()))

    def test_wrong_owner_is_refused(self):
        p = self._spawn()
        with self.assertRaises(PermissionError):
            gh.renice(p.pid, 12, caller_uid=os.getuid() + 40001)

    def test_unknown_uid_fails_closed_not_open(self):
        p = self._spawn()
        # None used to be treated like root (uid in (None, 0)) - now untrusted.
        with self.assertRaises(PermissionError):
            gh.renice(p.pid, 12, caller_uid=None)

    def test_dispatch_refuses_renice_when_uid_lookup_fails(self):
        with patch.object(gh, "_check_authorized", return_value=True), \
             patch.object(gh, "_caller_uid", return_value=None), \
             patch.object(gh, "renice") as renice_fn:
            inv = _call_with_connection("Renice", (1234, 0))
        renice_fn.assert_not_called()
        self.assertEqual(inv.error[0], f"{gh.IFACE}.NotAuthorized")


class EppValidation(unittest.TestCase):
    def test_rejects_a_value_the_kernel_does_not_advertise(self):
        with patch.object(gh, "_available_epps",
                          return_value={"performance", "power"}), \
                self.assertRaises(ValueError):
            gh.set_epp("balance_power")

    def test_falls_back_to_the_standard_set_when_kernel_lists_nothing(self):
        with patch.object(gh, "_available_epps", return_value=set()), \
             patch.object(gh, "_snapshot"), \
             patch.object(gh, "_cpu_epp_paths", return_value=[]):
            self.assertFalse(gh.set_epp("balance_power"))  # valid name, no cores
            with self.assertRaises(ValueError):
                gh.set_epp("ludicrous-speed")


def _call_with_connection(method_name: str, args: tuple):
    """Like _call() but leaves _check_authorized/_caller_uid to the caller's
    own patches (the Renice dispatch path resolves a uid off the connection)."""
    inv = FakeInvocation()
    gh._handle_call(None, "fake-sender", gh.OBJECT_PATH, gh.IFACE,
                    method_name, _FakeParams(args), inv)
    return inv


class NvidiaModesetConf(unittest.TestCase):
    """The modprobe.d drop-in: fixed content, and readable by the tooling
    that has to consume it.

    The helper's unit sets UMask=0077, which is correct for the state it keeps
    in /run and wrong for a config file in /etc - initramfs generators and the
    user both need to read this one, and every other file in modprobe.d is
    0644. Verified on real hardware: the write itself works inside the
    sandbox, and produced a 0600 file until this was fixed.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._orig = gh.NVIDIA_MODESET_CONF
        gh.NVIDIA_MODESET_CONF = Path(self._tmp.name) / "goblin-mode-pro-nvidia.conf"

    def tearDown(self):
        gh.NVIDIA_MODESET_CONF = self._orig
        self._tmp.cleanup()

    def test_writes_exactly_one_of_two_fixed_lines(self):
        gh.set_nvidia_modeset(True)
        self.assertEqual(gh.NVIDIA_MODESET_CONF.read_text(),
                         "options nvidia_drm modeset=1\n")
        gh.set_nvidia_modeset(False)
        self.assertEqual(gh.NVIDIA_MODESET_CONF.read_text(),
                         "options nvidia_drm modeset=0\n")

    def test_the_file_is_world_readable(self):
        gh.set_nvidia_modeset(True)
        mode = gh.NVIDIA_MODESET_CONF.stat().st_mode & 0o777
        self.assertEqual(mode, 0o644, f"expected 0644, got {mode:o}")

    def test_an_unwritable_directory_is_a_clean_false(self):
        gh.NVIDIA_MODESET_CONF = Path("/proc/nonexistent/x.conf")
        self.assertFalse(gh.set_nvidia_modeset(True))


if __name__ == "__main__":
    unittest.main()
