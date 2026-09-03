"""The Rust and Python GPU judgements agree.

This module decides whether a frame-rate dip was a hardware fault, a scene
that is simply heavy, or the game not drawing at all. That verdict reaches the
user as a sentence and decides whether the post-game post-mortem is armed, so
the two implementations have to agree on the wording and not merely on the
conclusion.

`describe_dip` mutates the snapshot it is given - the exporter and the
Diagnostics page read the added keys back - so the state after the call is
part of what is compared.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import gpu

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_GPU_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "gpu"
        if candidate.exists():
            return candidate
    return None


#: A healthy card under load, as the baseline every case varies from.
BUSY = {
    "util_gpu": 96, "vram_used_mb": 4000, "vram_total_mb": 8192,
    "vram_free_mb": 4192, "pcie_gen": 4, "pcie_gen_max": 4,
    "pcie_width": 16, "pcie_width_max": 16, "pcie_rx_mbps": 800,
    "pstate": "P0", "clock_gfx_mhz": 1800, "clock_gfx_max_mhz": 1900,
    "event_reasons": 0,
}


def case(**over) -> dict:
    state = dict(BUSY)
    state.update(over.pop("state", {}))
    return {"state": state, "fps": 30.0, "baseline": 60.0, "cpu_load": 50.0,
            "disk_read": None, "cpu_core_max": None, "under_load": True} | over


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the gpu example is not built "
                          "- run `cargo build -p gmp-core --example gpu`")
            self.skipTest("build it with `cargo build -p gmp-core --example gpu`")

    def _rust(self, payload: dict) -> dict:
        proc = subprocess.run([str(self.binary)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _python(self, payload: dict) -> dict:
        state = dict(payload["state"])
        described = dict(state)
        detail, is_real = gpu.describe_dip(
            described, fps=payload["fps"], baseline=payload["baseline"],
            cpu_load=payload["cpu_load"], disk_read=payload["disk_read"],
            cpu_core_max=payload["cpu_core_max"])
        pm = gpu.post_mortem(state)
        return {
            "classify_dip": gpu.classify_dip(state, payload["cpu_load"], payload["disk_read"]),
            "assess": gpu.assess(state, under_load=payload["under_load"]),
            "describe_detail": detail,
            "describe_is_real": is_real,
            "state_after": described,
            "post_mortem": list(pm) if pm else None,
        }

    def _same(self, payload: dict) -> dict:
        py, rs = self._python(payload), self._rust(payload)
        self.assertEqual(py, rs)
        return py

    # ---- the verdicts ------------------------------------------------------

    def test_a_healthy_card_under_load_finds_nothing(self):
        out = self._same(case())
        self.assertEqual(out["assess"], [])

    def test_vram_near_exhaustion(self):
        out = self._same(case(state={"vram_used_mb": 7900, "vram_free_mb": 292}))
        self.assertTrue(out["describe_is_real"])
        self.assertIn("VRAM near exhaustion", out["assess"][0])

    def test_vram_pressure_short_of_exhaustion(self):
        self._same(case(state={"vram_used_mb": 7300, "vram_free_mb": 892}))

    def test_a_degraded_pcie_link_under_load(self):
        self._same(case(state={"pcie_gen": 1, "pcie_width": 4}))

    def test_a_degraded_link_on_an_idle_gpu_is_not_reported(self):
        """A down-trained link is expected when the GPU is asleep. Reporting it
        would mean a false alarm after every session."""
        out = self._same(case(state={"util_gpu": 2, "pcie_gen": 1, "pcie_width": 4},
                              cpu_load=5.0))
        self.assertEqual(out["assess"], [])

    def test_a_low_power_state_under_load(self):
        self._same(case(state={"pstate": "P8"}))

    def test_a_collapsed_core_clock(self):
        self._same(case(state={"clock_gfx_mhz": 800}))

    def test_the_clock_event_bitmask(self):
        for reasons in (0, 0x8, 0x40, 0x80, 0x48, 0xC8, 0x1):
            with self.subTest(event_reasons=reasons):
                self._same(case(state={"event_reasons": reasons}))

    # ---- withheld vs starved ----------------------------------------------

    def test_an_idle_system_reads_as_withheld_not_starved(self):
        out = self._same(case(state={"util_gpu": 3}, cpu_load=10.0, fps=0.0, baseline=0.0))
        self.assertFalse(out["describe_is_real"])
        self.assertIn("withheld", out["describe_detail"])

    def test_a_loading_screen_is_named_by_the_disk(self):
        out = self._same(case(state={"util_gpu": 3}, cpu_load=10.0, disk_read=180.4))
        self.assertIn("180 MB/s", out["classify_dip"])

    def test_disk_read_just_under_the_threshold(self):
        self._same(case(state={"util_gpu": 3}, cpu_load=10.0, disk_read=25.0))

    # ---- heavy scenes, not faults -----------------------------------------

    def test_a_gpu_bound_scene(self):
        out = self._same(case(state={"util_gpu": 99}, fps=40.0, baseline=60.0))
        self.assertFalse(out["describe_is_real"])
        self.assertEqual(out["state_after"]["assessment"], "GPU-bound scene")

    def test_a_cpu_bound_scene(self):
        out = self._same(case(state={"util_gpu": 55}, fps=40.0, baseline=60.0,
                              cpu_core_max=99.0))
        self.assertFalse(out["describe_is_real"])
        self.assertEqual(out["state_after"]["assessment"], "CPU-bound scene")

    def test_a_small_drop_is_not_a_heavy_scene(self):
        """The 0.75 threshold: a dip has to be a real drop before the
        GPU-bound explanation applies."""
        self._same(case(state={"util_gpu": 99}, fps=50.0, baseline=60.0))

    def test_an_unclassified_dip_stays_real(self):
        out = self._same(case(state={"util_gpu": 60}, fps=40.0, baseline=60.0))
        self.assertTrue(out["describe_is_real"])

    # ---- awkward inputs ---------------------------------------------------

    def test_an_empty_snapshot(self):
        self._same({"state": {}, "fps": 10.0, "baseline": 60.0, "cpu_load": None,
                    "disk_read": None, "cpu_core_max": None, "under_load": True})

    def test_missing_fields_are_not_zeros(self):
        """`if used and total` treats 0 as absent, so a zeroed field must not
        look like a real reading."""
        self._same(case(state={"vram_used_mb": 0, "vram_total_mb": 0,
                               "pcie_gen": 0, "pcie_width": 0}))

    def test_rounded_at_dip_values(self):
        """cpu_load and cpu_core_max go through round(x, 1)."""
        for load in (51.15, 66.75, 48.05):
            with self.subTest(cpu_load=load):
                self._same(case(cpu_load=load, cpu_core_max=load))

    def test_post_mortem_flags_unreleased_vram(self):
        out = self._same(case(state={"vram_used_mb": 1200}))
        self.assertEqual(out["post_mortem"][0], "vram_not_freed")

    def test_post_mortem_is_quiet_after_a_clean_exit(self):
        out = self._same(case(state={"vram_used_mb": 400}))
        self.assertIsNone(out["post_mortem"])

    # ---- every threshold, from both sides ---------------------------------

    def test_each_threshold_is_straddled(self):
        """Cases in the MIDDLE of a range prove almost nothing.

        The first version of this file put every case comfortably inside its
        band, and a mutation check showed the consequence: moving
        `frac >= 0.94` to 0.95, `util >= 92` to 93 and `disk > 25` to 26 all
        left the suite green. A threshold is only pinned by inputs either side
        of it, and these are generated so that adding a rule means adding a
        pair here rather than remembering to.
        """
        total = 8192
        cases: list[tuple[str, dict]] = []

        # VRAM exhaustion at frac >= 0.94 and pressure at >= 0.88. The used
        # value has to be picked so the ACTUAL fraction straddles the
        # threshold - round(total * 0.94) is 7700, whose fraction is 0.93994,
        # which is below it. Free is held high so the other branch cannot fire
        # and mask this one.
        import math
        for threshold in (0.88, 0.94):
            exact = math.ceil(total * threshold)
            for used in (exact - 1, exact):
                frac = used / total
                assert (frac >= threshold) == (used == exact), (used, frac)
                cases.append((f"vram frac {used}/{total}={frac:.5f}",
                              case(state={"vram_used_mb": used,
                                          "vram_free_mb": 4000})))
        # free < 300, with the fraction held well below 0.88 so only the free
        # branch can decide.
        for free in (299, 300, 301):
            cases.append((f"vram free {free}",
                          case(state={"vram_used_mb": 5000, "vram_free_mb": free})))

        # busy: util >= 25, and the checks gated on util > 50
        for util in (24, 25, 26, 50, 51):
            cases.append((f"util {util}",
                          case(state={"util_gpu": util, "pcie_gen": 1,
                                      "pstate": "P8", "clock_gfx_mhz": 800})))

        # withheld: util < 15 and cpu_load < 45
        for util in (14, 15):
            for load in (44.0, 45.0):
                cases.append((f"idle util {util} load {load}",
                              case(state={"util_gpu": util}, cpu_load=load)))

        # loading screen: disk > 25
        for disk in (24.9, 25.0, 25.1):
            cases.append((f"disk {disk}",
                          case(state={"util_gpu": 3}, cpu_load=10.0, disk_read=disk)))

        # PCIe: rx > 500, width <= 4
        for rx in (500, 501):
            cases.append((f"rx {rx}",
                          case(state={"pcie_gen": 1, "pcie_rx_mbps": rx})))
        for width in (4, 5):
            cases.append((f"width {width}",
                          case(state={"pcie_width": width, "pcie_rx_mbps": 800})))

        # clock collapse: cg < cgm * 0.55
        for clock in (1044, 1045, 1046):   # 1900 * 0.55 = 1045
            cases.append((f"clock {clock}",
                          case(state={"clock_gfx_mhz": clock, "util_gpu": 60})))

        # heavy scene: fps <= baseline * 0.75, util >= 92, util < 80, core >= 95
        for fps in (44.9, 45.0, 45.1):     # baseline 60 -> 0.75 is 45
            cases.append((f"fps {fps}", case(fps=fps, state={"util_gpu": 99})))
        for util in (91, 92):
            cases.append((f"gpu-bound util {util}",
                          case(fps=40.0, state={"util_gpu": util})))
        for util in (79, 80):
            for core in (94.0, 95.0):
                cases.append((f"cpu-bound util {util} core {core}",
                              case(fps=40.0, state={"util_gpu": util},
                                   cpu_core_max=core)))

        # post-mortem: used > 900
        for used in (900, 901):
            cases.append((f"post-mortem {used}",
                          case(state={"vram_used_mb": used})))

        for name, payload in cases:
            with self.subTest(boundary=name):
                self._same(payload)


if __name__ == "__main__":
    unittest.main()
