import os
import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import cpuset

_LAYOUT = {
    "online": list(range(16)),
    "performance": [0, 1, 2, 3, 4, 5, 6, 7],
    "cache_groups": [[0, 1, 2, 3, 4, 5, 6, 7], [8, 9, 10, 11, 12, 13, 14, 15]],
}


class TargetCpus(unittest.TestCase):
    def test_performance_mode(self):
        self.assertEqual(cpuset.target_cpus("performance", _LAYOUT), _LAYOUT["performance"])

    def test_cache0_mode(self):
        self.assertEqual(cpuset.target_cpus("cache0", _LAYOUT), _LAYOUT["cache_groups"][0])

    def test_off_and_missing_data(self):
        self.assertIsNone(cpuset.target_cpus("off", _LAYOUT))
        self.assertIsNone(cpuset.target_cpus("performance", {"online": [0, 1]}))
        self.assertIsNone(cpuset.target_cpus("cache0", {"online": [0, 1]}))


@unittest.skipUnless(hasattr(os, "sched_setaffinity"), "needs sched_setaffinity")
class PinRoundTrip(unittest.TestCase):
    def test_pin_and_restore_current_process(self):
        pid = os.getpid()
        original = cpuset.current_affinity(pid)
        self.assertIsNotNone(original)
        if len(original) < 2:
            self.skipTest("only one CPU available")
        self.assertTrue(cpuset.pin(pid, original[:1]))
        self.assertEqual(cpuset.current_affinity(pid), original[:1])
        cpuset.restore(pid, original)
        self.assertEqual(cpuset.current_affinity(pid), original)


if __name__ == "__main__":
    unittest.main()
