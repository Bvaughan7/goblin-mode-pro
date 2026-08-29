import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import capabilities


class CpuListParsing(unittest.TestCase):
    def test_ranges_and_singletons(self):
        self.assertEqual(capabilities._parse_cpu_list("0-3,8,10-11"),
                         [0, 1, 2, 3, 8, 10, 11])

    def test_empty_and_whitespace(self):
        self.assertEqual(capabilities._parse_cpu_list(""), [])
        self.assertEqual(capabilities._parse_cpu_list(" 2 , 4 "), [2, 4])

    def test_garbage_is_skipped(self):
        self.assertEqual(capabilities._parse_cpu_list("0,foo,2-x,3"), [0, 3])


class DetectShape(unittest.TestCase):
    def test_detect_has_the_documented_keys(self):
        caps = capabilities.detect()
        for key in ("cpu_vendor", "cpufreq_driver", "governor_control",
                    "epp_control", "rapl_control", "tdp_control", "gpu_vendors",
                    "compositor", "core_layout"):
            self.assertIn(key, caps)
        self.assertIn("online", caps["core_layout"])
        self.assertIsInstance(caps["core_layout"]["online"], list)

    def test_tdp_control_is_consistent_with_rapl(self):
        caps = capabilities.detect()
        if caps["rapl_control"]:
            self.assertEqual(caps["tdp_control"], "rapl")


if __name__ == "__main__":
    unittest.main()
