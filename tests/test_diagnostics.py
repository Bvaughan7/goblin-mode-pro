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
