import io
import unittest
from contextlib import redirect_stdout

from tests._support import _SRC  # noqa: F401

from goblinmode import cli


class FakeBridge:
    available = True

    def get_status(self):
        return {"master_enabled": True, "active_games": ["Wow.exe"],
                "governor": "performance", "tweaks": {"governor": "performance"},
                "helper_available": True,
                "capabilities": {"cpu_model": "Test CPU", "gpu_vendors": ["nvidia"],
                                 "kernel_release": "7.2.0"},
                "profiles": [{"exe": "Wow.exe", "display_name": "WoW",
                              "enabled": True, "match_mode": "exact"}]}

    def get_health(self):
        return {"score": 8.5, "counts": {"ok": 11, "warn": 1, "fail": 0}, "worst": []}

    def get_sessions(self):
        return [{"started": "2026-08-28T20:00", "game": "WoW", "fps_avg": 140.0,
                 "fps_1low": 80.0}]

    def get_session_history(self, g):
        return self.get_sessions()

    def force_boost(self, on):
        self.boosted = on
        return True

    def arm_benchmark(self, exe):
        self.armed = exe
        return True

    def run_preflight(self):
        return [{"title": "vm.max_map_count", "value": "2147483642", "status": "ok"}]

    def apply_preflight_fixes(self):
        return {"applied": ["vm.swappiness=10"], "failed": []}

    def build_report(self, note):
        return "# report"

    def export_setup(self):
        return "# setup"


class CliDispatch(unittest.TestCase):
    def setUp(self):
        self._fake = FakeBridge()
        cli._connect = lambda: self._fake  # noqa

    def _run(self, *args) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(args))
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_status(self):
        out = self._run("status")
        self.assertIn("performance", out)
        self.assertIn("Wow.exe", out)

    def test_status_json(self):
        out = self._run("status", "--json")
        self.assertIn('"governor"', out)

    def test_health(self):
        self.assertIn("8.5", self._run("health"))

    def test_boost_unboost(self):
        self._run("boost")
        self.assertTrue(self._fake.boosted)
        self._run("unboost")
        self.assertFalse(self._fake.boosted)

    def test_benchmark(self):
        self._run("benchmark", "Wow.exe")
        self.assertEqual(self._fake.armed, "Wow.exe")

    def test_preflight_fix(self):
        out = self._run("preflight", "--fix")
        self.assertIn("vm.max_map_count", out)
        self.assertIn("vm.swappiness=10", out)

    def test_sessions_and_games_and_setup(self):
        self.assertIn("WoW", self._run("sessions"))
        self.assertIn("WoW", self._run("games"))
        self.assertIn("setup", self._run("setup"))


if __name__ == "__main__":
    unittest.main()
