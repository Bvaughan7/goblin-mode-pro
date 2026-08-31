import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC  # noqa: F401

from goblinmode import diagnostics


class _RestoreGlobals(unittest.TestCase):
    def setUp(self):
        self._saved = (diagnostics._HWMON, diagnostics._POWERCAP)
        self.addCleanup(self._restore)

    def _restore(self):
        diagnostics._HWMON, diagnostics._POWERCAP = self._saved


class HwmonResolution(_RestoreGlobals):
    def _hwmon(self, root: Path, idx: int, name: str, labels: dict[str, str]):
        d = root / f"hwmon{idx}"
        d.mkdir()
        (d / "name").write_text(name + "\n")
        for temp, label in labels.items():
            (d / f"{temp}_label").write_text(label + "\n")
            (d / f"{temp}_input").write_text("45000\n")
        if "temp1" not in {t for t in labels}:
            (d / "temp1_input").write_text("45000\n")
        return d

    def test_prefers_coretemp_package_label(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            diagnostics._HWMON = root
            self._hwmon(root, 3, "acpitz", {})
            self._hwmon(root, 6, "coretemp", {"temp1": "Package id 0", "temp2": "Core 0"})
            got = diagnostics._resolve_cpu_temp_input()
            self.assertEqual(got, root / "hwmon6" / "temp1_input")

    def test_amd_k10temp_tctl(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            diagnostics._HWMON = root
            self._hwmon(root, 2, "k10temp", {"temp1": "Tctl", "temp2": "Tccd1"})
            got = diagnostics._resolve_cpu_temp_input()
            self.assertEqual(got, root / "hwmon2" / "temp1_input")

    def test_none_when_no_cpu_hwmon(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            diagnostics._HWMON = root
            self._hwmon(root, 0, "nvme", {})
            self.assertIsNone(diagnostics._resolve_cpu_temp_input())


class RaplZone(_RestoreGlobals):
    def test_resolves_package_zone_by_name(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            diagnostics._POWERCAP = root
            (root / "intel-rapl:0").mkdir()
            (root / "intel-rapl:0" / "name").write_text("package-0\n")
            (root / "intel-rapl:1").mkdir()
            (root / "intel-rapl:1" / "name").write_text("psys\n")
            self.assertEqual(diagnostics._resolve_rapl_zone(), root / "intel-rapl:0")

    def test_none_without_powercap(self):
        with TemporaryDirectory() as td:
            diagnostics._POWERCAP = Path(td)
            self.assertIsNone(diagnostics._resolve_rapl_zone())


def _sample(t: float, throttled: bool, temp: float = 95.0) -> "diagnostics.Sample":
    return diagnostics.Sample(
        t=t, cpu_temp=temp, cpu_load=80.0, per_core=[80.0],
        pkg_power_w=None, pl1_w=None, pl2_w=None,
        gpu_load=10.0, gpu_temp=60.0, gpu_throttle_reasons="0x0",
        cpu_throttled=throttled,
    )


class ThrottleAssessment(unittest.TestCase):
    def _engine(self) -> "diagnostics.DiagnosticEngine":
        return diagnostics.DiagnosticEngine(
            sample_interval=1.0, gpu_probe=lambda: (10.0, 60.0, "0x0")
        )

    def _fires(self, eng, span, throttled) -> list[float]:
        out = []
        for t in span:
            res = eng.assess(_sample(t, throttled(t) if callable(throttled) else throttled))
            if res:
                out.append((t, res))
        return out

    def test_isolated_throttle_tick_is_not_an_incident(self):
        eng = self._engine()
        self.assertEqual(self._fires(eng, range(0, 15), lambda t: t == 3), [])

    def test_sustained_throttle_fires_once_then_dedupes(self):
        eng = self._engine()
        fires = self._fires(eng, range(0, 1000), True)
        # onset at the 5th hit; then reminded no sooner than 900 s later
        self.assertEqual([t for t, _ in fires], [4, 904])
        self.assertEqual(fires[0][1][0], "thermal_throttle")
        self.assertIn("throttle events", fires[0][1][1])

    def test_short_gap_does_not_re_trigger(self):
        eng = self._engine()
        fires = []
        fires += self._fires(eng, range(0, 21), True)
        fires += self._fires(eng, range(21, 51), False)   # 30 s < grace
        fires += self._fires(eng, range(51, 90), True)
        self.assertEqual([t for t, _ in fires], [4])

    def test_gap_past_grace_is_a_fresh_onset(self):
        eng = self._engine()
        fires = []
        fires += self._fires(eng, range(0, 21), True)
        fires += self._fires(eng, range(21, 140), False)  # > 90 s grace
        fires += self._fires(eng, range(140, 170), True)
        self.assertEqual([t for t, _ in fires], [4, 144])


class EngineWithoutGpu(unittest.TestCase):
    def test_sample_uses_injected_probe_not_nvidia_smi(self):
        seen = []

        def probe():
            seen.append(1)
            return 33.0, 60.0, "0x0"

        eng = diagnostics.DiagnosticEngine(sample_interval=1.0, gpu_probe=probe)
        s = eng.sample()
        self.assertEqual(seen, [1])
        self.assertEqual(s.gpu_load, 33.0)
        self.assertIn("cpu_load", s.as_dict())


if __name__ == "__main__":
    unittest.main()
