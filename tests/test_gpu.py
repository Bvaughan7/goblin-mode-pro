import time
import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import gpu


class Assess(unittest.TestCase):
    def test_vram_exhaustion_flagged(self):
        state = {"util_gpu": 90, "vram_used_mb": 5900, "vram_total_mb": 6000, "vram_free_mb": 100}
        causes = gpu.assess(state, under_load=True)
        self.assertTrue(any("VRAM near exhaustion" in c for c in causes))

    def test_idle_gpu_never_flags_pcie_or_pstate(self):
        state = {"util_gpu": 2, "pcie_gen": 1, "pcie_gen_max": 4, "pstate": "P8"}
        self.assertEqual(gpu.assess(state, under_load=False), [])
        self.assertEqual(gpu.assess(state, under_load=True), [])  # util gates it too

    def test_empty_state_returns_empty(self):
        self.assertEqual(gpu.assess({}), [])


class ClassifyDip(unittest.TestCase):
    def test_idle_cpu_and_gpu_is_withheld_not_starved(self):
        note = gpu.classify_dip({"util_gpu": 3}, cpu_load=10, disk_read_mbps=2)
        self.assertIsNotNone(note)
        self.assertIn("withheld", note)

    def test_busy_gpu_returns_none(self):
        self.assertIsNone(gpu.classify_dip({"util_gpu": 80}, cpu_load=70, disk_read_mbps=0))


class PostMortem(unittest.TestCase):
    def test_flags_unreleased_vram(self):
        v = gpu.post_mortem({"vram_used_mb": 1500})
        self.assertIsNotNone(v)
        self.assertEqual(v[0], "vram_not_freed")

    def test_clean_exit_returns_none(self):
        self.assertIsNone(gpu.post_mortem({"vram_used_mb": 120}))


class Monitor(unittest.TestCase):
    def test_polls_on_a_thread_and_stops_cleanly(self):
        calls = {"light": 0, "deep": 0}

        def fake_light():
            calls["light"] += 1
            return 42.0, 55.0, "0x0"

        def fake_deep():
            calls["deep"] += 1
            return {"vram_used_mb": 100}

        orig_l, orig_d, orig_a = gpu.light_state, gpu.deep_state, gpu.available
        gpu.light_state, gpu.deep_state, gpu.available = fake_light, fake_deep, lambda: True
        try:
            m = gpu.GpuMonitor()
            m._IDLE = (0.05, 0.1)          # speed the test up
            m._ACTIVE = (0.05, 0.05)
            m.start()
            time.sleep(0.3)
            m.set_active(True)
            time.sleep(0.2)
            m.stop()
            time.sleep(0.2)
            self.assertGreater(calls["light"], 2)
            self.assertGreaterEqual(calls["deep"], 1)
            self.assertEqual(m.light()[0], 42.0)
            self.assertEqual(m.deep()["vram_used_mb"], 100)
            self.assertFalse(m._thread.is_alive())
        finally:
            gpu.light_state, gpu.deep_state, gpu.available = orig_l, orig_d, orig_a

    def test_no_nvidia_no_thread(self):
        orig = gpu.available
        gpu.available = lambda: False
        try:
            m = gpu.GpuMonitor()
            m.start()
            self.assertIsNone(m._thread)
            self.assertEqual(m.light(), (None, None, ""))
            self.assertEqual(m.deep(), {})
        finally:
            gpu.available = orig


if __name__ == "__main__":
    unittest.main()
