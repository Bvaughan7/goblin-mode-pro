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

    def build_works_for_me(self, exe, note):
        self.works_for_me_call = (exe, note)
        return {"markdown": f"# works for me: {exe}", "url": "https://example.invalid/issues/new"}

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

    def test_gamescope_session_missing_binary(self):
        from unittest.mock import patch

        with patch("shutil.which", return_value=None):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["gamescope-session"])
        self.assertEqual(rc, 1)
        self.assertIn("not installed", buf.getvalue())

    def test_gamescope_session_unknown_game(self):
        from unittest.mock import patch

        with patch("shutil.which", return_value="/usr/bin/gamescope"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["gamescope-session", "--game", "NoSuchGame"])
        self.assertEqual(rc, 1)
        self.assertIn("no profile matches", buf.getvalue())

    def test_gamescope_session_execs_with_resolved_profile(self):
        from unittest.mock import patch

        with patch("shutil.which", return_value="/usr/bin/gamescope"), \
             patch("os.execvp") as execvp:
            cli.main(["gamescope-session", "--game", "Wow.exe"])
        self.assertEqual(execvp.call_args[0][0], "gamescope")
        self.assertIn("gamescope", execvp.call_args[0][1])

    def test_works_for_me_prints_markdown_and_url(self):
        out = self._run("works-for-me", "Wow.exe", "--note", "smooth as butter")
        self.assertIn("works for me: Wow.exe", out)
        self.assertIn("https://example.invalid/issues/new", out)
        self.assertEqual(self._fake.works_for_me_call, ("Wow.exe", "smooth as butter"))

    def test_compare_needs_two_sessions(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["compare", "Wow.exe"])
        self.assertEqual(rc, 1)
        self.assertIn("need at least two", buf.getvalue())

    def test_compare_shows_the_diff(self):
        self._fake.get_session_history = lambda g: [
            {"started": "2026-08-20T10:00", "fps_avg": 60.0, "fps_1low": 40.0},
            {"started": "2026-08-27T10:00", "fps_avg": 75.0, "fps_1low": 40.0},
        ]
        out = self._run("compare", "Wow.exe")
        self.assertIn("Average FPS", out)
        self.assertIn("+25.0%", out)

    def test_gamescope_session_default_launches_steam(self):
        from unittest.mock import patch

        with patch("shutil.which", return_value="/usr/bin/gamescope"), \
             patch("os.execvp") as execvp:
            cli.main(["gamescope-session"])
        argv = execvp.call_args[0][1]
        self.assertEqual(argv[argv.index("--") + 1:], ["steam", "-tenfoot"])


if __name__ == "__main__":
    unittest.main()
