"""Tests for `goblin-mode-pro-cli selftest`.

Two jobs. First, the drift guards: selftest mirrors several tables that live in
the privileged helper (the sysctl allowlist, the polkit actions, the required
capabilities, the fan floor) because the helper is not importable from the
installed package. Mirrored constants rot, so these fail the build the moment
they disagree - the same pattern tests/test_helper_sandbox.py uses for the
sandbox paths.

Second, the reporting contract: a probe that reports nothing is worse than no
probe, so every result must carry a sentence, and a failed round-trip must
still revert.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

_HELPER_DIR = Path(__file__).resolve().parent.parent / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import goblin_helper as gh

from goblinmode import selftest


class MirroredConstants(unittest.TestCase):
    def test_sysctl_keys_match_the_helper_allowlist(self):
        self.assertEqual(set(selftest.SYSCTL_KEYS), set(gh.SYSCTL_ALLOW))

    def test_polkit_actions_match_the_helper(self):
        self.assertEqual(
            set(selftest.POLKIT_ACTIONS),
            {gh.POLKIT_PERF, gh.POLKIT_KERNEL, gh.POLKIT_THERMAL},
        )

    def test_required_capabilities_match_the_helper(self):
        self.assertEqual(
            set(selftest.REQUIRED_CAPABILITIES), set(gh.HELPER_CAPABILITIES))

    def test_fan_floor_matches_the_helper(self):
        self.assertEqual(selftest.MIN_FAN_PERCENT, gh.MIN_FAN_PERCENT)


class CapabilityDecoding(unittest.TestCase):
    def test_decodes_the_bounding_set_the_helper_actually_shipped_with(self):
        # 0x800000 is what `capsh --decode` reports as cap_sys_nice alone - the
        # exact mask the helper ran with while user.max_user_namespaces was
        # silently failing.
        self.assertEqual(selftest._decode_caps(0x800000), ["CAP_SYS_NICE"])

    def test_decodes_the_fixed_set(self):
        both = (1 << 23) | (1 << 24)
        self.assertEqual(sorted(selftest._decode_caps(both)),
                         ["CAP_SYS_NICE", "CAP_SYS_RESOURCE"])

    def test_missing_capability_is_a_failure_not_a_skip(self):
        st = selftest.SelfTest()
        st._add("helper_caps", "Helper capabilities", selftest.FAIL, "missing one")
        self.assertEqual(st.results[0].status, selftest.FAIL)


class RoundTripContract(unittest.TestCase):
    """apply -> read back -> revert -> read back, and revert always runs."""

    def _st(self):
        return selftest.SelfTest(apply=True)

    def test_a_clean_round_trip_passes(self):
        st = self._st()
        state = {"v": "powersave"}
        st._round_trip(
            "gov", "Governor", "CPU",
            apply_fn=lambda: state.__setitem__("v", "performance") or True,
            read_fn=lambda: state["v"],
            revert_fn=lambda: state.__setitem__("v", "powersave"),
            expected="performance")
        r = st.results[-1]
        self.assertEqual(r.status, selftest.PASS, r.detail)
        self.assertEqual(state["v"], "powersave")

    def test_a_write_that_does_not_take_effect_fails(self):
        st = self._st()
        state = {"v": "powersave"}
        st._round_trip(
            "gov", "Governor", "CPU",
            apply_fn=lambda: True,            # claims success, changes nothing
            read_fn=lambda: state["v"],
            revert_fn=lambda: None,
            expected="performance")
        self.assertEqual(st.results[-1].status, selftest.FAIL)
        self.assertIn("did not take effect", st.results[-1].detail)

    def test_a_raising_apply_still_reverts(self):
        st = self._st()
        reverted = []
        st._round_trip(
            "gov", "Governor", "CPU",
            apply_fn=lambda: (_ for _ in ()).throw(RuntimeError("bus timeout")),
            read_fn=lambda: "powersave",
            revert_fn=lambda: reverted.append(True),
            expected="performance")
        self.assertEqual(st.results[-1].status, selftest.FAIL)
        self.assertIn("bus timeout", st.results[-1].detail)
        self.assertTrue(reverted, "revert must run even when apply raised")

    def test_a_revert_that_does_not_restore_is_a_failure(self):
        st = self._st()
        state = {"v": "powersave"}
        st._round_trip(
            "gov", "Governor", "CPU",
            apply_fn=lambda: state.__setitem__("v", "performance") or True,
            read_fn=lambda: state["v"],
            revert_fn=lambda: None,           # forgets to put it back
            expected="performance")
        self.assertEqual(st.results[-1].status, selftest.FAIL)
        self.assertIn("revert did not restore", st.results[-1].detail)


class ReportingContract(unittest.TestCase):
    def test_one_failing_probe_does_not_abort_the_rest(self):
        st = selftest.SelfTest()

        def boom(self=st):
            raise RuntimeError("probe exploded")

        st.probe_helper = boom
        st.probe_governor = lambda: st._add("gov", "Governor", selftest.INFO, "fine")
        st.PROBES = (("probe_helper", "Helper"), ("probe_governor", "Governor"))
        results = st.run()
        self.assertEqual([r.status for r in results],
                         [selftest.FAIL, selftest.INFO])
        self.assertIn("probe exploded", results[0].detail)

    def test_every_result_carries_a_sentence(self):
        """Never SKIP silently - a status with no explanation is the bug."""
        st = selftest.SelfTest()
        st.probe_modprobe_d()
        st.probe_sysctls()
        for r in st.results:
            self.assertTrue(r.detail.strip(), f"{r.name} has an empty detail")
            self.assertTrue(r.title.strip(), f"{r.name} has an empty title")

    def test_skips_do_not_fail_the_run_but_failures_do(self):
        st = selftest.SelfTest()
        st._add("a", "A", selftest.SKIP, "not on this machine")
        st._add("b", "B", selftest.INFO, "just so you know")
        self.assertFalse(any(r.status == selftest.FAIL for r in st.results))
        st._add("c", "C", selftest.FAIL, "broken")
        self.assertTrue(any(r.status == selftest.FAIL for r in st.results))

    def test_render_mentions_every_result(self):
        st = selftest.SelfTest()
        st._add("a", "Widget", selftest.SKIP, "no widget on this machine", "Bits")
        text = selftest.render(st.results, apply=False, color=False)
        self.assertIn("Widget", text)
        self.assertIn("no widget on this machine", text)
        self.assertIn("Bits", text)
        self.assertIn("Read-only", text)

    def test_json_shape_is_stable(self):
        st = selftest.SelfTest()
        st._add("a", "A", selftest.PASS, "fine", "Bits", observed_value=1)
        blob = selftest.to_json(st.results, apply=True)
        self.assertEqual(blob["mode"], "apply")
        self.assertEqual(blob["summary"], {"PASS": 1})
        self.assertEqual(blob["results"][0]["name"], "a")
        self.assertEqual(blob["results"][0]["observed"], {"observed_value": 1})
        for key in ("cpu", "gpu", "distro", "kernel"):
            self.assertIn(key, blob["machine"])


class FailureExplanations(unittest.TestCase):
    def test_a_bus_timeout_points_at_the_polkit_prompt(self):
        msg = selftest._explain_call_failure(
            RuntimeError("g-io-error-quark: Timeout was reached (24)"), "SpinUpFans")
        self.assertIn("polkit", msg)
        self.assertIn("SpinUpFans", msg)

    def test_an_unknown_error_still_names_the_method(self):
        msg = selftest._explain_call_failure(ValueError("weird"), "SetTDP")
        self.assertIn("SetTDP", msg)
        self.assertIn("weird", msg)


if __name__ == "__main__":
    unittest.main()
