import unittest

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
