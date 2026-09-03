import unittest
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import preflight


class RunAll(unittest.TestCase):
    def test_every_check_yields_the_expected_shape(self):
        results = preflight.run_all()
        self.assertGreater(len(results), 8)
        ids = {r["id"] for r in results}
        for expected in ("max_map_count", "userns", "anticheat", "vulkan_icd"):
            self.assertIn(expected, ids)
        required = {"id", "title", "status", "value", "detail", "sysctl",
                    "kernel_param", "fix_hint"}
        for r in results:
            self.assertTrue(required.issubset(r), f"{r['id']} missing keys")

    def test_status_is_a_known_value(self):
        allowed = {preflight.OK, preflight.WARN, preflight.FAIL,
                   preflight.INFO, preflight.UNKNOWN}
        for r in preflight.run_all():
            self.assertIn(r["status"], allowed)

    def test_summary_counts_sum_to_total(self):
        results = preflight.run_all()
        counts = preflight.summary(results)
        self.assertEqual(sum(counts.values()), len(results))

    def test_pending_sysctls_are_pairs_from_the_allowlist(self):
        for key, value in preflight.pending_sysctls():
            self.assertIsInstance(key, str)
            self.assertRegex(value, r"^-?\d+$")

    def test_dropin_text_is_valid_sysctl_syntax(self):
        text = preflight.sysctl_dropin_text([
            {"id": "x", "status": "fail", "sysctl": ["vm.max_map_count", "2147483642"],
             "kernel_param": None, "fix_hint": ""},
        ])
        self.assertIn("vm.max_map_count = 2147483642", text)


if __name__ == "__main__":
    unittest.main()


class ThresholdsAreTheSpecification(unittest.TestCase):
    """Each number here was chosen against a real failure mode, and the
    `detail` string beside it says which. A threshold that drifts turns a check
    into decoration, so they are pinned either side of the boundary rather than
    only in the middle.
    """

    def _read_int(self, mapping):
        return patch.object(preflight, "_read_int", lambda p: mapping.get(p))

    def _read(self, mapping):
        return patch.object(preflight, "_read", lambda p: mapping.get(p))

    def test_max_map_count_fails_below_the_unreal_engine_threshold(self):
        path = "/proc/sys/vm/max_map_count"
        with self._read_int({path: 1_048_576}):
            self.assertEqual(preflight._c_max_map_count().status, preflight.OK)
        with self._read_int({path: 1_048_575}):
            self.assertEqual(preflight._c_max_map_count().status, preflight.FAIL)

    def test_a_missing_max_map_count_is_treated_as_zero_and_fails(self):
        """Absent means the kernel cannot do it, which is a failure for the
        titles this check exists for - not an UNKNOWN to be ignored."""
        with self._read_int({}):
            self.assertEqual(preflight._c_max_map_count().status, preflight.FAIL)

    def test_the_esync_file_descriptor_limit(self):
        with patch.object(preflight, "_nofile_hard", lambda: 524_288):
            self.assertEqual(preflight._c_nofile().status, preflight.OK)
        with patch.object(preflight, "_nofile_hard", lambda: 524_287):
            self.assertEqual(preflight._c_nofile().status, preflight.WARN)

    def test_an_unreadable_limit_is_unknown_rather_than_a_failure(self):
        """Reporting FAIL for something that could not be measured sends the
        user chasing a problem that may not exist."""
        with patch.object(preflight, "_nofile_hard", lambda: None):
            self.assertEqual(preflight._c_nofile().status, preflight.UNKNOWN)

    def test_split_lock_mitigation(self):
        path = "/proc/sys/kernel/split_lock_mitigate"
        with self._read({path: "0"}):
            r = preflight._c_split_lock()
            self.assertEqual((r.status, r.value), (preflight.OK, "off"))
        with self._read({path: "1"}):
            self.assertEqual(preflight._c_split_lock().status, preflight.WARN)

    def test_a_kernel_without_the_split_lock_knob_is_informational(self):
        """Most kernels do not expose it. That is not a problem to fix."""
        with self._read({}):
            r = preflight._c_split_lock()
            self.assertEqual((r.status, r.value), (preflight.INFO, "n/a"))

    def test_compaction_proactiveness(self):
        path = "/proc/sys/vm/compaction_proactiveness"
        with self._read_int({path: 5}):
            self.assertEqual(preflight._c_compaction().status, preflight.OK)
        with self._read_int({path: 6}):
            self.assertEqual(preflight._c_compaction().status, preflight.WARN)

    def test_swappiness_is_advice_not_a_warning(self):
        """Above the threshold this is INFO, not WARN: a high swappiness is a
        defensible choice on a machine with little RAM."""
        path = "/proc/sys/vm/swappiness"
        with self._read_int({path: 20}):
            self.assertEqual(preflight._c_swappiness().status, preflight.OK)
        with self._read_int({path: 60}):
            self.assertEqual(preflight._c_swappiness().status, preflight.INFO)

    def test_fsync_needs_futex_waitv(self):
        with patch.object(preflight, "_kernel_ver", lambda: (5, 16)):
            self.assertEqual(preflight._c_fsync().status, preflight.OK)
        with patch.object(preflight, "_kernel_ver", lambda: (5, 15)):
            self.assertEqual(preflight._c_fsync().status, preflight.WARN)
        with patch.object(preflight, "_kernel_ver", lambda: (6, 0)):
            self.assertEqual(preflight._c_fsync().status, preflight.OK)


class ABrokenCheckDoesNotBreakThePreflight(unittest.TestCase):
    """`run_all` walks every check. One raising must cost that row, not the
    whole report - the user would otherwise see nothing at all because of a
    single unreadable file."""

    def test_a_raising_check_becomes_unknown(self):
        def boom() -> preflight.CheckResult:
            raise RuntimeError("procfs went away")

        chk = preflight.Check(id="x", title="x", why="x", _run=boom)
        result = chk.run()
        self.assertEqual(result.status, preflight.UNKNOWN)
        self.assertIn("procfs went away", result.detail)

    def test_run_all_still_returns_a_row_per_check(self):
        self.assertEqual(len(preflight.run_all()), len(preflight.CHECKS))


class SeverityCaps(unittest.TestCase):
    """A check declares the worst it is allowed to report. `run_all` caps its
    result at that, so a check that is merely advisory cannot shout FAIL."""

    def test_a_fail_is_capped_to_the_declared_severity(self):
        chk = preflight.Check(
            id="x", title="x", why="x",
            _run=lambda: preflight.CheckResult(preflight.FAIL, "v"),
            severity=preflight.WARN,
        )
        with patch.object(preflight, "CHECKS", [chk]):
            self.assertEqual(preflight.run_all()[0]["status"], preflight.WARN)

    def test_a_warn_is_capped_to_info(self):
        chk = preflight.Check(
            id="x", title="x", why="x",
            _run=lambda: preflight.CheckResult(preflight.WARN, "v"),
            severity=preflight.INFO,
        )
        with patch.object(preflight, "CHECKS", [chk]):
            self.assertEqual(preflight.run_all()[0]["status"], preflight.INFO)

    def test_an_ok_is_never_promoted(self):
        chk = preflight.Check(
            id="x", title="x", why="x",
            _run=lambda: preflight.CheckResult(preflight.OK, "v"),
            severity=preflight.FAIL,
        )
        with patch.object(preflight, "CHECKS", [chk]):
            self.assertEqual(preflight.run_all()[0]["status"], preflight.OK)


class ProposedFixes(unittest.TestCase):
    """What preflight asks the privileged helper to write."""

    def test_only_failing_checks_propose_a_sysctl(self):
        rows = [
            {"sysctl": ["vm.swappiness", "10"], "status": preflight.OK},
            {"sysctl": ["vm.max_map_count", "2147483642"], "status": preflight.FAIL},
            {"sysctl": ["vm.compaction_proactiveness", "0"], "status": preflight.WARN},
            {"sysctl": None, "status": preflight.FAIL},
        ]
        self.assertEqual(
            preflight.pending_sysctls(rows),
            [("vm.max_map_count", "2147483642"),
             ("vm.compaction_proactiveness", "0")],
        )

    def test_every_proposable_key_is_one_the_helper_will_accept(self):
        """THE BOUNDARY. preflight proposes; the helper decides. A key the
        helper's allowlist does not carry is a fix that silently never applies,
        and the user is told their machine was tuned when it was not.
        """
        import importlib.util
        import sys as _sys
        from pathlib import Path as _Path

        repo = _Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "goblin_helper_pf", repo / "helper" / "goblin_helper.py")
        helper = importlib.util.module_from_spec(spec)
        _sys.modules["goblin_helper_pf"] = helper
        spec.loader.exec_module(helper)

        for chk in preflight.CHECKS:
            if chk.sysctl:
                key, value = chk.sysctl
                with self.subTest(check=chk.id, key=key):
                    self.assertIn(key, helper.SYSCTL_ALLOW,
                                  f"{chk.id} proposes {key}, which the helper refuses")
                    low, high = helper.SYSCTL_ALLOW[key]
                    self.assertTrue(
                        low <= int(value) <= high,
                        f"{chk.id} proposes {key}={value}, outside the helper's "
                        f"accepted range ({low}, {high})")

    def test_the_dropin_lists_only_pending_fixes(self):
        rows = [
            {"sysctl": ["vm.swappiness", "10"], "status": preflight.OK},
            {"sysctl": ["vm.max_map_count", "2147483642"], "status": preflight.FAIL},
        ]
        text = preflight.sysctl_dropin_text(rows)
        self.assertIn("vm.max_map_count = 2147483642", text)
        self.assertNotIn("vm.swappiness", text)
        self.assertTrue(text.endswith("\n"))
